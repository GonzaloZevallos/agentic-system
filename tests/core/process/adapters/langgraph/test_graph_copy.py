"""The Tech Writer through the graph (README.md, 4), on the fake ports: a feature that
touches copy has its texts written after the definition and reviewed after the integration;
any other task goes through the nodes it always did."""

from __future__ import annotations

import dataclasses
import hashlib

import pytest
from langgraph.types import Command

from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.application import landing, merge, poll, resume
from agentic.core.process.application.ports import Upstream
from agentic.core.process.domain import rules
from agentic.core.process.domain.copy import (
    NO_WRITING_CLAIM,
    NO_WRITING_LOCATION,
    NO_WRITING_NOTE,
    copy_location_parts,
)
from agentic.core.process.domain.task import INTEGRATION
from tests.core.process.adapters.langgraph.test_graph import (
    DESIGN_SLUGS,
    Fakes,
    compiled,
    raised,
    run_graph,
    script_for,
    technical,
)
from tests.core.process.application.test_poll import _cfg, _Locks
from tests.core.process.application.test_poll_merge import HEAD, MASTER, URL, _HeldLocks, _pr
from tests.fakes import (
    FakeLauncher,
    consolidation,
    copy_draft,
    copy_finding,
    copy_reviewed,
    delivered,
    design_draft,
    design_findings,
    final_validation,
    memory_ports,
    planned,
    reviewed,
    verdict,
)

pytestmark = pytest.mark.anyio

# sha256[:16] of every prompt this scenario hands its roles (the product directory
# replaced by TMP), measured at 2e8b7d9, before the Tech Writer: the rendering today.
# The last (`pm_final`) was re-pinned when it gained the delivered-diff fact (close-time pass).
TODAYS_PROMPTS = [
    "0e6577edeacb6edb",
    "72d73d6fa29dc6d8",
    "18265170ebae82a7",
    "dd639b81a27b5656",
    "8860baa4974a826f",
    "15ff94b957022042",
    "f265d7c2937c133f",
    "4e32760b61f29107",
    "3d8e8943bfbcc855",
    "259dbea222e5ec88",
    "ee3a706fc545db3b",
    "b98a4471bb12b37d",
    "1100e9b51ee48d53",
    "05b4da22f14d7947",
    "e632cbbd62d83b5c",
    "27c14992def4ca06",
    "983429e4371d2542",
    "7cc6dc3e35498fca",
]

COPY = {"touches_ui": False, "touches_copy": True}
OFF = {"touches_ui": True, "touches_copy": False}
TEXTS = "### Texts\n\n- `Export` button: Export report."


@pytest.fixture
def config(tmp_path):
    return {"configurable": {"product_dir": str(tmp_path), "thread_id": "GH-1"}}


def _copy_script(review, **queues):
    return script_for(
        pm=[verdict("approved", flags=COPY)],
        tech_writer=[copy_draft(TEXTS)],
        tech_writer_review=review,
        **technical(**queues),
    )


async def test_a_touches_copy_feature_writes_before_the_plan_and_reviews_after_the_approval(
    config, tmp_path
):
    (tmp_path / "WRITING_STANDARDS.md").write_text("# Writing\n\nErrors say what to do next.\n")
    fakes = Fakes()
    fakes.workspace.diff_paths = ("src/x.py",)
    script = _copy_script([copy_reviewed([copy_finding(scope="outside")])])
    final = await run_graph(script, config, fakes=fakes)
    assert final["status"] == "ready_for_review"
    slugs = script.slugs
    assert slugs.index("pm") < slugs.index("tech_writer") < slugs.index("tech_lead_ephemeral")
    assert slugs.index("tech_lead_review") < slugs.index("tech_writer_review")
    assert slugs.count("tech_writer") == 1 and slugs.count("tech_writer_review") == 1
    # The section is what the role wrote, untouched: the criteria exist, so no note.
    assert final["sections"]["Tech Writer"] == TEXTS
    # The row is the review's, owned by the subtask whose diff touches the file.
    (row,) = [r for r in final["findings"] if copy_location_parts(r["location"])]
    assert row["location"] == "copy:1.1:src/x.py:12"
    assert (row["severity"], row["subtask"], row["version"]) == ("important", "api", 1)
    # The review never records a return, a pair or a change request.
    assert not final.get("changes_requested") and not final.get("change_history")
    # Both roles read the criteria whole; the review is told the base and gets the Tech
    # Writer's section as written.
    for slug in ("tech_writer", "tech_writer_review"):
        assert "Errors say what to do next." in script.prompts(slug)[0]
    assert f"## Tech Writer\n\n{TEXTS}" in script.prompts("tech_writer_review")[0]
    assert "- src/x.py" in script.prompts("tech_writer_review")[0]
    assert "Errors say" not in script.prompts("developer")[0]
    assert "Errors say" not in script.prompts("tech_lead_review")[0]


