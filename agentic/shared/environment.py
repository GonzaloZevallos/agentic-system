"""The environment the system hands down to what it spawns.

`uv run agentic` activates the system's own virtualenv: `VIRTUAL_ENV`, uv's own
variables, and its `bin/` first on `PATH`. A role that runs the product's tests inside
the product must find the product's toolchain, not ours (a bare `pytest` resolved to
our venv in the second GH-12 run and cost the Developer turns). The SDK merges the
whole process environment into every role's, and a variable cannot be removed from
there, so the scrub happens once, in the system's own process, before anything spawns.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def without_own_toolchain(env: Mapping[str, str]) -> dict[str, str]:
    """`env` without the virtualenv activated for the system itself: `VIRTUAL_ENV`,
    `UV` and `UV_*`, and that venv's `bin/` on `PATH`. Returns a new mapping."""
    venv = env.get("VIRTUAL_ENV")
    clean = {k: v for k, v in env.items() if k != "VIRTUAL_ENV" and not _is_uv(k)}
    if venv and "PATH" in clean:
        bin_dir = Path(venv) / "bin"
        entries = clean["PATH"].split(os.pathsep)
        # Compared as paths: a trailing slash or a `./` must not keep the entry alive.
        clean["PATH"] = os.pathsep.join(e for e in entries if not e or Path(e) != bin_dir)
    return clean


def _is_uv(name: str) -> bool:
    # `UV_*` is uv's namespace; a product's `UVICORN_*` is not.
    return name == "UV" or name.startswith("UV_")
