"""`refusal_note` (GH-885): the words of a refusal comment, apart from the verb that posts them.

Part 1 moves the names and pins what `merge._refuse` says today. Every expected string below is a
literal written here from the code as it was before the move, never one the code under test
produced; the table `GOLDEN` is the one the routing part reads as well."""

from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentic.core.process.application import landing_plan, merge, refusal_note
from agentic.core.process.domain import landing, rules

ROOT = Path(__file__).resolve().parents[4]
HEAD = "a" * 40
MASTER = "b" * 40
OTHER = "c" * 40
FIRST = f"Auto-merge refused: {HEAD} "
CLOSING = "\n\nLeft for the human to merge."
ORPHANED = (
    "The commit this task was built on will never reach the base branch: its parent was "
    "rebuilt, closed without merging, merged without a merge commit, or the record of the "
    "parent could not be read."
)

# name -> (reason, MergeFacts keywords, the comment `_refuse` posts with those facts present).
GOLDEN: dict[str, tuple[str, dict[str, object], str]] = {
    "perf_approved": (
        "perf_unverified",
        {"perf_verdict": "approved", "perf_head": OTHER},
        f"{FIRST}perf_unverified\n\nmeasured {OTHER}, head {HEAD}{CLOSING}",
    ),
    "perf_none": (
        "perf_unverified",
        {},
        f"{FIRST}perf_unverified\n\nverdict none{CLOSING}",
    ),
    "over_cap_messages": (
        "criteria_over_cap",
        {"criteria_regressed": ("a.md: 12 lines, cap 10", "b.md: 30 lines, cap 20")},
        f"{FIRST}criteria_over_cap\n\n- a.md: 12 lines, cap 10\n- b.md: 30 lines, cap 20{CLOSING}",
    ),
    "over_cap_none": ("criteria_over_cap", {}, f"{FIRST}criteria_over_cap{CLOSING}"),
    "unreadable": ("criteria_unreadable", {}, f"{FIRST}criteria_unreadable{CLOSING}"),
    "orphaned": (
        "stack_base_orphaned",
        {},
        f"{FIRST}stack_base_orphaned\n\n{ORPHANED}{CLOSING}",
    ),
}
# The same four reasons with `facts=None`: the first line and the closing, and for
# `stack_base_orphaned` its evidence too (that branch is not guarded by the facts).
NO_FACTS = {
    "perf_unverified": f"{FIRST}perf_unverified{CLOSING}",
    "criteria_unreadable": f"{FIRST}criteria_unreadable{CLOSING}",
    "criteria_over_cap": f"{FIRST}criteria_over_cap{CLOSING}",
    "stack_base_orphaned": f"{FIRST}stack_base_orphaned\n\n{ORPHANED}{CLOSING}",
}


class _Tracker:
    def __init__(self) -> None:
        self.notes: list[str] = []

    def note(self, task_id: str, text: str) -> None:
        self.notes.append(text)


class _Documents:
    def __init__(self, document: dict[str, object]) -> None:
        self.document = document

    def load(self, task_id: str) -> dict[str, object]:
        return self.document

    def record_closure(self, task_id: str, fields: dict[str, object]) -> None:
        raise AssertionError("a refusal whose note posted writes nothing")


def _facts(**more: object) -> rules.MergeFacts:
    base = {
        "task_id": "GH-5",
        "status": "ready_for_review",
        "reviewed_unit_approved": True,
        "reviewed_head": HEAD,
        "open_blocking": 0,
        "open_important": 0,
        "pr_state": "open",
        "pr_head": HEAD,
        "in_eval_set": False,
        "touched_paths": (),
        "attempts": 0,
        "checks_head": HEAD,
        "checks_master": MASTER,
        "checks_passed": True,
        "current_master": MASTER,
        "open_escalation": False,
    }
    return rules.MergeFacts(**{**base, **more})


def _refuse(reason, facts, document=None):
    ports = SimpleNamespace(tracker=_Tracker(), documents=_Documents(document or {}))
    result = merge._refuse("GH-5", HEAD, reason, ports, facts=facts)
    assert (result.kind, result.reason) == ("refused", reason)
    return ports.tracker.notes


@pytest.mark.parametrize("name", GOLDEN)
def test_the_verb_says_each_poller_noted_refusal_as_it_always_did(name):
    reason, keywords, expected = GOLDEN[name]

    assert _refuse(reason, _facts(**keywords)) == [expected]


@pytest.mark.parametrize("reason", NO_FACTS)
def test_the_verb_with_no_facts_says_the_first_line_and_the_closing(reason):
    assert _refuse(reason, None) == [NO_FACTS[reason]]


