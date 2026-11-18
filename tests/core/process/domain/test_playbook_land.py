"""The landing verdict of the playbook PR (owner's rule of 2026-11-16): the gates in their fixed
order, the closed content table, the attempt record and its streak rules.

Seam: `agentic.core.process.domain.playbook_land`."""

from __future__ import annotations

import pytest

from agentic.core.process.domain import playbook as pb
from agentic.core.process.domain import playbook_candidates as pc
from agentic.core.process.domain import playbook_land as pl
from agentic.core.process.domain.ci import CiCheck, CiReport, CiUnreadable

HEAD = "a" * 40
PATH = pc.playbook_file("developer")
NAMES = ("check",)


def run(conclusion: str | None = "SUCCESS", name: str = "check", run_id: int = 1) -> CiCheck:
    status = "COMPLETED" if conclusion else "IN_PROGRESS"
    return CiCheck(name, status, conclusion, f"https://github.com/o/r/actions/runs/{run_id}")


def report(*checks: CiCheck, completed: bool | None = True) -> CiReport:
    return CiReport(HEAD, "master", "b" * 40, None, checks, completed)


def verdict(
    paths: tuple[str, ...] = (PATH,),
    content: pl.ContentVerdict = "ok",
    read: CiReport | CiUnreadable | None = None,
    names: tuple[str, ...] = NAMES,
    covered: bool = True,
    confirmed: bool = False,
) -> pl.Merge | pl.Wait | pl.Refuse:
    return pl.land_verdict(
        paths, content, read if read is not None else report(run()), names, covered, confirmed
    )


# ---- land_verdict: every cause ---------------------------------------------------------


def test_a_green_head_over_playbook_paths_with_a_covered_base_merges_that_head():
    assert verdict() == pl.Merge(HEAD)


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"paths": ()}, pl.Refuse("paths_unreadable")),
        ({"content": "outside"}, pl.Refuse("path_outside")),
        ({"content": "invalid"}, pl.Refuse("playbook_invalid")),
        ({"read": report(run("FAILURE"))}, pl.Refuse("ci_red")),
        ({"read": report(run("SKIPPED"))}, pl.Refuse("ci_red")),
        ({"read": report(run(None)), "confirmed": True}, pl.Wait("ci_pending")),
        (
            {"read": report(run(), completed=True), "names": ("check", "lint"), "confirmed": True},
            pl.Refuse("ci_unreported"),
        ),
        (
            {"read": report(run(), completed=True), "names": ("check", "lint")},
            pl.Wait("ci_pending"),
        ),
        ({"read": report(completed=False)}, pl.Wait("ci_pending")),
        ({"read": CiUnreadable("gh_unavailable")}, pl.Wait("ci_unreadable")),
        ({"covered": False}, pl.Wait("stale_base")),
        ({"names": ()}, pl.Refuse("ci_unreported")),
    ],
)
def test_each_gate_gives_its_closed_cause(kwargs, expected):
    assert verdict(**kwargs) == expected


@pytest.mark.parametrize(
    "path",
    [
        ".agentic/playbooks-x/a",
        ".agentic/playbooks/../stack.yaml",
        ".agentic/playbooks/a/../../stack.yaml",
        f"a/../{PATH}",
        f"/{PATH}",
        ".agentic/playbooks",
        "",
        ".agentic/stack.yaml",
        "agentic/cli.py",
    ],
)
def test_a_path_outside_the_directory_prefix_is_path_outside_never_a_glob(path):
    assert verdict(paths=(PATH, path)) == pl.Refuse("path_outside")


def test_paths_are_judged_before_content_and_content_before_ci_and_ci_before_base():
    red = report(run("FAILURE"))
    assert verdict(paths=(), content="outside", read=red, covered=False) == pl.Refuse(
        "paths_unreadable"
    )
    assert verdict(paths=("x",), content="invalid", read=red, covered=False) == pl.Refuse(
        "path_outside"
    )
    assert verdict(content="invalid", read=red, covered=False) == pl.Refuse("playbook_invalid")
    assert verdict(read=red, covered=False) == pl.Refuse("ci_red")
    assert verdict(read=CiUnreadable("gh_unreadable"), covered=False) == pl.Wait("ci_unreadable")


def test_a_cancelled_or_superseded_run_does_not_decide_the_ci():
    cancelled = CiReport(
        HEAD,
        None,
        None,
        None,
        (run("SUCCESS", run_id=1), run("CANCELLED", run_id=2)),
        True,
        cancelled_runs=frozenset({"2"}),
    )
    assert verdict(read=cancelled) == pl.Merge(HEAD)
    superseded = report(run("FAILURE", run_id=1), run("SUCCESS", run_id=2))
    assert verdict(read=superseded) == pl.Merge(HEAD)
    newer_red = report(run("SUCCESS", run_id=1), run("FAILURE", run_id=2))
    assert verdict(read=newer_red) == pl.Refuse("ci_red")


