"""The hooks' decisions as pure functions, then wired as the SDK takes them. No model."""

from pathlib import Path

import pytest

from agentic.core.process.adapters.claude_sdk import hooks
from agentic.core.process.domain.roles import DEVELOPER, PJM, TECH_LEAD_REVIEWER

pytestmark = pytest.mark.anyio

CWD = Path("/repo/.agentic/worktrees/GH-12.api")


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


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("x = f()\n", "x = f()  # type: ignore\n"),
        ("x = f()\n", "x = f()  # type: ignore[arg-type]\n"),
        ("import os\n", "import os  # noqa: F401\n"),
        ("", "import os  #noqa\n"),
    ],
)
def test_silenced_denial_refuses_a_new_suppression(before, after):
    reason = hooks.silenced_denial("app/x.py", before, after)
    assert reason and "cast" in reason


@pytest.mark.parametrize(
    ("file_path", "before", "after"),
    [
        ("app/x.py", "x = f()\n", "x: int = f()\n"),
        # One that was already there is not the task's: the standards apply to new code.
        ("app/x.py", "a = f()  # type: ignore\n", "a = f()  # type: ignore\nb = 1\n"),
        # Only Python silences the Python checkers.
        ("docs/standards.md", "", "Never write `# type: ignore`.\n"),
    ],
)
def test_silenced_denial_allows_the_rest(file_path, before, after):
    assert hooks.silenced_denial(file_path, before, after) is None


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


async def test_the_edit_hook_checks_the_path_and_the_new_text(tmp_path):
    matchers = hooks.hooks_for(DEVELOPER, tmp_path, None)
    (tmp_path / "x.py").write_text("a = 1  # type: ignore\n")

    async def edit(tool, **tool_input):
        payload = {"tool_name": tool, "tool_input": tool_input, "cwd": str(tmp_path)}
        return denied(await call(matchers, "PreToolUse", payload))

    assert await edit("Edit", file_path="x.py", old_string="a = 1", new_string="a = 2  # noqa")
    assert not await edit("Edit", file_path="x.py", old_string="# type: ignore", new_string="")
    # A Write replaces the whole file: what it had already is not new.
    assert not await edit("Write", file_path="x.py", content="a = 1  # type: ignore\nb = 2\n")
    assert await edit("Write", file_path="y.py", content="b = f()  # type: ignore\n")
    assert await edit("Write", file_path=str(tmp_path.parent / "z.py"), content="")
    # A binary file on disk is not Python: nothing to read, nothing to deny.
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\xff\xfe")
    assert not await edit("Write", file_path="logo.png", content="x")


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
