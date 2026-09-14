"""`FlagVocabulary` from YAML: the product's `.agentic/flags.yaml`, or the default."""

from __future__ import annotations

from pathlib import Path

import yaml

from ...domain import flags

OVERLAY = Path(".agentic/flags.yaml")


class FilesystemFlags:
    def __init__(self, product_dir: Path):
        self.product_dir = product_dir

    def load(self) -> dict[str, str]:
        source = self.product_dir / OVERLAY
        if not source.exists():
            source = flags.DEFAULT
        return flags.vocabulary(yaml.safe_load(source.read_text(encoding="utf-8")), source)
