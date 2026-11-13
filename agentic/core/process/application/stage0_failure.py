"""The failure policy of a stage0 rotation that smokes twice before it blames a commit (GH-632,
part 2f). A red candidate smoke is rerun once; a second red is compared with a control smoke of
the current stage, and only a red candidate beside a green control is rejected. Anything that
is not that (a smoke that could not run, a red control, no current stage) is `Unavailable`, is
counted per commit, and never writes `rejected`. Pure application: everything goes through the
store and the candidate ports. Nothing outside tests builds it yet; the CLI command of part 2d
plugs it into `rotate`.

`current` is read from the store at call time, under the rotation's lock, never captured when
the policy is built: a deliberate reading of the request's "with the current stage name", so the
control is always the stage the rotation is leaving.

Contract for the adapter of part 2d: a smoke that cannot run (a missing stage, a full disk, a
timeout) is `Unavailable` with its own cause, never `Red`; only a smoke that ran and failed is
`Red`. The cause comes only from exit code, exception type and timeout, never from output.

Known false-reject: a green control under a different `.env` than the candidate's can reject a
good commit. Editing `rejected` undoes it."""

from __future__ import annotations

from ..domain.rules import same_commit
from ..domain.stage0_rotation import (
    CandidateCause,
    Green,
    Proceed,
    Red,
    Stop,
    Unavailable,
)
from .ports import Stage0Candidate, Stage0Store

_KEEP_UNAVAILABLE = "keep: unavailable"


class SmokeFailurePolicy:
    """A `FailurePolicy` that reruns a red smoke and rejects only after a green control. Holds
    the two ports and nothing else: every call reads what it needs from them."""

    def __init__(self, store: Stage0Store, candidate: Stage0Candidate) -> None:
        self._store = store
        self._candidate = candidate

    def prepare_failed(self, sha: str, outcome: Unavailable) -> Stop:
        return self._unavailable(sha, outcome.cause)

    def smoke_failed(self, sha: str, stage: str, outcome: Red | Unavailable) -> Proceed | Stop:
        if isinstance(outcome, Unavailable):
            return self._unavailable(sha, outcome.cause)
        rerun = self._candidate.smoke(stage)
        if isinstance(rerun, Green):
            return Proceed(f"smoke_flaked: {sha}", rerun)
        if isinstance(rerun, Unavailable):
            return self._unavailable(sha, rerun.cause)
        current = self._store.current()
        if current is None:
            return self._unavailable(sha, "control_red")
        control = self._candidate.smoke(current)
        if isinstance(control, Green):
            self._store.remove_partial(sha)
            self._store.reject(sha)
            return Stop(f"smoke_failed: {sha}")
        if isinstance(control, Unavailable):
            return self._unavailable(sha, control.cause)
        return self._unavailable(sha, "control_red")

    def succeeded(self, sha: str) -> None:
        self._store.clear_unavailable()

    def _unavailable(self, sha: str, cause: CandidateCause) -> Stop:
        """Removes the partial, then counts `sha` as unavailable once more (or from 1 when the
        record names another commit), then says so. Never writes `rejected`."""
        self._store.remove_partial(sha)
        record = self._store.unavailable_record()
        if record is not None and same_commit(record[0], sha):
            known, count = record[0], record[1] + 1
            sha = max(known, sha, key=len)
        else:
            count = 1
        self._store.write_unavailable(sha, count, cause)
        return Stop(_KEEP_UNAVAILABLE)
