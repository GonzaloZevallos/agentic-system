"""`scripts/gate_concurrency.py`: the issue's table of gate duration by how many other gates
ran alongside (GH-361). The seams: `overlap_peers`, and `main` as the CLI over fixture
transcripts and task documents."""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "gate_concurrency.py"
FIXTURES = REPO / "tests" / "fixtures" / "gate_concurrency"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gate_concurrency_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load()


def test_disjoint_intervals_have_no_peers():
    assert script.overlap_peers([(0.0, 10.0), (20.0, 30.0), (40.0, 41.0)]) == [0.0, 0.0, 0.0]


def test_an_interval_inside_another_has_one_peer_and_the_outer_a_fraction():
    # inner (4, 6) lies wholly in outer (0, 10): the inner always has one peer beside it,
    # the outer has one for 2 of its 10 seconds.
    assert script.overlap_peers([(0.0, 10.0), (4.0, 6.0)]) == pytest.approx([0.2, 1.0])


def test_peers_are_time_weighted_across_several_overlaps():
    # (0, 10) overlaps (5, 15) for 5 s and (8, 20) for 2 s: (5 + 2) / 10.
    peers = script.overlap_peers([(0.0, 10.0), (5.0, 15.0), (8.0, 20.0)])
    assert peers[0] == pytest.approx(0.7)
    assert peers[1] == pytest.approx((5 + 7) / 10)
    assert peers[2] == pytest.approx((2 + 7) / 12)


def test_an_empty_interval_has_no_peers_and_touching_ones_do_not_overlap():
    assert script.overlap_peers([(5.0, 5.0), (0.0, 10.0)]) == [0.0, 0.0]
    assert script.overlap_peers([(0.0, 5.0), (5.0, 10.0)]) == [0.0, 0.0]
    assert script.overlap_peers([]) == []


def _minutes(gate) -> float:
    return gate.minutes


def test_transcripts_yield_the_delivery_calls_that_ran_long_enough():
    gates = script.from_transcripts(FIXTURES / "transcripts")
    # The 50 ms delivery, the Bash call and the call with no result are not gates; the
    # refused call is one that did not pass.
    assert [(_minutes(g), g.passed) for g in gates] == [
        (12.0, True),
        (10.0, False),
        (10.0, True),
    ]
    assert all(g.wait_s is None and g.share is None for g in gates)


def test_documents_yield_each_recorded_gate_with_its_wait_run_and_share():
    gates = script.from_documents(FIXTURES / "documents")
    # The run's last gate delivered, the earlier one was refused; the merge passed and the
    # full checks did not. A document with no record, a malformed record and a file with no
    # frontmatter add nothing.
    assert [(g.wait_s, g.run_s, g.share, g.passed) for g in gates] == [
        (0.0, 600.0, 6, False),
        (120.0, 480.0, 4, True),
        (0.0, 600.0, 10, True),
        (0.0, 300.0, 12, False),
    ]
    assert [_minutes(g) for g in gates] == [10.0, 10.0, 10.0, 5.0]


def test_the_transcript_table_counts_passing_gates_by_rounded_peers():
    out = io.StringIO()
    assert script.main(["--transcripts", str(FIXTURES / "transcripts")], out) == 0
    assert out.getvalue().splitlines() == [
        "source: transcripts",
        "| other gates alongside | gates measured | median min |",
        "| ~0 | 1 | 10.0 |",
        "| ~1 | 1 | 12.0 |",
        "| ~2 | 0 | - |",
        "| ~3 | 0 | - |",
        "| 4+ | 0 | - |",
    ]


def test_the_document_table_adds_wait_run_and_share_columns():
    out = io.StringIO()
    assert script.main(["--documents", str(FIXTURES / "documents")], out) == 0
    assert out.getvalue().splitlines() == [
        "source: documents",
        "| other gates alongside | gates measured | median min | median wait min "
        "| median run min |",
        "| ~0 | 0 | - | - | - |",
        "| ~1 | 2 | 10.0 | 1.0 | 9.0 |",
        "| ~2 | 0 | - | - | - |",
        "| ~3 | 0 | - | - | - |",
        "| 4+ | 0 | - | - | - |",
        "",
        "| share | gates measured | median min |",
        "| 4 | 1 | 10.0 |",
        "| 10 | 1 | 10.0 |",
    ]


def test_both_sources_print_both_tables():
    out = io.StringIO()
    argv = [
        "--transcripts",
        str(FIXTURES / "transcripts"),
        "--documents",
        str(FIXTURES / "documents"),
    ]
    assert script.main(argv, out) == 0
    text = out.getvalue()
    assert text.index("source: transcripts") < text.index("source: documents")


def test_four_or_more_peers_fall_in_the_last_bucket():
    peers = (0.0, 0.49, 0.5, 2.4, 3.5, 4.0, 11.0)
    assert [script.bucket(x) for x in peers] == [0, 0, 1, 2, 4, 4, 4]


def test_a_source_must_be_named():
    with pytest.raises(SystemExit) as raised:
        script.main([], io.StringIO())
    assert raised.value.code == 2