async def test_a_product_without_writing_criteria_says_so_in_both_nodes(config):
    script = _copy_script([copy_reviewed(settled=[], proposed_standards="# Writing\n\nVoice.")])
    final = await run_graph(script, config)
    assert final["status"] == "ready_for_review"
    assert final["sections"]["Tech Writer"] == f"_{NO_WRITING_NOTE}_\n\n{TEXTS}"
    assert NO_WRITING_CLAIM in script.prompts("tech_writer_review")[0]
    (row,) = [r for r in final["findings"] if copy_location_parts(r["location"])]
    assert row["location"] == NO_WRITING_LOCATION
    assert (row["severity"], row["scope"]) == ("important", "outside")
    assert row["proposed"] == "# Writing\n\nVoice."
    assert final.get("escalated") in (None, False)


async def test_the_review_follows_the_integration_when_there_are_several_subtasks(config, tmp_path):
    (tmp_path / "WRITING_STANDARDS.md").write_text("# Writing\n\nErrors say what to do next.\n")
    fakes = Fakes()
    fakes.workspace.diff_paths = ("src/x.py",)
    script = script_for(
        pm=[verdict("approved", flags=COPY)],
        tech_writer=[copy_draft(TEXTS)],
        tech_writer_review=[copy_reviewed([copy_finding(scope="outside")])],
        **technical(
            tech_lead=[planned({"id": "domain", "title": "Domain", "brief": "b", "seams": ["x"]})],
            developer__api=[delivered("api")],
            developer__domain=[delivered("domain")],
            tech_lead_review__api=[reviewed("approved")],
            tech_lead_review__domain=[reviewed("approved")],
            tech_lead_review=[reviewed("approved")],
        ),
    )
    final = await run_graph(script, config, fakes=fakes)
    assert final["status"] == "ready_for_review"
    assert INTEGRATION in final["subtasks"]
    last_review = max(i for i, s in enumerate(script.slugs) if s == "tech_lead_review")
    assert script.slugs.index("tech_writer_review") > last_review
    (row,) = [r for r in final["findings"] if copy_location_parts(r["location"])]
    assert row["subtask"] == "api, domain"


