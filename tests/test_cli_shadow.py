"""`agentic eval shadow`: argparse and the print, over documents on disk."""

import pytest
import yaml

from agentic import cli
from agentic.shared.configuration import Configuration
from tests.fakes import memory_ports


def _shadow(status="ran", findings=(), cause=None, lines=12, subtask="integration"):
    return {
        "subtask": subtask,
        "head": "h",
        "status": status,
        "cause": cause,
        "findings": list(findings),
        "cost_usd": 0.5,
        "changed_lines": lines,
    }


def _finding(location="a/c.py:1", severity="blocking"):
    return {
        "subtask": "integration",
        "version": 1,
        "location": location,
        "claim": "c",
        "severity": severity,
        "scope": "subtask",
        "category": "spec-behaviour",
        "status": "open",
    }


def _write(root, task_id, shadows, **extra):
    front = {
        "id": task_id,
        "title": "t",
        "type": "feature",
        "status": "ready_for_review",
        "subtasks": {"integration": {}},
        "cost_usd": 4.0,
        "shadow_reviews": shadows,
        **extra,
    }
    tasks = root / ".agentic" / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    (tasks / f"{task_id}.md").write_text(
        f"---\n{yaml.safe_dump(front)}---\n\n## Original request\n\nd\n"
    )


async def _run(tmp_path, capsys):
    await cli._eval_shadow(Configuration(product_dir=tmp_path), memory_ports())
    return capsys.readouterr().out


@pytest.mark.anyio
async def test_a_task_whose_shadow_ran_and_found_nothing_prints_the_zero_findings_line(
    tmp_path, capsys
):
    _write(tmp_path, "GH-1", [_shadow()])
    out = await _run(tmp_path, capsys)
    assert "GH-1: shadow ran, 0 findings" in out.splitlines()


@pytest.mark.anyio
async def test_a_task_with_findings_prints_its_buckets_and_the_tail_of_what_was_dropped(
    tmp_path, capsys
):
    shadows = [
        _shadow(findings=[_finding()]),
        _shadow("late", cause="late", subtask="b", lines=40),
    ]
    _write(tmp_path, "GH-1", shadows)
    out = await _run(tmp_path, capsys)
    assert "GH-1: blocking missed 1, caught_lower 0, caught 0, unlocated 0" in out
    assert "also: late 1; 40 changed line(s) dropped" in out


@pytest.mark.anyio
async def test_an_empty_ran_record_with_a_late_one_prints_the_zero_line_and_the_tail(
    tmp_path, capsys
):
    _write(tmp_path, "GH-1", [_shadow(), _shadow("late", cause="late", subtask="b")])
    out = await _run(tmp_path, capsys)
    assert "GH-1: shadow ran, 0 findings" in out.splitlines()
    assert "also: late 1" in out


@pytest.mark.anyio
async def test_a_task_with_only_skipped_or_unavailable_records_prints_its_cause_and_no_denominator(
    tmp_path, capsys
):
    _write(tmp_path, "GH-1", [_shadow("skipped", cause="single_unit")])
    _write(tmp_path, "GH-2", [_shadow("unavailable", cause="setup", subtask="b")])
    out = await _run(tmp_path, capsys)
    assert "GH-1: no shadow ran (skipped single_unit x1)" in out
    assert "GH-2: no shadow ran (unavailable setup x1)" in out
    assert "0 task(s) counted" in out


@pytest.mark.anyio
async def test_an_unreadable_document_is_named_and_excluded_and_a_task_without_shadows_is_not_read(
    tmp_path, capsys
):
    _write(tmp_path, "GH-1", [_shadow()])
    _write(tmp_path, "GH-3", [])
    (tmp_path / ".agentic" / "tasks" / "broken.md").write_text("no frontmatter")
    out = await _run(tmp_path, capsys)
    assert out.startswith("agentic: 1 document(s) could not be read; excluded")
    assert "read 1 document(s): GH-1" in out


def test_shadow_takes_no_window(monkeypatch):
    monkeypatch.setattr("sys.argv", ["agentic", "eval", "shadow", "--since", "2026-10-13"])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 2
