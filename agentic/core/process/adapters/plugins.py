"""Where the skills plugin lives (ADR-0013): a submodule pinned under `agentic/plugins/`.
The SDK adapter loads it; the document adapter records its version."""

from __future__ import annotations

import json
from pathlib import Path

PLUGIN = "mattpocock-skills"
# `agentic/plugins/<plugin>/`: the package root is three levels up from this module.
PLUGIN_DIR = Path(__file__).resolve().parents[3] / "plugins" / PLUGIN


def version() -> dict[str, str]:
    """Name and version of the plugin, read off its manifest. Empty when the submodule
    is not checked out."""
    manifest = PLUGIN_DIR / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        return {}
    data = json.loads(manifest.read_text(encoding="utf-8"))
    return {data["name"]: data["version"]}
