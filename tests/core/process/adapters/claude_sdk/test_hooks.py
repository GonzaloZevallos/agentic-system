"""The hooks' decisions as pure functions, then wired as the SDK takes them. No model."""

import re
from dataclasses import replace
from pathlib import Path

import anyio
import pytest

from agentic.core.process.adapters.claude_sdk import hooks
from agentic.core.process.application.ports import RunStopped
from agentic.core.process.domain import turn_notice
from agentic.core.process.domain.roles import (
    DEVELOPER,
    DEVELOPER_INTEGRATOR,
    PJM,
    TECH_LEAD_REVIEWER,
)
from agentic.core.process.domain.suppressions import Forbidden

pytestmark = pytest.mark.anyio

CWD = Path("/repo/.agentic/worktrees/GH-12.api")

# A product-declared rule, generic on purpose: the system knows no language of its own
# (issue #38), so the hooks' own tests must not either.
SUPPRESS = Forbidden(
    pattern=re.compile(r"SUPPRESS\b"), extensions=frozenset({".src"}), reason="do not suppress"
)


@pytest.mark.parametrize(
    "command",
    [
        "git config user.name me",
        "git config --global user.email me@x",
        "git config set user.name me",
        "git push",
        "git push -u origin HEAD",
        "git worktree add ../x",
        "git worktree list",
        "git -C /repo config user.name me",
        "git -C . -c core.pager=cat config user.name me",
        "git --git-dir=/repo/.git log",
        "git --git-dir /repo/.git status",
        "git -C /elsewhere commit -m x",
        "git commit --no-verify -m x",
        "git commit -n -m x",
        "git commit -anm x",
        "git checkout main",
        "git checkout -b other",
        "git switch main",
        "uv run pytest && git push",
        "cd sub; git config user.name me",
        "echo $(git config user.name me)",
        "FOO=1 git push",
        "/usr/bin/git push",
        "bash -c 'git push'",
        'sh -c "git config user.name me"',
        "bash -lc 'git push'",
        "git commit -S -n -m x",
        "git commit --no-verif -m x",
        "git -c core.hooksPath=/dev/null commit -m x",
        "git -c user.email=me@x commit -m x",
        "GIT_DIR=/repo/.git git log",
        "cat <<<foo\ngit push",
    ],
)
def test_git_denial_refuses_what_the_developer_may_never_do(command):
    assert hooks.git_denial(command, CWD)


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git diff HEAD~1",
        "git log --oneline",
        "git add -A && git commit -m 'why it changed'",
        "git commit -m 'no -n here' -a",
        "git commit -F msg.txt",
        "git config --get user.name",
        "git config --list",
        "git config get user.name",
        "git -C . status",
        "git -C sub log",
        "git checkout -- app/x.py",
        "git checkout HEAD~1 -- app/x.py",
        "git restore app/x.py",
        "uv run pytest -q",
        "echo 'git push is not allowed'",
        "grep -rn 'git config' docs",
        "git commit -m \"$(cat <<'EOF'\nDon't push: it isn't allowed\nEOF\n)\"",
        "echo 'unbalanced",
        "git commit -Skey -m x",
        "git -c core.pager=cat log",
    ],
)
def test_git_denial_lets_the_everyday_commands_through(command):
    assert hooks.git_denial(command, CWD) is None


def test_a_nested_shell_is_denied_once():
    assert hooks.git_denial("sh -c 'git push'", CWD).count("Denied by the system") == 1


def test_a_git_denial_says_what_to_do_instead():
    assert "git checkout -- <path>" in hooks.git_denial("git checkout main", CWD)
    assert "signed as the system" in hooks.git_denial("git config user.name me", CWD)


@pytest.mark.parametrize(
    "file_path",
    [
        "/repo/.git/config",
        ".git/hooks/pre-commit",
        "../GH-12.domain/app/x.py",
        "/tmp/scratch.py",
        "app/../../x.py",
    ],
)
def test_path_denial_keeps_edits_inside_the_checkout_and_out_of_git(file_path):
    assert hooks.path_denial(file_path, CWD)


