"""`review()` chooses what an incremental pass covers and records it (GH-589, ADR-0089): the
resolution of a conflicted merge of master, and the commits after it, when that is all that
moved; the whole range otherwise. Seam: `application.review.review` through the fakes of
`test_review_landing.py`, and `application.preflight.preflight` for the git-version warning."""

import pytest

from agentic.core.process.application import review_scope
from agentic.core.process.application.review import review
from agentic.core.process.domain import landing as landing_rules
from agentic.core.process.domain.rules import Commit
from agentic.core.process.domain.task import INTEGRATION
from agentic.shared.configuration import Configuration
from tests.fakes import (
    MemoryStack,
    MemoryWorkspace,
    ScriptedRunner,
    memory_ports,
    reviewed,
)

pytestmark = pytest.mark.anyio

HEAD = "base"  # what the fake workspace answers for `full_head`
TIP = "r" * 40
MASTER = "m" * 40
MERGE = "1" * 40
LATER = "2" * 40
OWN = "3" * 40
BROUGHT = "4" * 40
HUNK = "diff --git a/docs/adr/INDEX.md b/docs/adr/INDEX.md\n+| 0089 | resolved row |"
LATER_DIFF = "diff --git a/src/late.py b/src/late.py\n+late = True"
CLEAN_LINE = "master brought this line cleanly"


def unit(**extra):
    return {
        "id": "api",
        "title": "api",
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": "delivered",
        "branch": "agentic/GH-589.api",
        "base": "base1",
        "reviewed_head": TIP,
        **extra,
    }


def state(**extra):
    return {
        "id": "GH-589",
        "title": "T",
        "description": "d",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nx"},
        "subtask_id": "api",
        "subtasks": {"api": unit()},
        "landing": [{"step": "sync_master", "upstream": MASTER}],
        **extra,
    }


def scenario(
    line,
    *,
    ancestor=True,
    version=(2, 43),
    resolutions=None,
    errors=None,
    st=None,
):
    workspace = MemoryWorkspace()
    workspace.first_parent_line = tuple(line)
    workspace.ancestors_by_ref = {HEAD: {TIP} if ancestor else set(), MASTER: {BROUGHT}}
    workspace.git_version_answer = version
    workspace.merge_resolutions = resolutions or {}
    workspace.merge_resolution_errors = errors or {}
    runner = ScriptedRunner(tech_lead_review=[reviewed()])
    ports = memory_ports(runner, workspace=workspace, stack=MemoryStack())
    return ports, runner, st or state()


async def run(ports, runner, st):
    patch = await review(st, Configuration(product_dir=MemoryWorkspace.ROOT), ports)
    return patch, runner.calls[0]


def recorded(patch):
    [entry] = patch["review_scopes"]
    return entry["scope"], entry["cause"]


async def test_a_resolved_merge_and_a_later_commit_reach_the_reviewer_as_the_only_text():
    line = [Commit(MERGE, (TIP, BROUGHT)), Commit(LATER, (MERGE,))]
    ports, runner, st = scenario(
        line, resolutions={MERGE: HUNK, LATER: LATER_DIFF, OWN: CLEAN_LINE}
    )

    patch, call = await run(ports, runner, st)

    assert recorded(patch) == ("resolution", "resolved")
    assert review_scope.RESOLUTION_SECTION not in patch["sections"]
    assert HUNK in call["prompt"] and LATER_DIFF in call["prompt"]
    assert CLEAN_LINE not in call["prompt"]
    assert f"## {review_scope.RESOLUTION_SECTION}" in call["prompt"]
    assert patch["subtasks"]["api"]["reviewed_head"] == HEAD
    assert patch["review_scopes"][0]["base"] == TIP


async def test_a_branch_whose_own_commits_were_rewritten_reviews_in_full():
    line = [Commit(MERGE, (TIP, BROUGHT))]
    ports, runner, st = scenario(line, ancestor=False, resolutions={MERGE: HUNK})

    patch, call = await run(ports, runner, st)

    assert recorded(patch) == ("full", "rewritten")
    assert review_scope.RESOLUTION_SECTION not in call["prompt"]
    assert HUNK not in call["prompt"] and ports.workspace.merge_resolution_asks == []


