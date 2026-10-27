"""`agentic eval findings`: argparse and the print, over documents on disk."""

from datetime import date

import pytest
import yaml

from agentic import cli
from agentic.shared.configuration import Configuration
from tests.fakes import memory_ports


def _write(root, task_id, closed_at, **extra):
    front = {
        "id": task_id,
        "title": "t",
        "type": "feature",
        "status": "ready_for_review",
        "closed_at": closed_at,
        "delivery_outcome": "merged",
        **extra,
    }
    tasks = root / ".agentic" / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    (tasks / f"{task_id}.md").write_text(
        f"---\n{yaml.safe_dump(front)}---\n\n## Original request\n\nd\n"
    )


@pytest.mark.anyio
async def test_findings_prints_the_ids_it_read_and_skips_the_documents_outside_the_window(
    tmp_path, capsys
):
    (tmp_path / ".agentic").mkdir()
    _write(tmp_path, "GH-1", "2026-10-20T12:00:00+00:00")
    _write(tmp_path, "GH-2", "2026-09-01T12:00:00+00:00")
    (tmp_path / ".agentic" / "tasks" / "broken.md").write_text("no frontmatter")
    cfg = Configuration(product_dir=tmp_path)

    await cli._eval_findings(
        cfg,
        memory_ports(),
        date(2026, 10, 13),
        date(2026, 10, 27),
    )

    out = capsys.readouterr().out
    assert "1 document(s) could not be read" in out
    assert "Read 1 document(s) closed in the window (1 outside it): GH-1" in out


def test_findings_requires_a_window(monkeypatch):
    monkeypatch.setattr("sys.argv", ["agentic", "eval", "findings", "--since", "2026-10-13"])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 2
