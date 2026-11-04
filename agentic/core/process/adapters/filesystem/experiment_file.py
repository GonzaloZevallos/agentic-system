"""`ExperimentFile` over the product directory: `.agentic/experiment.yaml` (GH-470)."""

from __future__ import annotations

from pathlib import Path

import yaml

EXPERIMENT_RELATIVE_PATH = Path(".agentic") / "experiment.yaml"


class FilesystemExperimentFile:
    def __init__(self, product_dir: Path):
        self.path = product_dir / EXPERIMENT_RELATIVE_PATH

    def read(self) -> object | None:
        if not self.path.is_file():
            return None
        try:
            data: object = yaml.safe_load(self.path.read_text(encoding="utf-8"))
            return data
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            detail = " ".join(str(exc).split()) or type(exc).__name__
            raise ValueError(f"{EXPERIMENT_RELATIVE_PATH}: cannot be read: {detail}") from exc