def test_foreign_refusal_accepts_only_a_pull_request_known_to_be_from_this_repository():
    assert pl.foreign_refusal(False) is None
    assert pl.foreign_refusal(True) == pl.Refuse("foreign_head")
    assert pl.foreign_refusal(None) == pl.Refuse("foreign_head")


# ---- content_verdict: the closed table -------------------------------------------------


def role_text(count: int, slug: str = "developer") -> str:
    items = tuple(
        pb.Item(
            f"{slug}-{n}", f"Lesson number {n}.", "standard-other", "empty", ("GH-1",), "2026-10-05"
        )
        for n in range(1, count + 1)
    )
    return pb.render_file(pb.Playbook(slug, items))


def at(name: str) -> str:
    return f"{pc.PLAYBOOKS_DIR}/{name}"


VALID_RECORDS = {
    pc.CANDIDATES_FILE: pc.render_candidates([]),
    pc.ARCHIVED_FILE: pc.render_archived([]),
    pc.REJECTED_FILE: pc.render_rejected([]),
    pc.LEDGER_FILE: pc.render_ledger(pc.Ledger()),
}


def test_every_row_of_the_table_passes_with_a_valid_file():
    files: dict[str, str | None] = {at("developer.yaml"): role_text(2), **VALID_RECORDS}
    files[pc.summary_file(3)] = "# a summary\n"
    files[at("tech_lead.yaml")] = None
    assert pl.content_verdict(files) == "ok"


def test_a_role_playbook_at_the_cap_passes_and_one_over_it_is_invalid():
    assert pl.content_verdict({PATH: role_text(pb.PLAYBOOK_MAX_ITEMS)}) == "ok"
    assert pl.content_verdict({PATH: role_text(pb.PLAYBOOK_MAX_ITEMS + 1)}) == "invalid"


@pytest.mark.parametrize(
    "path",
    [PATH, pc.CANDIDATES_FILE, pc.ARCHIVED_FILE, pc.REJECTED_FILE, pc.LEDGER_FILE],
)
def test_a_file_that_fails_its_own_parser_is_invalid(path):
    assert pl.content_verdict({path: "items: [unclosed"}) == "invalid"
    assert pl.content_verdict({path: "- just\n- a list\n"}) == "invalid"


@pytest.mark.parametrize(
    ("path", "text"),
    [
        (pc.REJECTED_FILE, None),
        (pc.LEDGER_FILE, None),
        (pc.CANDIDATES_FILE, None),
        (pc.summary_file(3), None),
        (at("unknown.yaml"), "items: []\n"),
        (at("summaries/x.md"), "text"),
        (at("summaries/3.md.bak"), "text"),
        (at("summaries/sub/3.md"), "text"),
        (at("sub/developer.yaml"), role_text(1)),
        (at("README.md"), "text"),
        (".agentic/stack.yaml", "ci: [check]\n"),
    ],
)
def test_an_unknown_file_or_a_deletion_of_a_record_is_outside(path, text):
    assert pl.content_verdict({path: text}) == "outside"


def test_outside_wins_over_invalid_and_an_empty_batch_has_nothing_wrong():
    assert pl.content_verdict({PATH: "items: [unclosed", at("x.txt"): "a"}) == "outside"
    assert pl.content_verdict({}) == "ok"


# ---- LandAttempt -----------------------------------------------------------------------


def test_an_attempt_round_trips_and_omits_optional_keys_at_their_default():
    plain = pl.LandAttempt("h", "2026-11-18T10:00:00Z", "merge")
    assert plain.to_data() == {
        "v": 1,
        "head": "h",
        "at": "2026-11-18T10:00:00Z",
        "verdict": "merge",
    }
    full = pl.LandAttempt("h", "t", "refuse", "ci_red", 2, "m", "e")
    assert full.to_data() == {
        "v": 1,
        "head": "h",
        "at": "t",
        "verdict": "refuse",
        "cause": "ci_red",
        "streak": 2,
        "master_sha": "m",
        "escalated_at": "e",
    }
    assert pl.LandAttempt.from_data(plain.to_data()) == plain
    assert pl.LandAttempt.from_data(full.to_data()) == full


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {},
        {"v": 2, "head": "h", "at": "t", "verdict": "merge"},
        {"v": True, "head": "h", "at": "t", "verdict": "merge"},
        {"v": 1, "head": "h", "at": "t", "verdict": "other"},
        {"v": 1, "head": 3, "at": "t", "verdict": "merge"},
        {"v": 1, "head": "h", "verdict": "merge"},
        {"v": 1, "head": "h", "at": "t", "verdict": "refuse", "streak": "2"},
        {"v": 1, "head": "h", "at": "t", "verdict": "refuse", "streak": -1},
        {"v": 1, "head": "h", "at": "t", "verdict": "refuse", "streak": True},
        {"v": 1, "head": "h", "at": "t", "verdict": "refuse", "cause": 4},
    ],
)
def test_an_unknown_version_or_malformed_data_reads_as_none(data):
    assert pl.LandAttempt.from_data(data) is None


