"""The reaper's pure rules (GH-282, ADR-0052): the mark, how it is parsed, and which
environments a scope matches. Every case is plain bytes."""

from __future__ import annotations

from pathlib import Path

from agentic.core.process.domain import reaping
from agentic.core.process.domain.reaping import ReapScope, matches, parse_mark

ROOT = Path(__file__).resolve().parents[4]


def environ(*entries: str) -> bytes:
    return b"".join(entry.encode() + b"\0" for entry in entries)


def marked(value: str, *others: str) -> bytes:
    return environ("PATH=/usr/bin", f"{reaping.MARK_NAME}={value}", *others)


def test_the_mark_round_trips_through_parse():
    value = reaping.mark("abc123def456", "GH-12", 4321, "tok")
    assert value == "abc123def456/GH-12/4321/tok"
    parsed = parse_mark(value)
    assert parsed is not None
    assert (parsed.product, parsed.task, parsed.run, parsed.token) == (
        "abc123def456",
        "GH-12",
        4321,
        "tok",
    )


def test_a_value_that_is_not_a_mark_does_not_parse():
    assert parse_mark("") is None
    assert parse_mark("a/b/c") is None
    assert parse_mark("a/b/notanumber/d") is None
    assert parse_mark("a/b/1/d/extra") is None
    assert parse_mark("/b/1/d") is None


def test_a_task_scope_matches_the_task_and_its_subtasks_only():
    scope = ReapScope(product="k", task="GH-12", run=None, token=None)
    assert matches(marked("k/GH-12/1/t"), scope)
    assert matches(marked("k/GH-12.sub/2/t"), scope)
    assert not matches(marked("k/GH-1/3/t"), scope)
    assert not matches(marked("k/GH-123/3/t"), scope)
    assert not matches(marked("k2/GH-12/4/t"), scope)


def test_a_run_scope_matches_only_its_own_pid():
    scope = ReapScope(product="k", task="GH-12", run=7, token=None)
    assert matches(marked("k/GH-12/7/t"), scope)
    assert matches(marked("k/GH-12.sub/7/t"), scope)
    assert not matches(marked("k/GH-12/8/t"), scope)
    assert not matches(marked("k/GH-12/77/t"), scope)


def test_a_token_scope_matches_only_its_own_attempt():
    scope = ReapScope(product="k", task="GH-12", run=7, token="aa")
    assert matches(marked("k/GH-12/7/aa"), scope)
    assert not matches(marked("k/GH-12/7/ab"), scope)
    assert not matches(marked("k/GH-12/7/aaa"), scope)


def test_a_mark_inside_another_variable_does_not_match():
    scope = ReapScope(product="k", task="GH-12", run=None, token=None)
    other = environ("PATH=/usr/bin", f"NOTES={reaping.MARK_NAME}=k/GH-12/1/t")
    assert not matches(other, scope)
    lookalike = environ(f"X_{reaping.MARK_NAME}=k/GH-12/1/t", f"{reaping.MARK_NAME}X=k/GH-12/1/t")
    assert not matches(lookalike, scope)


def test_an_environment_without_the_mark_or_with_a_broken_one_does_not_match():
    scope = ReapScope(product="k", task="GH-12", run=None, token=None)
    assert not matches(b"", scope)
    assert not matches(environ("PATH=/usr/bin"), scope)
    assert not matches(marked("garbage"), scope)
    assert not matches(b"\xff\xfe=\0no equals sign\0", scope)


def test_a_command_is_joined_decoded_and_cut():
    assert reaping.truncate_command(b"uv\0run\0sample_app.py\0") == "uv run sample_app.py"
    assert reaping.truncate_command(b"echo\0\xff\0") == "echo �"
    long = ("x" * 300).encode()
    assert len(reaping.truncate_command(long + b"\0")) == reaping.COMMAND_MAX == 120
    assert reaping.truncate_command(b"") == ""


def test_the_product_key_is_the_first_twelve_hex_of_the_sha256_and_stable():
    key = reaping.product_key("/work/apostrophe")
    assert key == reaping.product_key("/work/apostrophe")
    assert len(key) == 12 and all(c in "0123456789abcdef" for c in key)
    assert key != reaping.product_key("/work/other")
    import hashlib

    assert key == hashlib.sha256(b"/work/apostrophe").hexdigest()[:12]


def mark_literal_violations(files: dict[str, str], owner: str) -> list[str]:
    """The files, other than `owner`, whose text holds the mark's name."""
    return sorted(
        path for path, text in files.items() if path != owner and reaping.MARK_NAME in text
    )


