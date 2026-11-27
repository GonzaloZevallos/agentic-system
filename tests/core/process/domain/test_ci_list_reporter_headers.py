"""`ci.scan_headers` on Playwright's list-reporter lines (GH-1151): a job cancelled at its time
limit never prints the numbered summary, so its failing specs are read from the `✘` lines. Pure."""

import pytest

from agentic.core.process.domain import ci

SPEC = "[mobile] › e2e/mobile.spec.ts:44:5 › phone standard at 320x568, claro"


def ids(text: str) -> list[str]:
    return [header.id for header in ci.scan_headers(text).headers]


def test_a_log_cut_mid_run_lists_the_failing_spec_with_its_position() -> None:
    log = f"  ✓  1 [mobile] › e2e/a.spec.ts:3:1 › fine (1.2s)\n  ✘  2 {SPEC} (7.0m)\n  ✓  3 [mobile] › e2e/b.spec.ts:9:1 › ok (2ms)\n"
    assert ci.scan_headers(log).headers == (ci.FailingHeader(1, SPEC),)


def test_a_failure_that_passed_on_retry_is_not_listed() -> None:
    log = f"  ✘  2 {SPEC} (3.1s)\n  ✓  9 {SPEC} (retry #1) (2.0s)\n"
    assert ids(log) == []


def test_a_failure_on_every_retry_is_listed_once() -> None:
    log = "".join(
        f"  ✘  {n} {SPEC}{retry} (3.1s)\n"
        for n, retry in [(2, ""), (5, " (retry #1)"), (8, " (retry #2)")]
    )
    assert ci.scan_headers(log).headers == (ci.FailingHeader(0, SPEC),)


def test_a_failure_whose_retry_passed_first_then_failed_again_is_listed() -> None:
    log = f"  ✓  1 {SPEC} (1s)\n  ✘  2 {SPEC} (retry #1) (3.1s)\n"
    assert ids(log) == [SPEC]


def test_parentheses_inside_a_title_stay() -> None:
    title = "[mobile] › e2e/a.spec.ts:3:1 › opens (and closes) the sheet (again)"
    assert ids(f"  ✘  4 {title} (812ms)") == [title]


@pytest.mark.parametrize(
    "line",
    [
        "\x1b[31m  ✘  4 {} (7.0m)\x1b[39m",
        "job\tstep\t2026-11-10T10:00:00.0000000Z   ✘  4 {} (7.0m)",
    ],
)
def test_ansi_and_gh_prefix_do_not_change_the_key(line: str) -> None:
    assert ids(line.format(SPEC)) == [SPEC]


def test_the_numbered_summary_wins_when_the_log_has_one() -> None:
    summary = "  1) [mobile] › e2e/mobile.spec.ts:44:5 › phone standard at 320x568, claro ───"
    log = f"  ✘  2 {SPEC} (7.0m)\n\n{summary}\n"
    assert ids(log) == ["[mobile] › e2e/mobile.spec.ts › phone standard at 320x568, claro"]


def test_a_passing_log_lists_nothing() -> None:
    assert ids(f"  ✓  1 {SPEC} (1.0s)\n  12 passed (1m)") == []