async def test_a_feature_with_touches_ui_and_no_touches_copy_goes_through_today_nodes(
    config, tmp_path
):
    """Byte-equality outside `copy_applies`: the same nodes, and the same input for every
    role whether or not the product has writing criteria, which only the two text roles read."""

    def scripted(flags):
        return script_for(
            pm=[verdict("approved", flags=flags)],
            ux_designer=[design_draft()],
            ux_lead=[design_findings("approved")],
            ui_designer=[design_draft()],
            ui_lead=[design_findings("approved")],
            pm_consolidator=[consolidation("approved")],
            ux_lead_final=[design_findings("approved")],
            ui_lead_final=[design_findings("approved")],
            pm_final=[final_validation("approved")],
            **technical(),
        )

    other = tmp_path / "with-criteria"
    other.mkdir()
    (other / "WRITING_STANDARDS.md").write_text("# Writing\n\nErrors say what to do next.\n")
    plain, with_criteria = scripted(OFF), scripted(OFF)
    await run_graph(plain, config)
    await run_graph(
        with_criteria, {"configurable": {**config["configurable"], "product_dir": str(other)}}
    )
    assert plain.slugs == with_criteria.slugs
    i = plain.slugs.index("ux_designer")
    assert plain.slugs[i : i + len(DESIGN_SLUGS)] == DESIGN_SLUGS
    assert not {"tech_writer", "tech_writer_review"} & set(plain.slugs)
    assert [c["prompt"] for c in plain.calls] == [c["prompt"] for c in with_criteria.calls]
    # And against the rendering before this role existed, so a change that reached every
    # role the same way cannot pass. Re-pin only when a prompt changes on purpose.
    digests = [
        hashlib.sha256(c["prompt"].replace(str(tmp_path), "TMP").encode()).hexdigest()[:16]
        for c in plain.calls
    ]
    assert digests == TODAYS_PROMPTS
    # No role of that task is handed anything of the writing family: today's rendering.
    for call in plain.calls:
        assert "Tech Writer" not in call["prompt"] and "WRITING_STANDARDS" not in call["prompt"]
        assert "Errors say" not in call["prompt"]


def test_the_texts_are_the_last_step_of_the_definition_ladder():
    state = {
        "type": "feature",
        "flags": COPY,
        "runs": [{"role": "Product Analyst", "section": "Product Analyst"}],
    }
    assert rules.design_step(state) == "write_copy"
    assert (
        rules.after_write_copy({**state, "runs": [*state["runs"], {"role": "Tech Writer"}]})
        == "plan"
    )
    assert rules.after_write_copy({**state, "escalated": True}) is None


# --- The lifecycle past `deliver` (ADR-0053): the landing reads the saved document ----------

NOW = "2026-10-14T00:00:00+00:00"
NEW_HEAD = "d" * 40
UPSTREAM = Upstream("origin/master", MASTER, None)


def _landing_ports(tmp_path, fakes):
    """The landing's ports over the document the graph saved and the tracker and workspace
    it ran with: the pull request open on `HEAD`, every unit approved on it, checks green."""
    ports = memory_ports(
        documents=FilesystemTaskDocuments(tmp_path),
        workspace=fakes.workspace,
        tracker=fakes.tracker,
    )
    ports.workspace.upstream_answer = UPSTREAM
    ports.workspace.conflict_answers[(HEAD, MASTER)] = False
    _on_head(ports, HEAD)
    return ports


def _on_head(ports, head):
    """`deliver` on `head`: the pull request there, the units approved on it, checks green."""
    document = ports.documents.load("GH-1") or {}
    ports.tracker.prs[URL.format(1)] = dataclasses.replace(_pr(), head_oid=head)
    ports.documents.save(
        {
            **document,
            "pull_request": URL.format(1),
            "subtasks": {
                name: {**unit, "status": "approved", "reviewed_head": head}
                for name, unit in document["subtasks"].items()
            },
            "merge_checks": {"head": head, "master": MASTER, "passed": True},
        }
    )


def _candidates(ports) -> list[int]:
    return poll.gather_merges(ports, frozenset(), MASTER, _Locks(), [])


def _land(ports, tmp_path, launcher):
    lines: list[str] = []
    result = landing.land(
        "GH-1",
        _cfg(tmp_path),
        ports,
        _HeldLocks(),
        UPSTREAM,
        frozenset(),
        lines.append,
        clock=lambda: NOW,
        launcher=launcher,
        argv0="agentic",
    )
    return result


async def _resume_graph(script, config, tmp_path, fakes, ports):
    """`agentic resume` on the reopened document, as the landing spawns it."""
    fakes.workspace.existing_branches["agentic/GH-1"] = (HEAD, "base0")
    state = resume.from_document("GH-1", ports)
    app = compiled(script, fakes=fakes)
    await app.aupdate_state(config, state, as_node="escalate")
    return await app.ainvoke(Command(resume={"decision": None}), config=raised(config))


