"""Flag vocabulary: the closed set of scope flags a role may mark.

The system ships a minimal default (`flags.yaml`, next to this module); a product
replaces it entirely with `.agentic/flags.yaml` in its repo, the same pattern as
prompt overrides (ADR-0005). Loading is the `FlagVocabulary` port's job. Roles
receive the vocabulary in their prompt and the schema only admits those names, so an
invented flag is a role failure, not a new flag. How the vocabulary reads in a prompt
and the flags marked on a task are `core.process`'s (ADR-0010).
"""

from __future__ import annotations

from pathlib import Path

DEFAULT = Path(__file__).resolve().parent / "flags.yaml"


def vocabulary(data: object, source: object) -> dict[str, str]:
    """Flag name -> one-line description, from the parsed file. Rejects anything but a
    non-empty mapping: a product that empties its overlay has no flags, not the default."""
    if not isinstance(data, dict) or not data:
        raise ValueError(f"{source}: expected a non-empty mapping of flag -> description")
    return {str(k): str(v) for k, v in data.items()}
