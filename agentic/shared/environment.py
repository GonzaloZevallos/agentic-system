"""The environment the system hands down to what it spawns: without the system's own
toolchain, and without the system's own configuration.

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

from .configuration import ENV_PREFIX


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


def without_own_configuration(env: Mapping[str, str]) -> dict[str, str]:
    """`env` without the system's own configuration, the `AGENTIC_*` of `Configuration`.
    Returns a new mapping.

    What the human set for their run says nothing about what a spawned process should
    do, and with the system as a product (ADR-0017) the two read the very same
    variables: the product's checks are this repo's, and an inherited
    `AGENTIC_PRODUCT_DIR` or `AGENTIC_SYSTEM_VERSION` there speaks for the run outside,
    not for the work inside."""
    return {k: v for k, v in env.items() if not k.startswith(ENV_PREFIX)}


def own_configuration_unset(env: Mapping[str, str]) -> dict[str, str]:
    """Every `AGENTIC_*` of `env` mapped to the empty string: the same scrub for a
    process whose environment we can only add to.

    The SDK merges this process's whole environment into every role's and a key cannot
    be removed from there, so removing one means overwriting it with nothing. Empty is
    already unset for everything of ours that reads them: `Configuration.from_sources`
    skips an empty variable on purpose, and `system_version` falls through it."""
    return {name: "" for name in env if name.startswith(ENV_PREFIX)}


def _is_uv(name: str) -> bool:
    # `UV_*` is uv's namespace; a product's `UVICORN_*` is not.
    return name == "UV" or name.startswith("UV_")
