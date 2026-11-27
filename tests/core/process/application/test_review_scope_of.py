"""`review_scope.scope_of` (GH-976, ADR-0089): the one computer of what a pass over a moved head
covers, now that the system's regeneration commits are recorded in `reintegrations`. Seam:
`application.review_scope.scope_of` through the shared fake workspace and a stack that answers
`docs` per revision, so a read at the wrong revision fails loudly."""

from pathlib import Path

from agentic.core.process.application import review_scope
from agentic.core.process.application.ports import GitError
from agentic.core.process.domain.docs import Docs
from agentic.core.process.domain.rules import Commit
from tests.fakes import MemoryStack, MemoryWorkspace, memory_ports

TIP, MASTER, MERGE, REGEN, OWN = "r" * 40, "m" * 40, "1" * 40, "2" * 40, "3" * 40
BROUGHT, BASE, HEAD = "4" * 40, "b" * 40, "9" * 40
OWNED = "docs/generated/a.md"
DOCS = Docs(generate="make docs", paths=("docs/generated/**",))
OWNED_HUNK = f"diff --git a/{OWNED} b/{OWNED}\n+row"
CODE_HUNK = "diff --git a/src/x.py b/src/x.py\n+x = 1"


class StackAt(MemoryStack):
    """`docs` per revision; a revision nobody declared is a test bug, not a `GitError`."""

    def __init__(self, by_revision: dict[str, Docs | GitError | None]) -> None:
        super().__init__()
        self.by_revision = by_revision
        self.docs_reads: list[str] = []

    def docs(self, revision: str) -> Docs | None:
        self.docs_reads.append(revision)
        answer = self.by_revision[revision]
        if isinstance(answer, GitError):
            raise answer
        return answer


def chain(
    *line: Commit,
    reintegrations: tuple[str, ...] = (),
    resolutions: dict[str, str] | None = None,
    touched: dict[str, tuple[str, ...]] | None = None,
    stack: StackAt | None = None,
    touched_fails: str | None = None,
):
    """`scope_of` over `line` (the first-parent commits after the accepted tip `TIP`)."""
    workspace = MemoryWorkspace()
    workspace.first_parent_line = line
    workspace.ancestors_by_ref = {HEAD: {TIP}, MASTER: {BROUGHT}}
    workspace.merge_resolutions = resolutions or {}
    workspace.touched_by = {(c.parents[0], c.sha): (touched or {}).get(c.sha, ()) for c in line}
    if touched_fails:
        workspace.touched_head_errors = {touched_fails}
    workspace.existing_branches = {"agentic/GH-976": (HEAD, BASE)}
    stack = stack or StackAt({BASE: DOCS})
    ports = memory_ports(None, workspace=workspace, stack=stack)
    state = {
        "id": "GH-976",
        "subtasks": {"api": {"id": "api", "reviewed_head": TIP}},
        "landing": [{"step": "sync_master", "upstream": MASTER}],
        "reintegrations": [{"regeneration": sha} for sha in reintegrations],
    }
    verdict, tip = review_scope.scope_of(state, "api", HEAD, Path("/co"), ports)
    return (verdict.scope, verdict.cause), workspace, stack


MERGE_STEP = Commit(MERGE, (TIP, BROUGHT))
REGEN_STEP = Commit(REGEN, (MERGE,))


def test_a_recorded_one_parent_commit_with_owned_paths_is_carried_regenerated():
    result, workspace, _ = chain(
        MERGE_STEP,
        REGEN_STEP,
        reintegrations=(REGEN[:12],),
        resolutions={REGEN: OWNED_HUNK},
        touched={REGEN: (OWNED,)},
    )
    assert result == ("carried", "regenerated")
    assert workspace.touched_reads == [(MERGE, REGEN)]


def test_a_recorded_commit_touching_code_outside_docs_paths_is_a_resolution():
    result, _, _ = chain(
        MERGE_STEP,
        REGEN_STEP,
        reintegrations=(REGEN,),
        resolutions={REGEN: OWNED_HUNK + "\n" + CODE_HUNK},
        touched={REGEN: (OWNED, "src/x.py")},
    )
    assert result == ("resolution", "resolved")


def test_an_unrecorded_sha_is_an_own_commit_whatever_it_touches():
    result, _, _ = chain(
        MERGE_STEP,
        Commit(OWN, (MERGE,)),
        reintegrations=(REGEN,),
        resolutions={OWN: OWNED_HUNK},
        touched={OWN: (OWNED,)},
    )
    assert result == ("resolution", "resolved")


def test_a_clean_merge_alone_is_carried_clean_and_reads_no_docs():
    result, workspace, stack = chain(MERGE_STEP)
    assert result == ("carried", "clean")
    assert workspace.touched_reads == [] and stack.docs_reads == []


def test_a_merge_resolved_wholly_under_docs_paths_is_carried_regenerated_at_the_base():
    result, _, stack = chain(MERGE_STEP, resolutions={MERGE: OWNED_HUNK})
    assert result == ("carried", "regenerated")
    assert stack.docs_reads == [BASE]


def test_a_branch_widening_docs_paths_in_its_own_stack_does_not_carry():
    # The head's stack would own `src/**`; only the task base's is read, so the hunk stays
    # a resolution (a read at HEAD would raise KeyError here).
    stack = StackAt({BASE: DOCS, HEAD: Docs("make docs", ("src/**",))})
    result, _, stack = chain(MERGE_STEP, resolutions={MERGE: CODE_HUNK}, stack=stack)
    assert result == ("resolution", "resolved")
    assert stack.docs_reads == [BASE]


def test_a_product_without_docs_never_carries_a_resolution():
    result, _, _ = chain(MERGE_STEP, resolutions={MERGE: OWNED_HUNK}, stack=StackAt({BASE: None}))
    assert result == ("resolution", "resolved")


def test_a_git_error_reading_docs_is_full_unreadable():
    stack = StackAt({BASE: GitError("show failed")})
    result, _, _ = chain(MERGE_STEP, resolutions={MERGE: OWNED_HUNK}, stack=stack)
    assert result == ("full", "unreadable")


def test_a_git_error_reading_the_paths_of_a_commit_is_full_unreadable():
    result, _, _ = chain(MERGE_STEP, REGEN_STEP, reintegrations=(REGEN,), touched_fails=REGEN)
    assert result == ("full", "unreadable")