# Python sources only: the affected-tests guard (ADR-0047) covers a walk of `agentic/*.py`,
# and no prompt or data file under `agentic/` has any reason to name the variable.
def test_the_mark_name_is_written_in_one_module_of_the_application():
    files = {
        str(path.relative_to(ROOT)): path.read_text(encoding="utf-8")
        for path in (ROOT / "agentic").rglob("*.py")
    }
    owner = "agentic/core/process/domain/reaping.py"
    assert owner in files
    assert mark_literal_violations(files, owner) == []


def test_the_literal_check_refuses_a_second_writer():
    files = {
        "a/reaping.py": "MARK_NAME = 'ROLE_PROCESS_OWNER'",
        "a/runner.py": "x ROLE_PROCESS_OWNER",
    }
    assert mark_literal_violations(files, "a/reaping.py") == ["a/runner.py"]


W = "/p/.agentic/worktrees"


def test_a_cwd_inside_a_root_is_within_it_whole_segments_only():
    roots = [f"{W}/GH-7"]
    assert reaping.cwd_within(f"{W}/GH-7", roots)
    assert reaping.cwd_within(f"{W}/GH-7/web", roots)
    assert reaping.cwd_within(f"{W}/GH-7/web/node_modules/.bin", roots)


def test_a_cwd_outside_every_root_is_not_within_any():
    roots = [f"{W}/GH-7"]
    assert not reaping.cwd_within(f"{W}/GH-70", roots), "a string prefix is not a segment"
    assert not reaping.cwd_within(f"{W}/GH-70/web", roots)
    assert not reaping.cwd_within(W, roots), "the parent is not inside the child"
    assert not reaping.cwd_within("/p", roots)
    assert not reaping.cwd_within("relative/GH-7", roots)


def test_any_of_several_roots_takes_the_cwd():
    assert reaping.cwd_within(f"{W}/GH-8/x", [f"{W}/GH-7", f"{W}/GH-8"])


def test_a_deleted_cwd_is_never_within_a_root():
    assert not reaping.cwd_within(f"{W}/GH-7 (deleted)", [f"{W}/GH-7"])
    assert not reaping.cwd_within(f"{W}/GH-7/web (deleted)", [f"{W}/GH-7"])


def test_no_roots_take_no_cwd():
    assert not reaping.cwd_within(f"{W}/GH-7", [])
    assert not reaping.cwd_within(f"{W}/GH-7", [""])


def test_the_check_scope_is_a_task_segment_no_task_id_can_be():
    assert reaping.CHECK_TASK == "check"
    value = reaping.mark("abc123def456", reaping.CHECK_TASK, 9, "uuid")
    assert matches(marked(value), ReapScope("abc123def456", reaping.CHECK_TASK, 9, None))
    assert not matches(marked(value), ReapScope("abc123def456", "GH-1", None, None))


def test_an_environment_is_marked_with_any_value_and_not_otherwise():
    assert reaping.is_marked(marked("whatever"))
    assert reaping.is_marked(marked(""))
    assert reaping.is_marked(marked(reaping.mark("abc", "GH-1", 1, "t")))
    assert not reaping.is_marked(environ("PATH=/usr/bin"))
    assert not reaping.is_marked(b"")
    assert not reaping.is_marked(environ(f"X{reaping.MARK_NAME}=1", f"{reaping.MARK_NAME}X=1"))


def test_the_perf_scope_is_a_task_segment_no_task_id_can_be():
    assert reaping.PERF_TASK == "perf"
    value = reaping.mark("abc123def456", reaping.PERF_TASK, 9, "uuid")
    assert reaping.parse_mark(value) == ("abc123def456", "perf", 9, "uuid")
    own = ReapScope("abc123def456", reaping.PERF_TASK, 9, "uuid")
    assert matches(marked(value), own)
    assert not matches(marked(value), ReapScope("abc123def456", "GH-1", None, None))
    assert not matches(marked(value), ReapScope("abc123def456", reaping.CHECK_TASK, None, None))
    assert not matches(marked(value), ReapScope("abc123def456", reaping.PERF_TASK, 8, None))
    assert not matches(marked(value), ReapScope("abc123def456", reaping.PERF_TASK, 9, "other"))
    check = reaping.mark("abc123def456", reaping.CHECK_TASK, 9, "uuid")
    assert not matches(marked(check), own)
