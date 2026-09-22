"""Cross-checks `.github/dependabot.yml` against ADR-0022 (issue #74, subtask
`dependabot`).

A Dependabot manifest has no other automatic check: this parses the YAML and asserts
the contract the ADR decides on, not any of Dependabot's own internals. It has three
concerns, each a decision in the ADR:

- the declared ecosystems are exactly `uv` and `github-actions`, both weekly;
- `claude-agent-sdk`, `langgraph` and `langgraph-checkpoint-sqlite` pin the harness and
  the graph engine and must each land as their own pull request, so no group's
  `patterns` (net of that group's own `exclude-patterns`) may match any of them;
- `gitsubmodule` is absent — ADR-0013 makes the skills plugin bump a deliberate human
  commit, and nothing in CI can see a behaviour change there.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parent.parent
DEPENDABOT = REPO / ".github" / "dependabot.yml"

HARNESS_PACKAGES = ["claude-agent-sdk", "langgraph", "langgraph-checkpoint-sqlite"]


def _manifest() -> dict[str, Any]:
    return yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))


def _updates() -> list[dict[str, Any]]:
    return _manifest()["updates"]


def _matched_by_group(name: str, group: dict[str, Any]) -> bool:
    patterns = group.get("patterns", [])
    excludes = group.get("exclude-patterns", [])
    matched = any(fnmatch.fnmatch(name, p) for p in patterns)
    excluded = any(fnmatch.fnmatch(name, p) for p in excludes)
    return matched and not excluded


def test_declares_exactly_uv_and_github_actions() -> None:
    ecosystems = {u["package-ecosystem"] for u in _updates()}
    assert ecosystems == {"uv", "github-actions"}


def test_every_entry_schedule_is_weekly() -> None:
    for update in _updates():
        assert update["schedule"]["interval"] == "weekly"


def test_harness_packages_are_matched_by_no_group() -> None:
    """The three packages are `uv` dependencies; only `uv` entries' groups are a
    possible source of grouping them, so only those are checked — a `github-actions`
    group's `patterns: ["*"]` is not a claim about `uv` package names at all."""
    for update in _updates():
        if update["package-ecosystem"] != "uv":
            continue
        for group in update.get("groups", {}).values():
            for package in HARNESS_PACKAGES:
                assert not _matched_by_group(package, group), (
                    f"{package} is matched by a group's patterns; "
                    "it must arrive on its own pull request"
                )


def test_gitsubmodule_is_absent() -> None:
    ecosystems = {u["package-ecosystem"] for u in _updates()}
    assert "gitsubmodule" not in ecosystems