@pytest.mark.parametrize("file_path", ["app/x.py", f"{CWD}/tests/test_x.py", "docs/.gitkeep"])
def test_path_denial_allows_the_checkout(file_path):
    assert hooks.path_denial(file_path, CWD) is None


def test_forbidden_denial_refuses_a_new_occurrence_of_a_declared_rule():
    reason = hooks.forbidden_denial("app/x.src", "keep\n", "keep  SUPPRESS\n", [SUPPRESS])
    assert reason and "do not suppress" in reason and reason.startswith("Denied by the system")


@pytest.mark.parametrize(
    ("file_path", "before", "after"),
    [
        ("app/x.src", "keep\n", "keep\n"),
        # One that was already there is not the task's: the standards apply to new code.
        ("app/x.src", "a  SUPPRESS\n", "a  SUPPRESS\nb\n"),
        # No rule applies to this extension: nothing to deny.
        ("docs/standards.md", "", "SUPPRESS\n"),
    ],
)
def test_forbidden_denial_allows_the_rest(file_path, before, after):
    assert hooks.forbidden_denial(file_path, before, after, [SUPPRESS]) is None


def test_forbidden_denial_with_no_declared_rules_denies_nothing():
    assert hooks.forbidden_denial("app/x.src", "", "SUPPRESS\n", []) is None


async def call(matchers, event, payload):
    """Every callback of `event` whose matcher takes the tool, as the CLI would."""
    outputs = []
    for matcher in matchers.get(event, []):
        tool = payload.get("tool_name", "")
        if matcher.matcher is None or tool in matcher.matcher.split("|"):
            for callback in matcher.hooks:
                outputs.append(await callback(payload, "t1", {"signal": None}))
    return outputs


def denied(outputs) -> list[str]:
    return [
        o["hookSpecificOutput"]["permissionDecisionReason"]
        for o in outputs
        if o.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    ]


def test_only_a_role_that_executes_gets_the_cage():
    assert hooks.hooks_for(PJM, CWD, None) == {}
    assert hooks.hooks_for(TECH_LEAD_REVIEWER, CWD, None) == {}
    matchers = hooks.hooks_for(DEVELOPER, CWD, None)
    assert {m.matcher for m in matchers["PreToolUse"]} == {"Bash", "Edit|Write"}
    assert all(m.timeout for m in matchers["PreToolUse"])


async def test_the_bash_hook_denies_with_the_reason():
    matchers = hooks.hooks_for(DEVELOPER, CWD, None)
    payload = {"tool_name": "Bash", "tool_input": {"command": "git push"}, "cwd": str(CWD)}
    assert denied(await call(matchers, "PreToolUse", payload))
    ok = {**payload, "tool_input": {"command": "git status"}}
    assert denied(await call(matchers, "PreToolUse", ok)) == []


async def test_the_edit_hook_checks_the_path_and_a_declared_suppression(tmp_path):
    matchers = hooks.hooks_for(DEVELOPER, tmp_path, None, forbidden=[SUPPRESS])
    (tmp_path / "x.src").write_text("a  SUPPRESS\n")

    async def edit(tool, **tool_input):
        payload = {"tool_name": tool, "tool_input": tool_input, "cwd": str(tmp_path)}
        return denied(await call(matchers, "PreToolUse", payload))

    assert await edit("Edit", file_path="x.src", old_string="a", new_string="b  SUPPRESS")
    assert not await edit("Edit", file_path="x.src", old_string="SUPPRESS", new_string="")
    # A Write replaces the whole file: what it had already is not new.
    assert not await edit("Write", file_path="x.src", content="a  SUPPRESS\nb\n")
    assert await edit("Write", file_path="y.src", content="b  SUPPRESS\n")
    assert await edit("Write", file_path=str(tmp_path.parent / "z.src"), content="")
    # No rule covers this extension: nothing to read against, nothing to deny.
    assert not await edit("Write", file_path="logo.png", content="SUPPRESS")
    # The path cage still answers even with a declared rule that matches nothing here.
    assert await edit("Edit", file_path="../outside.src", old_string="", new_string="")


