"""Shared fixtures of the landing tests (GH-330). `gh320_conflicting_facts` is the shape of
GH-320 / PR #324: an approved task whose pull request went `CONFLICTING` after master moved,
whose CI never reported, and whose three approved units are all blocked from landing as a
slice by the integrator's own paths. Landing syncs were already spent on this sha."""

from agentic.core.process.domain import ci, landing, presubmit, rules
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
    conflicts = rules.merge_conflicts_from(gh320_pull_request(), HEAD, MASTER, None, recorded=False)
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


API_BREAK_LINE = presubmit.ApiBreakLine("Breaking-API:", "docs", "api_breaks")
DOCS_RUN = "https://github.com/o/r/actions/runs/{}/job/1"


def _red(name: str, step: str | None, run: int, started: str) -> ci.CiCheck:
    return ci.CiCheck(
        name,
        "COMPLETED",
        "FAILURE",
        DOCS_RUN.format(run),
        failed_step=step,
        started_at=started,
        completed_at=started,
    )


def _green(name: str, run: int) -> ci.CiCheck:
    return ci.CiCheck(name, "COMPLETED", "SUCCESS", DOCS_RUN.format(run))


def gh686_body_only(
    *, docs_step: str | None = "api_breaks", started: str = "2026-11-10T10:00:00Z"
) -> tuple[rules.MergeFacts, landing.LandingSpent]:
    """The head of apostrophe GH-686 (D-c1b9a7f0): approved, the branch holds master, every job
    green but `docs`, red at its `api_breaks` step, and `ci-ok` red because `docs` is; the
    landing's passes and syncs are spent and the slicing says `no_clean_slice`, the restart that
    cost $141.63. The pull request body lacks the product's `Breaking-API:` line."""
    failing = (_red("docs", docs_step, 11, started), _red("ci-ok", None, 12, started))
    verdict = ci.CiVerdict(
        "red",
        failing,
        (),
        red=(("docs", failing[:1]), ("ci-ok", failing[1:])),
    )
    facts = rules.MergeFacts(
        task_id="GH-686",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=("src/api/routes.py",),
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master=MASTER,
        ci_required=("docs", "ci-ok", "tests"),
        ci_head=HEAD,
        ci_verdict=verdict,
    )
    spent = landing.LandingSpent(
        passes=landing.LANDING_PASSES_MAX,
        syncs=landing.LANDING_SYNCS_MAX,
        reruns_on_head=1,
        diagnoses=0,
        waited_s=None,
    )
    return facts, spent
