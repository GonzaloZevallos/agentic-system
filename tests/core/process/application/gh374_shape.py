"""The recorded shape of apostrophe GH-374 when `agentic land` refused it (GH-442): an approved,
restored task whose pull request is open, with three `fix` entries spent, an `escalate` for
`ci_failed` on `web` (red on the head and on master) and one `rerun` after it, all on the head
32bc0e91. The refusal is `ci_failed`, `conflicts` is not True, the head is red on `web` and
`ci_red_on_base` is empty: master's `web` is green again. Shared by the suites that build the
deadlock, so each states the recorded values once."""

from __future__ import annotations

from dataclasses import replace

from agentic.core.process.domain import ci, rules

HEAD = "32bc0e91" + "0" * 32
MASTER = "b" * 40
NEW_MASTER = "c" * 40
REFERENCE = "https://github.com/o/r/pull/443"
ESCALATED_AT = "2026-10-28T20:00:00+00:00"
RERUN_AT = "2026-10-29T07:14:00+00:00"
RELEASED_AT = "2026-10-29T09:00:00+00:00"
HUMAN = "### Decision\n\nMerge master in and rerun.\n"
RUN = "https://github.com/o/r/actions/runs/{}"

ARCHIVED = {
    "head": "e9c47772" + "0" * 32,
    "step": "diagnose",
    "refusal": "merge_failed",
    "delivered": True,
    "verdict": "unlandable",
    "at": "2026-10-20T00:00:00+00:00",
}


def _fix(minute: int) -> dict:
    return {
        "head": HEAD,
        "step": "fix",
        "refusal": "open_findings_blocking",
        "at": f"2026-10-28T19:{minute:02d}:00+00:00",
        "resolved_at": f"2026-10-28T19:{minute + 5:02d}:00+00:00",
    }


ESCALATE = {"head": HEAD, "step": "escalate", "refusal": "ci_failed", "at": ESCALATED_AT}
RERUN = {
    "head": HEAD,
    "step": "rerun",
    "refusal": "ci_failed",
    "at": RERUN_AT,
    "checks": ["web"],
}


def document(**over) -> dict:
    """The escalated document, with `over` replacing top-level keys."""
    raw: dict = {
        "id": "GH-374",
        "title": "t",
        "description": "d",
        "status": "waiting_on_human",
        "escalated": "CI check(s) `web` red on the head and also red on the base branch",
        "escalation_class": "questions",
        "pull_request": REFERENCE,
        "ci_required": [],
        "sections": {"Human": HUMAN},
        "landing": [ARCHIVED, _fix(0), _fix(10), _fix(20), dict(ESCALATE), dict(RERUN)],
        "restorations": [{"at": "2026-10-28T10:00:00+00:00", "landing_cut": 1}],
        "subtasks": {"integration": {"status": "approved"}},
        "cost_usd": 297.0,
    }
    return {**raw, **over}


def web(conclusion: str | None = "FAILURE", *, run: int = 1) -> ci.CiCheck:
    status = "COMPLETED" if conclusion else "IN_PROGRESS"
    return ci.CiCheck("web", status, conclusion, RUN.format(run))


def report(*checks: ci.CiCheck, head: str = HEAD) -> ci.CiReport:
    return ci.CiReport(head, "master", MASTER, "2026-10-28T18:00:00+00:00", checks)


def facts(**over) -> rules.MergeFacts:
    """The facts of the failing `land`: head red on `web`, nothing red on the base."""
    found = rules.MergeFacts(
        task_id="GH-374",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=None,
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master=NEW_MASTER,
        conflicts=False,
        ci_required=("web",),
        ci_head=HEAD,
        ci_verdict=ci.CiVerdict("red", (web(),), ()),
        ci_red_on_base=(),
    )
    return replace(found, **over)