# The four reasons with the facts of their `GOLDEN` row, and the comment each posts when the
# landing takes a `sync_master`: the same text with no closing.
STEPPED = {
    "perf_none": f"{FIRST}perf_unverified\n\nverdict none",
    "over_cap_messages": (
        f"{FIRST}criteria_over_cap\n\n- a.md: 12 lines, cap 10\n- b.md: 30 lines, cap 20"
    ),
    "unreadable": f"{FIRST}criteria_unreadable",
    "orphaned": f"{FIRST}stack_base_orphaned\n\n{ORPHANED}",
}
_DIAGNOSED = {"step": "diagnose", "delivered": True, "head": HEAD, "upstream": MASTER}
DOCUMENTS = {
    "fresh": {},
    "unlandable": {"landing": [{**_DIAGNOSED, "verdict": "unlandable"}]},
    "passes_spent": {"landing": [{"step": "fix", "head": HEAD}] * 3},
    "verdict_sync_due": {"landing": [{**_DIAGNOSED, "verdict": "sync"}]},
}
# document -> (the step kind `landing_step` answers for every reason here, the Landing line).
ROWS = {
    "fresh": ("none", None),
    "unlandable": ("none", None),
    "passes_spent": ("none", None),
    "verdict_sync_due": ("sync_master", f"Landing: {HEAD} sync_master"),
}


@pytest.mark.parametrize("document", ROWS)
@pytest.mark.parametrize("name", STEPPED)
def test_the_closing_is_left_to_the_human_only_when_the_step_is_none(name, document):
    reason, keywords, with_closing = GOLDEN[name]
    facts = _facts(**keywords)
    kind, landing_line = ROWS[document]

    step = landing_plan.step_for(DOCUMENTS[document], reason, facts, turn="head")
    notes = _refuse(reason, facts, DOCUMENTS[document])

    assert step.kind == kind
    assert notes[0] == (with_closing if kind == "none" else STEPPED[name])
    assert notes[1:] == ([landing_line] if landing_line else [])


def test_poller_noted_is_a_subset_of_what_is_commented_and_human_owned():
    assert set(refusal_note.POLLER_NOTED) <= set(merge.COMMENTED)
    assert set(refusal_note.POLLER_NOTED) <= set(landing.HUMAN_OWNED_REFUSALS)
    for other in ("drawing_unsettled", "ci_untrusted_no_dispatch", "open_escalation"):
        assert other not in refusal_note.POLLER_NOTED
    assert refusal_note.POLLER_NOTED == (
        "perf_unverified",
        "criteria_unreadable",
        "criteria_over_cap",
        "stack_base_orphaned",
    )


@pytest.mark.parametrize(
    ("step", "closing"),
    [
        (None, "Left for the human to merge."),
        (landing.LandingStep("none", "perf_unverified"), "Left for the human to merge."),
        (landing.LandingStep("sync_master", "perf_unverified"), ""),
        (landing.LandingStep("escalate", "perf_unverified"), ""),
    ],
)
def test_the_closing_follows_the_step(step, closing):
    assert refusal_note.closing_for(step) == closing


def test_said_is_true_for_an_earlier_body_that_is_not_the_last():
    bodies = [f"{FIRST}perf_unverified\n\nverdict none", f"{FIRST}criteria_unreadable"]

    assert refusal_note.said(bodies, HEAD, "perf_unverified")


def test_said_is_false_for_another_head_another_reason_or_a_later_line():
    bodies = [
        f"{FIRST}perf_unverified",
        f"Landing: {HEAD} sync_master\n{FIRST}criteria_unreadable",
    ]

    assert not refusal_note.said(bodies, OTHER, "perf_unverified")
    assert not refusal_note.said(bodies, HEAD, "criteria_over_cap")
    assert not refusal_note.said(bodies, HEAD, "criteria_unreadable")
    assert not refusal_note.said([], HEAD, "perf_unverified")


def _import_violations(source: str) -> list[str]:
    """The imports of `source`, at any nesting, that are not `..domain*` or `.ports`, and any
    absolute one naming `poll`, `merge`, `resume` or `landing_plan`."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.level:
            target = (node.level, (node.module or "").split(".")[0])
            if target not in {(2, "domain"), (1, "ports")}:
                found.append(f"{'.' * node.level}{node.module or ''}")
        elif isinstance(node, ast.Import | ast.ImportFrom):
            names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
            if any(part in names_part.split(".") for names_part in names for part in FORBIDDEN):
                found.append(ast.unparse(node))
    return found


FORBIDDEN = ("poll", "merge", "resume", "landing_plan")


def test_the_module_imports_only_the_domain_and_the_ports():
    source = (ROOT / "agentic/core/process/application/refusal_note.py").read_text()

    assert _import_violations(source) == []


def test_the_import_check_rejects_a_module_that_reaches_for_the_verb_or_the_poller():
    source = (
        "from .poll import AUTO_MERGED\n"
        "import agentic.core.process.application.merge\n"
        "def f():\n"
        "    from . import resume\n"
        "    from .landing_plan import step_for\n"
        "from ..domain import rules\n"
        "from .ports import Ports\n"
    )

    assert _import_violations(source) == [
        ".poll",
        "import agentic.core.process.application.merge",
        ".",
        ".landing_plan",
    ]


@pytest.mark.parametrize("module", ["refusal_note", "poll", "merge"])
def test_each_module_imports_in_a_fresh_interpreter(module):
    done = subprocess.run(
        [sys.executable, "-c", f"import agentic.core.process.application.{module}"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )

    assert done.returncode == 0, done.stderr


def test_the_module_is_under_the_type_checker():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert "agentic/core/process/application/refusal_note.py" in config["tool"]["mypy"]["files"]
