"""The settlement of `PM: final validation` rows (GH-374, ADR-0015 addendum): the one place that
decides which of that role's findings records close, written as `PlanSettledRow`s into the
patch of the run that decides it and applied per record by `rules.findings_rows`.

PM's final validation is not incremental: every round re-reads the whole task and writes its
acceptance criteria again, reworded, as `blocking | plan` rows. Nothing else closes them, so
without this module the merge gate counts every round's rows as open for good. Two decisions:

- an approving run at version V settles every open record of that location below V
  (`approved`);
- a returning run at V settles the earlier open records whose key it does not raise again
  (`superseded`) and leaves only its own open: PM's last word stands.

No head comparison and no reformulation detection. Everything reads the raw `state["findings"]`,
never `findings_rows`, so the fold and these decisions cannot feed each other. Pure: no I/O."""

from collections.abc import Iterable

from agentic.core.process.domain import rules
from agentic.core.process.domain.roles import PM_FINAL
from agentic.core.process.domain.task import PlanSettledRow, TaskState

LOCATION = PM_FINAL.name
_LOCATION_KEY = rules.finding_key(LOCATION, "")[0]


def _earlier_open(
    state: TaskState, version: int, covered: dict[tuple[str, str], int] | None = None
) -> dict[tuple[str, str], PlanSettledRow]:
    """One row per normalised key with an open `PM: final validation` record below `version`
    that `covered` (the versions `plan_settled` already settled per key) does not reach, in the
    order the keys were first raised, written `approved`; the caller overrides `by`."""
    done = covered or {}
    rows: dict[tuple[str, str], PlanSettledRow] = {}
    for record in state.get("findings", []):
        key = rules.finding_key(record["location"], record["claim"])
        if (
            record["status"] == "open"
            and key[0] == _LOCATION_KEY
            and done.get(key, 0) <= record["version"] < version
        ):
            rows.setdefault(
                key,
                {
                    "location": record["location"],
                    "claim": record["claim"],
                    "version": version,
                    "by": "approved",
                },
            )
    return rows


def on_approval(state: TaskState, version: int) -> list[PlanSettledRow]:
    """What an approving `PM: final validation` run at `version` settles: every open record at
    its location with a lower version, one row per normalised key, `by: "approved"`. A record at
    `version` or above, another location (`QA Performance`, `full_checks:*`, a subtask's file)
    and a record a reviewer dismissed are not touched."""
    return list(_earlier_open(state, version).values())


def on_return(state: TaskState, version: int, claims: Iterable[str]) -> list[PlanSettledRow]:
    """What a returning `PM: final validation` run at `version` supersedes: the earlier open
    records at its location whose key is not among `claims`, the claims this run raised at that
    location, `by: "superseded"`. The run's own records are at `version`, not below it, so they
    stay open."""
    raised = {rules.finding_key(LOCATION, claim) for claim in claims}
    return [
        {**settled, "by": "superseded"}
        for key, settled in _earlier_open(state, version).items()
        if key not in raised
    ]


def approved_green(facts: rules.MergeFacts) -> bool:
    """The reviewed head is the pull request's, approved, and green where the product declares
    a CI: the shape under which a refusal that is only ledger state is never the code's fault.
    The CI is read like `rules._ci_gate` reads it (verdict `green` on a full `ci_head` that is the
    pull request's); a product with no `ci_required` has none to read. Ids compare only through
    `rules.same_commit`."""
    if not (
        facts.reviewed_unit_approved
        and rules.full_commit_id(facts.pr_head)
        and rules.same_commit(facts.reviewed_head, facts.pr_head)
    ):
        return False
    if not facts.ci_required:
        return True
    verdict = facts.ci_verdict
    return (
        verdict is not None
        and verdict.state == "green"
        and rules.full_commit_id(facts.ci_head)
        and rules.same_commit(facts.ci_head, facts.pr_head)
    )


def ledger_only(refusal: rules.MergeRefusal, facts: rules.MergeFacts, state: TaskState) -> bool:
    """Whether `refusal` is nothing but `PM: final validation` ledger state on an approved,
    green head: `open_findings_blocking`, `approved_green`, and every open blocking row of
    `rules.open_findings` a `plan` row at that location. No code change resolves it, so the
    landing settles or escalates and never fixes or restarts (the only reader of this fact)."""
    if refusal != "open_findings_blocking" or not approved_green(facts):
        return False
    blocking = [row for row in rules.open_findings(state) if row["severity"] == "blocking"]
    return bool(blocking) and all(
        row["scope"] == "plan" and rules.finding_key(row["location"], "")[0] == _LOCATION_KEY
        for row in blocking
    )


def pending(state: TaskState) -> list[PlanSettledRow]:
    """What `on_approval` at the live approval would add that `plan_settled` does not cover
    yet: the backfill of a document written before this existed, and exactly what the landing's
    `settle` step writes. Empty when there is no live approval (`rules.pm_final_approved_version`
    is `0`)."""
    version = rules.pm_final_approved_version(state)
    if version == 0:
        return []
    covered: dict[tuple[str, str], int] = {}
    for item in state.get("plan_settled", []):
        key = rules.finding_key(item["location"], item["claim"])
        covered[key] = max(covered.get(key, 0), item["version"])
    return list(_earlier_open(state, version, covered).values())