async def test_an_own_commit_before_the_merge_reviews_in_full():
    line = [Commit(OWN, (TIP,)), Commit(MERGE, (OWN, BROUGHT))]
    ports, runner, st = scenario(line, resolutions={MERGE: HUNK})

    patch, call = await run(ports, runner, st)

    assert recorded(patch) == ("full", "own_before_merge")
    assert HUNK not in call["prompt"]


async def test_a_merge_of_something_else_than_the_upstream_reviews_in_full():
    line = [Commit(MERGE, (TIP, BROUGHT)), Commit(LATER, (MERGE, OWN))]
    ports, runner, st = scenario(line, resolutions={MERGE: HUNK})

    patch, _ = await run(ports, runner, st)

    assert recorded(patch) == ("full", "foreign_merge")


async def test_a_review_with_no_merge_renders_what_a_rewritten_one_renders():
    """Both are today's call: the same base and no section, byte for byte."""
    ports, runner, st = scenario([Commit(OWN, (TIP,))])
    patch, plain = await run(ports, runner, st)
    ports, runner, st = scenario([Commit(MERGE, (TIP, BROUGHT))], ancestor=False)
    _, rewritten = await run(ports, runner, st)

    assert recorded(patch) == ("full", "no_merge")
    assert plain["prompt"] == rewritten["prompt"]
    assert plain["system_prompt"] == rewritten["system_prompt"]
    assert review_scope.RESOLUTION_SECTION not in plain["prompt"]
    assert TIP in plain["system_prompt"]
    assert patch["review_scopes"][0]["base"] == TIP


async def test_a_first_review_records_full_first_and_reads_no_git():
    st = state(subtasks={"api": {k: v for k, v in unit().items() if k != "reviewed_head"}})
    ports, runner, st = scenario([Commit(MERGE, (TIP, BROUGHT))], st=st)

    patch, _ = await run(ports, runner, st)

    assert recorded(patch) == ("full", "first")
    assert ports.workspace.first_parent_asks == []
    assert patch["review_scopes"][0]["base"] == "base0"


async def test_a_clean_merge_with_nothing_after_it_is_reviewed_from_the_tip_as_full_clean():
    ports, runner, st = scenario([Commit(MERGE, (TIP, BROUGHT))])

    patch, call = await run(ports, runner, st)

    assert recorded(patch) == ("full", "clean")
    assert review_scope.RESOLUTION_SECTION not in call["prompt"]
    assert patch["review_scopes"][0]["base"] == TIP
    assert "reviewed_head_carried_from" not in patch["subtasks"]["api"]


async def test_a_resolution_over_the_cap_is_never_cut_and_reviews_in_full():
    long = "\n".join(f"+line {n}" for n in range(landing_rules.RESOLUTION_LINES_MAX + 1))
    ports, runner, st = scenario([Commit(MERGE, (TIP, BROUGHT))], resolutions={MERGE: long})

    patch, call = await run(ports, runner, st)

    assert recorded(patch) == ("full", "over_cap")
    assert "+line 0" not in call["prompt"]


async def test_an_old_git_reviews_a_merge_in_full_with_no_remerge_diff():
    ports, runner, st = scenario([Commit(MERGE, (TIP, BROUGHT))], version=(2, 35))

    patch, call = await run(ports, runner, st)

    assert recorded(patch) == ("full", "no_remerge_diff")
    assert review_scope.RESOLUTION_SECTION not in call["prompt"]


async def test_an_old_git_does_not_touch_a_review_with_no_merge():
    ports, runner, st = scenario([Commit(OWN, (TIP,))], version=None)

    patch, _ = await run(ports, runner, st)

    assert recorded(patch) == ("full", "no_merge")


async def test_a_git_failure_reviews_in_full_as_unreadable():
    line = [Commit(MERGE, (TIP, BROUGHT))]
    ports, runner, st = scenario(line, errors={MERGE: "boom"})
    patch, _ = await run(ports, runner, st)
    assert recorded(patch) == ("full", "unreadable")

    ports, runner, st = scenario(line)
    ports.workspace.first_parent_error = "no such ref"
    patch, _ = await run(ports, runner, st)
    assert recorded(patch) == ("full", "unreadable")