async def test_the_edit_hook_with_no_forbidden_declared_denies_no_suppression(tmp_path):
    """`forbidden` absent (the default) is the explicit no-op: the cage still installs
    for the path rule, but denies no suppression (issue #38)."""
    matchers = hooks.hooks_for(DEVELOPER, tmp_path, None)
    (tmp_path / "x.src").write_text("a  SUPPRESS\n")

    async def edit(tool, **tool_input):
        payload = {"tool_name": tool, "tool_input": tool_input, "cwd": str(tmp_path)}
        return denied(await call(matchers, "PreToolUse", payload))

    assert not await edit("Edit", file_path="x.src", old_string="a", new_string="b  SUPPRESS")
    assert await edit("Edit", file_path="../outside.src", old_string="", new_string="")


class FakePresubmit:
    timeout_s = 120.0

    def __init__(self, *reasons):
        self.reasons = list(reasons)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.reasons.pop(0) if self.reasons else None


async def test_the_presubmit_answers_the_delivery_call():
    presubmit = FakePresubmit("suite red: `pytest`\n1 failed")
    matchers = hooks.hooks_for(DEVELOPER, CWD, presubmit)
    payload = {"tool_name": hooks.DELIVERY_TOOL, "tool_input": {"markdown": "x"}}
    assert denied(await call(matchers, "PreToolUse", payload)) == ["suite red: `pytest`\n1 failed"]
    assert denied(await call(matchers, "PreToolUse", payload)) == []
    assert presubmit.calls == 2
    (hooked,) = [m for m in matchers["PreToolUse"] if m.matcher == hooks.DELIVERY_TOOL]
    assert hooked.timeout >= presubmit.timeout_s


class FakeGuard:
    def __init__(self, *reasons):
        self.reasons = list(reasons)
        self.refusals = 0
        self.seen: list[dict] = []

    def __call__(self, tool_input):
        self.seen.append(tool_input)
        return self.reasons.pop(0) if self.reasons else None


async def test_the_guard_answers_the_delivery_call():
    guard = FakeGuard("missing `summary`, `assumptions`")
    matchers = hooks.hooks_for(DEVELOPER, CWD, None, guard=guard)
    payload = {"tool_name": hooks.DELIVERY_TOOL, "tool_input": {"markdown": "x"}}
    reasons = denied(await call(matchers, "PreToolUse", payload))
    assert reasons == ["missing `summary`, `assumptions`"]
    assert guard.seen == [{"markdown": "x"}]


def test_the_guard_and_the_presubmit_share_one_matcher():
    matchers = hooks.hooks_for(DEVELOPER, CWD, FakePresubmit(), guard=FakeGuard())["PreToolUse"]
    delivery_matchers = [m for m in matchers if m.matcher == hooks.DELIVERY_TOOL]
    assert len(delivery_matchers) == 1


async def test_the_guard_runs_before_the_presubmit_and_short_circuits_it():
    guard = FakeGuard("bad shape")
    presubmit = FakePresubmit("suite red")
    matchers = hooks.hooks_for(DEVELOPER, CWD, presubmit, guard=guard)
    payload = {"tool_name": hooks.DELIVERY_TOOL, "tool_input": {"markdown": "x"}}
    assert denied(await call(matchers, "PreToolUse", payload)) == ["bad shape"]
    assert presubmit.calls == 0  # never reached: the guard's own denial already answered


async def test_the_presubmit_still_answers_once_the_guard_lets_the_call_through():
    guard = FakeGuard()  # nothing to deny
    presubmit = FakePresubmit("suite red")
    matchers = hooks.hooks_for(DEVELOPER, CWD, presubmit, guard=guard)
    payload = {"tool_name": hooks.DELIVERY_TOOL, "tool_input": {"markdown": "x"}}
    assert denied(await call(matchers, "PreToolUse", payload)) == ["suite red"]
    assert presubmit.calls == 1