def _merge(ports, tmp_path):
    merge.merge_task(
        "GH-1",
        1,
        _cfg(tmp_path),
        ports,
        UPSTREAM,
        frozenset(),
        lambda line: None,
        clock=lambda: NOW,
    )


def _settled(document):
    return [r for r in document["findings"] if copy_location_parts(r["location"])]


def _lifecycle_script(reviews):
    return _copy_script(
        reviews,
        developer=[delivered("api"), delivered("api again")],
        tech_lead_review=[reviewed("approved")] * 4,
    )


async def _delivered(config, tmp_path, script, standards=True):
    if standards:
        (tmp_path / "WRITING_STANDARDS.md").write_text("# Writing\n\nErrors say what to do next.\n")
    fakes = Fakes()
    fakes.workspace.diff_paths = ("src/x.py",)
    final = await run_graph(script, config, fakes=fakes)
    return final, fakes, _landing_ports(tmp_path, fakes)


async def test_a_copy_important_is_fixed_before_the_pull_request_and_a_review_settles(
    config, tmp_path
):
    """GH-354: the final review's `owned` important no longer waits for the pull request:
    the pre-PR pass reopens the owner, the second review settles it, and the task delivers
    with nothing open (the landing has no fix left to run)."""
    script = _lifecycle_script(
        [copy_reviewed([copy_finding()]), copy_reviewed(settled=["copy:1.1:src/x.py:12"])]
    )
    final, fakes, ports = await _delivered(config, tmp_path, script)

    assert script.slugs.count("tech_writer_review") == 2
    assert script.slugs.count("tech_writer") == 1
    assert final["sections"]["Tech Writer"] == TEXTS
    (row,) = _settled(final)
    assert row["location"] == "copy:1.1:src/x.py:12"
    assert "copy:1.1:src/x.py:12" in final["settled_findings"]
    assert rules.open_findings(final) == []
    (entry,) = final["landing"]
    assert (entry["step"], entry["before_pr"]) == ("fix_important", True)
    assert entry["resolved_at"]
    assert not final.get("changes_requested")
    assert _candidates(ports) == [1]
    _merge(ports, tmp_path)
    assert fakes.tracker.merge_calls == [(URL.format(1), HEAD)]
    assert not final.get("escalated")


async def test_a_review_that_raises_a_new_important_every_pass_costs_one_fix_and_one_second_pass(
    config, tmp_path
):
    script = _lifecycle_script(
        [
            copy_reviewed([copy_finding()]),
            copy_reviewed(
                [copy_finding(location="src/x.py:13", claim="Another vague error")],
                settled=["copy:1.1:src/x.py:12"],
            ),
            copy_reviewed([copy_finding(location="src/x.py:14", claim="Yet another")]),
        ]
    )
    final, fakes, ports = await _delivered(config, tmp_path, script)
    launcher = FakeLauncher()

    assert script.slugs.count("tech_writer_review") == 2
    assert [r["location"] for r in rules.open_findings(final)] == ["copy:2.1:src/x.py:13"]
    assert _candidates(ports) == [1]
    assert _land(ports, tmp_path, launcher).step != "fix_important"
    assert launcher.spawned == []
    assert not final.get("escalated")


async def test_a_product_without_criteria_files_its_follow_up_and_merges_without_a_human(
    config, tmp_path
):
    script = _lifecycle_script(
        [copy_reviewed(settled=[], proposed_standards="# Writing\n\nVoice.")]
    )
    final, fakes, ports = await _delivered(config, tmp_path, script, standards=False)
    assert not final.get("escalated") and _candidates(ports) == []

    assert _land(ports, tmp_path, FakeLauncher()).step == "file_followups_and_land"

    [(title, body)] = fakes.tracker.opened_issues
    assert title == "Follow-up of #1: open important findings"
    assert "WRITING_STANDARDS.md" in body
    assert fakes.tracker.opened_labels == ["needs-triage"]
    assert _candidates(ports) == [1]
    _merge(ports, tmp_path)
    assert fakes.tracker.merge_calls == [(URL.format(1), HEAD)]
    assert not (ports.documents.load("GH-1") or {}).get("escalated")
