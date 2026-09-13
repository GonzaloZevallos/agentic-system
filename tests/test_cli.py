import json
import subprocess

import pytest

from agentic import cli


def product(tmp_path, name):
    root = tmp_path / name
    (root / ".agentic").mkdir(parents=True)
    return root


def capture_run(monkeypatch):
    """Replace the graph run with a recorder of what the CLI would have run."""
    seen = {}

    def fake_run(fn, task_id, title, description, cfg):
        seen.update(task_id=task_id, title=title, description=description, cfg=cfg)

    monkeypatch.setattr(cli.anyio, "run", fake_run)
    return seen


def test_flag_beats_env(monkeypatch, tmp_path):
    from_env, from_flag = product(tmp_path, "env"), product(tmp_path, "flag")
    monkeypatch.setenv("AGENTIC_PRODUCT_DIR", str(from_env))
    seen = capture_run(monkeypatch)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(from_flag)])
    cli.main()
    assert seen["cfg"].product_dir == from_flag
    assert seen["task_id"].startswith("T-")
    assert seen["cfg"].verbose is False


def test_verbose_reaches_the_configuration(monkeypatch, tmp_path):
    root = product(tmp_path, "p")
    seen = capture_run(monkeypatch)
    monkeypatch.setattr(
        "sys.argv", ["agentic", "run", "t", "d", "--verbose", "--product-dir", str(root)]
    )
    cli.main()
    assert seen["cfg"].verbose is True


def test_preflight_rejects_a_dir_without_agentic(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(tmp_path)])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


def test_preflight_rejects_a_bad_flags_overlay(monkeypatch, tmp_path):
    root = product(tmp_path, "p")
    (root / ".agentic/flags.yaml").write_text("- touches_ui\n")
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(root)])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


def fake_gh(monkeypatch, *, stdout="", returncode=0, stderr=""):
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if returncode:
            raise subprocess.CalledProcessError(returncode, cmd, stderr=stderr)
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(cli.subprocess, "run", run)
    return calls


def test_issue_feeds_title_body_and_id(monkeypatch, tmp_path):
    root = product(tmp_path, "p")
    calls = fake_gh(
        monkeypatch, stdout=json.dumps({"title": " Metrics ", "body": "How is my gym\n"})
    )
    seen = capture_run(monkeypatch)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "--issue", "12", "--product-dir", str(root)])
    cli.main()
    assert seen["task_id"] == "GH-12"
    assert seen["title"] == "Metrics"
    assert seen["description"] == "How is my gym"
    ((cmd, kwargs),) = calls
    assert cmd[:4] == ["gh", "issue", "view", "12"]
    assert kwargs["cwd"] == root  # gh resolves the repo from the product checkout


def test_issue_without_body_is_rejected(monkeypatch, tmp_path):
    root = product(tmp_path, "p")
    fake_gh(monkeypatch, stdout=json.dumps({"title": "Empty", "body": ""}))
    capture_run(monkeypatch)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "--issue", "3", "--product-dir", str(root)])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


def test_gh_failure_exits_2(monkeypatch, tmp_path, capsys):
    root = product(tmp_path, "p")
    fake_gh(monkeypatch, returncode=1, stderr="GraphQL: Could not resolve to an Issue")
    capture_run(monkeypatch)
    monkeypatch.setattr(
        "sys.argv", ["agentic", "run", "--issue", "999", "--product-dir", str(root)]
    )
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2
    assert "Could not resolve" in capsys.readouterr().err


def test_issue_and_title_are_exclusive(monkeypatch, tmp_path):
    root = product(tmp_path, "p")
    capture_run(monkeypatch)
    monkeypatch.setattr(
        "sys.argv", ["agentic", "run", "t", "d", "--issue", "1", "--product-dir", str(root)]
    )
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


def test_title_without_description_is_rejected(monkeypatch, tmp_path):
    root = product(tmp_path, "p")
    capture_run(monkeypatch)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "only-title", "--product-dir", str(root)])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2