def test_a_guard_with_no_presubmit_keeps_the_bare_guard_timeout():
    matchers = hooks.hooks_for(DEVELOPER, CWD, None, guard=FakeGuard())["PreToolUse"]
    (hooked,) = [m for m in matchers if m.matcher == hooks.DELIVERY_TOOL]
    assert hooked.timeout == hooks.GUARD_TIMEOUT_S


def test_only_developer_roles_get_the_turn_notice_hook():
    # The conservative default (`domain/turn_notice.threshold_for`'s own docstring): a
    # role that executes but is not named in `TURN_NOTICE_AT` gets no hook, not even
    # one that always stays quiet.
    assert "PostToolUse" not in hooks.hooks_for(PJM, CWD, None)
    assert "PostToolUse" not in hooks.hooks_for(TECH_LEAD_REVIEWER, CWD, None)
    other_executor = replace(DEVELOPER, slug="other_executor")
    assert "PostToolUse" not in hooks.hooks_for(other_executor, CWD, None)
    assert "PostToolUse" in hooks.hooks_for(DEVELOPER, CWD, None)
    assert "PostToolUse" in hooks.hooks_for(DEVELOPER_INTEGRATOR, CWD, None)


async def test_the_turn_notice_hook_fires_at_the_first_window_and_names_it():
    # First position 45 (`turn_notice.TURN_NOTICE_AT`): a `TurnCount` already at 45 (the
    # stream loop's own write, never this hook's) fires on the very next event, with
    # window 1's own text.
    role = replace(DEVELOPER, max_turns=200)
    fired = []
    turns = hooks.TurnCount(value=45)
    matchers = hooks.hooks_for(role, CWD, None, on_notice=lambda: fired.append(1), turns=turns)
    payload = {"tool_name": "Bash", "tool_input": {}, "cwd": str(CWD)}
    (output,) = await call(matchers, "PostToolUse", payload)
    context = output["hookSpecificOutput"]["additionalContext"]
    assert context == turn_notice.notice_context(1)
    assert fired == [1]


async def test_the_turn_notice_hook_stays_quiet_on_a_second_event_still_inside_the_same_window():
    # The collapsing predicate's own point: unlike the deleted always-refiring
    # advisory, a second `PostToolUse` event whose count has not crossed a *new*
    # position must not fire again -- this is the mechanism that drops the firing count
    # from 109 to at most one per position.
    role = replace(DEVELOPER, max_turns=200)
    turns = hooks.TurnCount(value=45)
    matchers = hooks.hooks_for(role, CWD, None, turns=turns)
    payload = {"tool_name": "Bash", "tool_input": {}, "cwd": str(CWD)}
    (first,) = await call(matchers, "PostToolUse", payload)
    turns.value = 50  # still inside window 1 (45..64), no new position crossed
    (second,) = await call(matchers, "PostToolUse", payload)
    assert first["hookSpecificOutput"]["additionalContext"] == turn_notice.notice_context(1)
    assert second == {}


async def test_the_turn_notice_hook_fires_again_when_the_second_window_is_reached():
    role = replace(DEVELOPER, max_turns=200)
    turns = hooks.TurnCount(value=45)
    matchers = hooks.hooks_for(role, CWD, None, turns=turns)
    payload = {"tool_name": "Bash", "tool_input": {}, "cwd": str(CWD)}
    (first,) = await call(matchers, "PostToolUse", payload)
    turns.value = 65
    (second,) = await call(matchers, "PostToolUse", payload)
    assert first["hookSpecificOutput"]["additionalContext"] == turn_notice.notice_context(1)
    assert second["hookSpecificOutput"]["additionalContext"] == turn_notice.notice_context(2)


