import pytest

from agentic import cli


def product(tmp_path, name):
    root = tmp_path / name
    (root / ".agentic").mkdir(parents=True)
    return root


def test_flag_beats_env(monkeypatch, tmp_path):
    from_env, from_flag = product(tmp_path, "env"), product(tmp_path, "flag")
    monkeypatch.setenv("AGENTIC_PRODUCT_DIR", str(from_env))
    seen = {}

    def fake_run(fn, title, description, cfg):
        seen["cfg"] = cfg

    monkeypatch.setattr(cli.anyio, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(from_flag)])
    cli.main()
    assert seen["cfg"].product_dir == from_flag


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
