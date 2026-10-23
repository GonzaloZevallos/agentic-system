"""Shared fixtures of the landing tests (GH-330). `gh320_conflicting_facts` is the shape of
GH-320 / PR #324: an approved task whose pull request went `CONFLICTING` after master moved,
whose CI never reported, and whose three approved units are all blocked from landing as a
slice by the integrator's own paths. Landing syncs were already spent on this sha."""

from agentic.core.process.domain import ci, landing, rules
from agentic.core.process.domain.task import PullRequest, TaskState

HEAD = "a" * 40
MASTER = "b" * 40
BASE = "c" * 40
UNITS = ("api", "web", "docs")


def gh320_pull_request() -> PullRequest:
    return PullRequest(
        state="OPEN",
        created_at="2026-10-23T10:00:00+00:00",
        merged_at=None,
        commits=(),
        head_oid=HEAD,
        mergeable="CONFLICTING",
        base_oid=MASTER,
    )


def gh320_conflicting_facts() -> tuple[rules.MergeFacts, TaskState]:
    """The `MergeFacts` and the task document of GH-320: the PR `CONFLICTING` on its head and
    its base (read through `rules.merge_conflicts_from`), a CI that never reported past
    `CI_WAIT_MAX_S`, three approved units, conflicting syncs for the current sha already at
    `LANDING_CONFLICT_SYNCS_MAX`, and restart depth 0."""
    conflicts = rules.merge_conflicts_from(gh320_pull_request(), HEAD, MASTER, None)
    facts = rules.MergeFacts(
        task_id="GH-320",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=("agentic/core/process/domain/landing.py",),
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master=MASTER,
        ci_required=("checks",),
        ci_head=HEAD,
        ci_verdict=None,
        ci_waited_s=float(ci.CI_WAIT_MAX_S),
        conflicts=conflicts,
    )
    syncs = [
        {
            "head": f"{n}" * 40,
            "step": "sync_master",
            "refusal": "merge_conflict",
            "upstream": MASTER,
            "resolved_at": "2026-10-23T12:00:00+00:00",
        }
        for n in "12"
    ]
    assert len(syncs) == landing.LANDING_CONFLICT_SYNCS_MAX
    document: TaskState = {
        "id": "GH-320",
        "title": "GH-320",
        "description": "",
        "status": "ready_for_review",
        "pull_request": "https://github.com/o/r/pull/324",
        "subtasks": {name: {"status": "approved", "reviewed_head": HEAD} for name in UNITS},
        "landing": syncs,
        "landing_restarts": 0,
    }
    return facts, document


def gh320_slice_inputs() -> landing.SliceInputs:
    """What the exhaustion reads for GH-320: all three approved units carry, but each touches a
    path the integrator's work touches, so no slice is clean (`no_clean_slice`)."""
    shared = frozenset({"tests/core/process/adapters/claude_sdk/test_runner.py"})
    return landing.SliceInputs(
        approved=tuple(landing.SliceUnit(name) for name in UNITS),
        carries={name: True for name in UNITS},
        integration_paths=shared,
        unit_paths={name: shared for name in UNITS},
        landed=frozenset(),
        refused={},
        blocking_units=frozenset(),
        readable=True,
    )
