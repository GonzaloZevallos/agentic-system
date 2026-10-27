"""GH-353: when what master gained since a recorded check pass cannot change its result
(`domain/master_gain.py`, ADR-0045 addendum). Pure functions, no I/O."""

from __future__ import annotations

import pytest

from agentic.core.process.domain import master_gain as mg

SETUP = ["uv sync"]
CURRENT = "c" * 40
OLD = "a" * 40
OLDER = "b" * 40


def test_matches_keeps_star_and_question_within_one_segment():
    assert mg.matches("docs/*.md", "docs/x.md")
    assert not mg.matches("docs/*.md", "docs/a/b.md")
    assert mg.matches("docs/?.md", "docs/a.md")
    assert not mg.matches("docs/?.md", "docs/ab.md")
    assert mg.matches("docs/[ab].md", "docs/a.md")
    assert not mg.matches("docs/[ab].md", "docs/c.md")


def test_matches_double_star_as_a_whole_segment_is_zero_or_more_segments():
    assert mg.matches("docs/**", "docs/a/b.md")
    assert mg.matches("docs/**", "docs/x")
    assert mg.matches("docs/**/x.md", "docs/x.md")
    assert mg.matches("docs/**/x.md", "docs/a/b/x.md")
    assert not mg.matches("docs/**", "src/docs/x")
    assert not mg.matches("docs/**/x.md", "docs/a/y.md")


def test_matches_is_case_sensitive_and_normalises_the_path():
    assert not mg.matches("docs/*.md", "DOCS/x.md")
    assert mg.matches("docs/*.md", "./docs//x.md")
    assert not mg.matches("docs/*.md", "docs/../src/x.md")


def test_a_double_star_inside_a_segment_is_an_ordinary_star():
    assert mg.matches("docs/a**b", "docs/axxb")
    assert not mg.matches("docs/a**b", "docs/ax/xb")


@pytest.mark.parametrize(
    ("glob", "reason"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("/docs/x", "absolute"),
        ("docs/../src/x", "parent"),
        ("../x", "parent"),
        ("docs\\x", "backslash"),
        ("**", "wildcard_root"),
        ("*", "wildcard_root"),
        ("*.md", "wildcard_root"),
        ("**/x", "wildcard_root"),
        ("./**", "wildcard_root"),
        ("?x/y", "wildcard_root"),
        ("[a]x/y", "wildcard_root"),
    ],
)
def test_glob_refusal_names_the_closed_reason(glob, reason):
    assert mg.glob_refusal(glob) == reason


@pytest.mark.parametrize("glob", ["docs/**", ".agentic/tasks/*.md", "docs/*.md", "a/b/c.txt"])
def test_glob_refusal_accepts_a_rooted_glob(glob):
    assert mg.glob_refusal(glob) is None


@pytest.mark.parametrize(
    "path",
    [
        ".agentic/stack.yaml",
        ".agentic/foo.yaml",
        ".agentic/index/standards.md",
        ".agentic/tasks/a/b.md",
        ".agentic/tasks/GH-1.txt",
        ".github/workflows/ci.yml",
        ".github/CODEOWNERS",
        "CODING_STANDARDS.md",
        "./.agentic/stack.yaml",
        "docs/../.github/x",
        "../outside",
        "/etc/passwd",
    ],
)
def test_never_inert_paths_are_not_inert_even_under_a_broad_glob(path):
    assert not mg.gain_inert((path,), ["**"], SETUP)
    assert not mg.gain_inert((path,), [path], SETUP)
    assert not mg.gain_inert((path,), [".agentic/**", ".github/**", "CODING_STANDARDS.md"], SETUP)


def test_a_task_document_is_inert_only_when_a_glob_declares_it():
    assert mg.gain_inert((".agentic/tasks/GH-1.md",), [".agentic/tasks/*.md"], SETUP)
    assert not mg.gain_inert((".agentic/tasks/GH-1.md",), ["docs/**"], SETUP)


@pytest.mark.parametrize(
    "path",
    [
        "uv.lock",
        "pyproject.toml",
        "web/package-lock.json",
        "apps/api/requirements-dev.txt",
        "deep/er/go.sum",
    ],
)
def test_a_lock_or_manifest_at_any_depth_is_never_inert(path):
    assert not mg.gain_inert((path,), ["**/*", "web/**", "apps/**", "deep/**"], SETUP)
    assert not mg.gain_inert((path,), [path], SETUP)


def test_a_path_a_setup_command_names_is_never_inert():
    assert not mg.gain_inert(
        ("scripts/bootstrap.sh",), ["scripts/**"], ["bash scripts/bootstrap.sh"]
    )
    assert mg.gain_inert(("scripts/bootstrap.sh",), ["scripts/**"], SETUP)


def test_gain_inert_needs_every_path_to_match_a_declared_glob():
    globs = ["docs/**", ".agentic/tasks/*.md"]
    assert mg.gain_inert(("docs/a.md", "docs/b/c.md", ".agentic/tasks/GH-2.md"), globs, SETUP)
    assert not mg.gain_inert(("docs/a.md", "src/x.py"), globs, SETUP)


def test_a_rename_gain_listing_both_sides_is_not_inert_when_one_side_is_live():
    assert not mg.gain_inert(("src/x.py", "docs/x.py"), ["docs/**"], SETUP)
    assert mg.gain_inert(("docs/old.md", "docs/new.md"), ["docs/**"], SETUP)


def test_gain_inert_is_false_with_no_paths_or_no_globs():
    assert not mg.gain_inert((), ["docs/**"], SETUP)
    assert not mg.gain_inert(("docs/a.md",), [], SETUP)


def _covered(**over):
    args = {
        "current": CURRENT,
        "row_masters": [OLD],
        "gains": {OLD: ("docs/a.md",)},
        "globs": ["docs/**"],
        "setup_commands": SETUP,
        "task_touched": ("src/x.py",),
    }
    args.update(over)
    return mg.masters_covered(**args)


def test_masters_covered_is_true_when_every_differing_gain_is_inert():
    assert _covered() is True
    assert (
        _covered(
            row_masters=[OLD, OLDER, CURRENT],
            gains={OLD: ("docs/a.md",), OLDER: ("docs/b.md", "docs/c/d.md")},
        )
        is True
    )


def test_masters_covered_is_none_when_no_row_master_differs():
    assert _covered(row_masters=[CURRENT], gains={}) is None
    assert _covered(row_masters=[], gains={}) is None
    assert _covered(row_masters=[CURRENT, CURRENT], gains={}, globs=[]) is None


@pytest.mark.parametrize(
    "over",
    [
        {"globs": []},
        {"task_touched": None},
        {"task_touched": (".agentic/stack.yaml", "src/x.py")},
        {"task_touched": ("./.agentic/stack.yaml",)},
        {"gains": {OLD: None}},
        {"gains": {OLD: ()}},
        {"gains": {}},
        {"gains": {OLD: ("src/x.py",)}},
        {"gains": {OLD: ("docs/a.md", "uv.lock")}},
        {"gains": {OLD: ("docs/a.md",), OLDER: None}, "row_masters": [OLD, OLDER]},
        {"gains": {OLD: ("docs/a.md",), OLDER: ("src/x.py",)}, "row_masters": [OLD, OLDER]},
        {"gains": {OLD: ("docs/a.md",), OLDER: ()}, "row_masters": [OLD, OLDER]},
    ],
)
def test_masters_covered_is_false_whenever_anything_is_in_doubt(over):
    assert _covered(**over) is False