# ---- next_attempt and land_halted ------------------------------------------------------


def step(prior, verdict, head="h2", at="t2", master="m2"):
    return pl.next_attempt(prior, verdict, head, at, master)


def test_a_first_refusal_starts_a_streak_of_one_and_records_where_it_happened():
    got = step(None, pl.Refuse("ci_red"))
    assert got == pl.LandAttempt("h2", "t2", "refuse", "ci_red", 1, "m2", None)


def test_a_refusal_for_the_same_cause_adds_one_and_another_cause_starts_over():
    first = step(None, pl.Refuse("ci_red"))
    second = step(first, pl.Refuse("ci_red"))
    assert second.streak == 2
    assert step(second, pl.Refuse("path_outside")).streak == 1


def test_a_merge_resets_the_streak_and_a_wait_or_a_failed_call_keeps_it():
    refused = pl.LandAttempt("h", "t", "refuse", "ci_red", 2, "m", "e")
    merged = step(refused, pl.Merge("h2"))
    assert (merged.verdict, merged.cause, merged.streak, merged.escalated_at) == (
        "merge",
        None,
        0,
        None,
    )
    waited = step(refused, pl.Wait("ci_pending"))
    assert waited == pl.LandAttempt("h2", "t2", "refuse", "ci_red", 2, "m2", "e")
    failed = step(refused, pl.Failed("MergeCallFailed"))
    assert failed == pl.LandAttempt("h2", "t2", "refuse", "ci_red", 2, "m2", "e")


def test_a_wait_or_a_failed_call_with_no_refusal_standing_is_recorded_with_its_own_cause():
    waited = step(None, pl.Wait("ci_pending"))
    assert (waited.verdict, waited.cause, waited.streak) == ("wait", "ci_pending", 0)
    failed = step(waited, pl.Failed("MergeCallFailed"))
    assert (failed.verdict, failed.cause, failed.streak) == ("failed", "MergeCallFailed", 0)
    merged = step(pl.LandAttempt("h", "t", "merge"), pl.Wait("stale_base"))
    assert (merged.verdict, merged.streak) == ("wait", 0)


def test_escalated_at_survives_a_repeated_refusal_and_is_cleared_when_the_streak_resets():
    prior = pl.LandAttempt("h", "t", "refuse", "ci_red", 3, "m", "e")
    assert step(prior, pl.Refuse("ci_red")).escalated_at == "e"
    assert step(prior, pl.Refuse("stale_conflict")).escalated_at is None
    assert step(prior, pl.Merge("h2")).escalated_at is None


def test_a_refuse_wait_refuse_sequence_for_one_cause_counts_and_halts():
    refused = step(None, pl.Refuse("ci_red"))
    waited = step(refused, pl.Wait("ci_pending"))
    again = step(waited, pl.Refuse("ci_red"))
    assert again.streak == 2
    failed = step(again, pl.Failed("MergeCallFailed"))
    third = step(failed, pl.Refuse("ci_red"))
    assert third.streak == pl.LAND_REFUSALS_MAX and pl.land_halted(third)
    assert step(waited, pl.Refuse("path_outside")).streak == 1


def test_land_halted_only_when_refusals_in_a_row_reach_the_maximum():
    assert pl.LAND_REFUSALS_MAX == 3
    assert not pl.land_halted(None)
    refused = pl.LandAttempt("h", "t", "refuse", "ci_red", pl.LAND_REFUSALS_MAX - 1)
    assert not pl.land_halted(refused)
    assert pl.land_halted(step(refused, pl.Refuse("ci_red")))
    assert not pl.land_halted(pl.LandAttempt("h", "t", "wait", "ci_pending", 3))


def test_the_halt_marker_names_the_product_and_the_cause():
    assert (
        pl.halt_marker("agentic", "ci_red") == "<!-- agentic-playbook-land-halt: agentic ci_red -->"
    )