async def test_the_turn_notice_hook_emits_one_message_when_both_windows_cross_at_once():
    # The invariant this seam has to prove: a count that jumps from before the first
    # position straight past the second (a text-only turn or a sub-agent tail can do
    # this between two consecutive main-thread tool results) still gets one message,
    # not two spent back to back on the same pair of events.
    role = replace(DEVELOPER, max_turns=200)
    fired = []
    turns = hooks.TurnCount(value=70)
    matchers = hooks.hooks_for(role, CWD, None, on_notice=lambda: fired.append(1), turns=turns)
    payload = {"tool_name": "Bash", "tool_input": {}, "cwd": str(CWD)}
    (output,) = await call(matchers, "PostToolUse", payload)
    assert output["hookSpecificOutput"]["additionalContext"] == turn_notice.notice_context(2)
    assert fired == [1]  # one firing, not two, even though both positions were crossed
    (second,) = await call(matchers, "PostToolUse", payload)
    assert second == {}  # both windows already spent by the one call above


async def test_the_turn_notice_hook_still_ignores_a_sub_agents_own_event():
    # The counter itself no longer needs the `agent_id` filter to stay main-thread-only
    # (`parent_tool_use_id is None` already guarantees that on the runner's side), but
    # firing still checks it: a sub-agent's own `PostToolUse` is not this role's turn,
    # and injecting `additionalContext` there would land in the sub-agent's own
    # conversation, not the one the close-out is meant to reach -- even with the shared
    # counter already past the first window, this must stay quiet.
    role = replace(DEVELOPER, max_turns=200)
    turns = hooks.TurnCount(value=45)
    matchers = hooks.hooks_for(role, CWD, None, turns=turns)
    payload = {"tool_name": "Bash", "tool_input": {}, "cwd": str(CWD), "agent_id": "sub-1"}
    assert await call(matchers, "PostToolUse", payload) == [{}]


async def test_the_turn_notice_hook_is_quiet_before_the_first_threshold():
    role = replace(DEVELOPER, max_turns=200)
    turns = hooks.TurnCount(value=44)
    matchers = hooks.hooks_for(role, CWD, None, turns=turns)
    payload = {"tool_name": "Bash", "tool_input": {}, "cwd": str(CWD)}
    assert await call(matchers, "PostToolUse", payload) == [{}]


async def test_the_turn_notice_hook_with_no_counter_given_never_fires():
    # `turns=None` (`hooks_for`'s own docstring): the conservative default for every
    # other call this module's tests make that is not about the notice itself.
    role = replace(DEVELOPER, max_turns=40)
    matchers = hooks.hooks_for(role, CWD, None)
    payload = {"tool_name": "Bash", "tool_input": {}, "cwd": str(CWD)}
    assert await call(matchers, "PostToolUse", payload) == [{}]


class StoppedPresubmit(FakePresubmit):
    """The run was told to stop while the gate's checks ran."""

    def __call__(self):
        self.calls += 1
        raise RunStopped


async def test_a_stop_in_the_gate_returns_no_decision_and_waits_for_the_cancellation():
    presubmit = StoppedPresubmit()
    matchers = hooks.hooks_for(DEVELOPER, CWD, presubmit)
    payload = {"tool_name": hooks.DELIVERY_TOOL, "tool_input": {"markdown": "x"}}
    outputs: list = []
    with anyio.move_on_after(0.3) as scope:
        outputs = await call(matchers, "PreToolUse", payload)
    assert scope.cancelled_caught  # never returned: neither a deny nor an allow for the SDK
    assert outputs == []
    assert presubmit.calls == 1


# -- the attempt's own scratch and its task's evidence folder (GH-391) -----------------