async def test_a_failure_finding_the_accepted_tip_is_unreadable_not_a_first_review():
    docs = "5" * 40
    close_docs = {"parent": TIP, "head": docs, "paths": ["docs/product/capability.md"]}
    ports, runner, st = scenario([Commit(MERGE, (TIP, BROUGHT))], st=state(close_docs=close_docs))
    ports.workspace.ancestor_errors = {docs}

    patch, call = await run(ports, runner, st)

    assert recorded(patch) == ("full", "unreadable")
    assert patch["review_scopes"][0]["base"] == TIP
    assert review_scope.RESOLUTION_SECTION not in call["prompt"]


async def test_with_no_landing_pass_the_upstream_is_the_base_branch_tip():
    ports, runner, st = scenario([Commit(MERGE, (TIP, BROUGHT))], resolutions={MERGE: HUNK})
    st = {k: v for k, v in st.items() if k != "landing"}
    ports.workspace.upstream_answer = ports.workspace.upstream_answer.__class__(
        "origin/main", MASTER, None
    )

    patch, _ = await run(ports, runner, st)

    assert recorded(patch) == ("resolution", "resolved")
    assert ports.workspace.upstream_calls == 1


async def test_a_returned_pass_records_its_scope_with_the_head_as_an_approved_one_does():
    ports, runner, st = scenario([Commit(MERGE, (TIP, BROUGHT))], resolutions={MERGE: HUNK})
    runner = ScriptedRunner(tech_lead_review=[reviewed("returned")])
    ports = memory_ports(runner, workspace=ports.workspace, stack=MemoryStack())

    patch, _ = await run(ports, runner, st)

    assert patch["subtasks"]["api"]["status"] == "returned"
    assert patch["subtasks"]["api"]["reviewed_head"] == HEAD
    assert recorded(patch) == ("resolution", "resolved")


TASK = {
    "id": "GH-28",
    "title": "Dashboard",
    "description": "d",
    "type": "feature",
    "sections": {"PM": "### Consolidated definition\n\nx"},
}


def sub(name, status, **more):
    return {
        "id": name,
        "title": name,
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": status,
        "branch": f"agentic/GH-28.{name}",
        "base": "base1",
        **more,
    }


async def test_a_branch_the_system_approves_in_the_single_arm_writes_no_scope():
    st = {
        **TASK,
        "subtask_id": "api",
        "subtasks": {
            "api": sub("api", "delivered", reviewed_head=TIP),
            "web": sub("web", "delivered"),
        },
        "sections": {**TASK["sections"], "Developer: api": "x", "Developer: web": "y"},
    }
    workspace = MemoryWorkspace()
    workspace.first_parent_line = (Commit(MERGE, (TIP, BROUGHT)),)
    runner = ScriptedRunner()
    ports = memory_ports(runner, workspace=workspace, stack=MemoryStack())
    cfg = Configuration(product_dir=MemoryWorkspace.ROOT, review_mode="single")

    patch = await review(st, cfg, ports)

    assert patch["subtasks"]["api"]["status"] == "approved"
    assert "review_scopes" not in patch and runner.calls == []
    assert workspace.first_parent_asks == [] and workspace.ancestor_checks == []


async def test_the_shadow_reviews_of_the_integration_write_only_the_deciding_scope():
    names = ("api", "web", "ui")
    st = {
        **TASK,
        "subtask_id": INTEGRATION,
        "subtasks": {
            **{n: sub(n, "approved", reviewed_head=f"h-{n}") for n in names},
            INTEGRATION: sub(INTEGRATION, "delivered", branch="agentic/GH-28", base="b"),
        },
        "sections": {
            **TASK["sections"],
            **{f"Developer: {n}": "x" for n in (*names, INTEGRATION)},
        },
    }
    workspace = MemoryWorkspace()
    runner = ScriptedRunner(tech_lead_review=[reviewed("approved")] * 4)
    ports = memory_ports(runner, workspace=workspace, stack=MemoryStack())
    cfg = Configuration(product_dir=MemoryWorkspace.ROOT, review_mode="single", shadow_parallel=1)

    patch = await review(st, cfg, ports)

    assert len(runner.calls) == 4
    [entry] = patch["review_scopes"]
    assert (entry["unit"], entry["scope"], entry["cause"]) == (INTEGRATION, "full", "first")
    assert workspace.first_parent_asks == []
