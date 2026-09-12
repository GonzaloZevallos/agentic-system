"""Flag vocabulary: the closed set of scope flags a role may mark.

Ships with a minimal default; a product replaces it entirely with
`.agentic/flags.yaml` in its repo, the same pattern as prompt overrides
(ADR-0005). Roles receive the vocabulary in their prompt and the schema only
admits those names, so an invented flag is a role failure, not a new flag.
"""

from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT = Path(__file__).resolve().parent / "flags.yaml"
OVERLAY = Path(".agentic/flags.yaml")


def load(product_dir: Path) -> dict[str, str]:
    """Flag name -> one-line description."""
    source = product_dir / OVERLAY
    if not source.exists():
        source = DEFAULT
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not data:
        raise ValueError(f"{source}: expected a non-empty mapping of flag -> description")
    return {str(k): str(v) for k, v in data.items()}


def describe(vocabulary: dict[str, str]) -> str:
    return "\n".join(f"- `{name}`: {desc}" for name, desc in vocabulary.items())