class _Layout:
    """A product root with an attempt's worktree, scratch and kept folder on a real disk."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.cwd = root / ".agentic/worktrees/GH-1.b"
        self.scratch = root / ".agentic/tmp/GH-1/r-aaaaaaaa"
        self.kept = root / ".agentic/kept/GH-1"
        self.store = root / ".agentic/check-results"
        for directory in (self.cwd, self.scratch, self.kept, self.store):
            directory.mkdir(parents=True)

    @property
    def allowed(self) -> tuple[Path, Path]:
        return (self.scratch, self.kept)


@pytest.fixture
def layout(tmp_path) -> _Layout:
    return _Layout(tmp_path / "product")


def test_path_denial_allows_the_scratch_and_the_evidence_folder_it_is_given(layout):
    for path in (layout.kept / "GH-1.b-home.png", layout.scratch / "y", layout.scratch / "a/b/c"):
        assert hooks.path_denial(str(path), layout.cwd, allowed=layout.allowed) is None
    # Relative to where the call runs, as a path in the checkout is.
    assert (
        hooks.path_denial("../../tmp/GH-1/r-aaaaaaaa/z", layout.cwd, allowed=layout.allowed) is None
    )


@pytest.mark.parametrize(
    "relative",
    [
        ".agentic/kept/GH-2/x.png",
        ".agentic/kept/GH-1x/x.png",
        ".agentic/tmp/GH-1/r-bbbbbbbb/y",
        ".agentic/tmp/GH-1/r-aaaaaaaa.json",
        ".agentic/tmp/GH-2/r-aaaaaaaa/y",
        ".agentic/worktrees/GH-1.other/x.py",
        ".agentic/tmp/GH-1/r-aaaaaaaa/../r-bbbbbbbb/y",
        ".agentic/kept/GH-1/../GH-2/x.png",
        "other.py",
    ],
)
def test_path_denial_still_refuses_everything_else_outside_the_checkout(layout, relative):
    reason = hooks.path_denial(str(layout.root / relative), layout.cwd, allowed=layout.allowed)
    assert reason and hooks.OUTSIDE_PATH in reason


def test_path_denial_refuses_a_symlink_that_leaves_the_allowed_directory(layout):
    (layout.scratch / "out").symlink_to(layout.root / ".agentic/kept")
    (layout.kept / "back").symlink_to(layout.cwd)
    for path in (layout.scratch / "out/GH-2/x.png", layout.kept / "back/f"):
        reason = hooks.path_denial(str(path), layout.cwd, allowed=layout.allowed)
        assert reason and hooks.OUTSIDE_PATH in reason


def test_path_denial_without_an_allowance_is_the_guard_it_always_was(layout):
    for path in (layout.scratch / "y", layout.kept / "x.png"):
        assert hooks.OUTSIDE_PATH in str(hooks.path_denial(str(path), layout.cwd))
        assert hooks.OUTSIDE_PATH in str(hooks.path_denial(str(path), layout.cwd, allowed=()))
    assert hooks.path_denial("app/x.py", layout.cwd, allowed=layout.allowed) is None


def test_git_internals_stay_denied_inside_an_allowed_directory(layout):
    reason = hooks.path_denial(str(layout.kept / ".git/config"), layout.cwd, allowed=layout.allowed)
    assert reason and hooks.GIT_PATH in reason


async def test_the_edit_hook_allows_exactly_the_allowance_and_the_store_still_wins(layout):
    matchers = hooks.hooks_for(
        DEVELOPER, layout.cwd, None, store=layout.store, allowed=layout.allowed
    )

    async def edit(tool, path):
        payload = {
            "tool_name": tool,
            "tool_input": {"file_path": str(path), "content": "x", "new_string": "x"},
            "cwd": str(layout.cwd),
        }
        return denied(await call(matchers, "PreToolUse", payload))

    for tool in ("Write", "Edit"):
        assert await edit(tool, layout.kept / "x.png") == []
        assert await edit(tool, layout.scratch / "y") == []
        assert hooks.OUTSIDE_PATH in (await edit(tool, layout.root / ".agentic/kept/GH-2/x.png"))[0]
        assert await edit(tool, layout.root / ".agentic/worktrees/GH-1.o/x.py")
    # A store that lives inside an allowed directory is still not ours to touch.
    inside = layout.kept / "results"
    inside.mkdir()
    matchers = hooks.hooks_for(DEVELOPER, layout.cwd, None, store=inside, allowed=layout.allowed)
    reasons = await edit("Write", inside / "k.json")
    assert reasons and all(hooks.STORE_PATH in reason for reason in reasons)
