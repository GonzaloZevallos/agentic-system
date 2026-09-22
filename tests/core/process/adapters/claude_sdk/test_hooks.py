"""The hooks' decisions as pure functions, then wired as the SDK takes them. No model."""

import re
from dataclasses import replace
from pathlib import Path

import pytest

from agentic.core.process.adapters.claude_sdk import hooks
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
    # The conservative default (`domain/turn_notice.margin_for`'s own docstring): a
    # role that executes but is not named in `TURN_NOTICE_MARGIN_TURNS` gets no hook,
    # not even one that always stays quiet.
    assert "PostToolUse" not in hooks.hooks_for(PJM, CWD, None)
    assert "PostToolUse" not in hooks.hooks_for(TECH_LEAD_REVIEWER, CWD, None)
    other_executor = replace(DEVELOPER, slug="other_executor")
    assert "PostToolUse" not in hooks.hooks_for(other_executor, CWD, None)
    assert "PostToolUse" in hooks.hooks_for(DEVELOPER, CWD, None)
    assert "PostToolUse" in hooks.hooks_for(DEVELOPER_INTEGRATOR, CWD, None)


async def test_the_turn_notice_hook_fires_once_past_the_margin():
    # Margin 39 (`turn_notice.TURN_NOTICE_MARGIN_TURNS`): with `max_turns=40` the very
    # first main-thread call already crosses it (40 - 1 == 39 remaining).
    role = replace(DEVELOPER, max_turns=40)
    fired = []
    matchers = hooks.hooks_for(role, CWD, None, on_notice=lambda: fired.append(1))
    payload = {"tool_name": "Bash", "tool_input": {}, "cwd": str(CWD)}
    (output,) = await call(matchers, "PostToolUse", payload)
    context = output["hookSpecificOutput"]["additionalContext"]
    assert "39 turns left" in context
    assert fired == [1]
    # One notice per attempt: a later call, still past the margin, adds nothing more.
    assert await call(matchers, "PostToolUse", payload) == [{}]
    assert fired == [1]


async def test_the_turn_notice_hook_ignores_sub_agent_traffic():
    role = replace(DEVELOPER, max_turns=40)
    matchers = hooks.hooks_for(role, CWD, None)
    payload = {"tool_name": "Bash", "tool_input": {}, "cwd": str(CWD), "agent_id": "sub-1"}
    # A sub-agent's own call never counts towards the enforced cap: no notice fires,
    # even though the same call on the main thread would have crossed the margin.
    assert await call(matchers, "PostToolUse", payload) == [{}]


async def test_the_turn_notice_hook_is_quiet_before_the_margin():
    role = replace(DEVELOPER, max_turns=100)  # margin only at hook_turns 61
    matchers = hooks.hooks_for(role, CWD, None)
    payload = {"tool_name": "Bash", "tool_input": {}, "cwd": str(CWD)}
    assert await call(matchers, "PostToolUse", payload) == [{}]
