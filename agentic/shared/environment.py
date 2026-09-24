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
from urllib.parse import quote

from .configuration import ENV_PREFIX

# `OTEL_RESOURCE_ATTRIBUTES` keys this module ever writes: our marker on a spawned
# process (GH-70). Never `service.*` — that key names the OTel resource's own service
# identity; writing it here would misattribute the spawned process's identity instead
# of merely tagging it (invariant 5).
RESOURCE_ROLE_SLUG_KEY = "agentic.role.slug"
RESOURCE_TRACE_ID_KEY = "agentic.trace_id"


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


def trace_id_of(traceparent: str | None) -> str | None:
    """The 32-hex trace id inside a W3C `traceparent` header
    (`00-<32 hex trace id>-<16 hex span id>-<flags>`), or None for a header that is
    absent or does not match that shape, including the all-zero trace id or span id
    the spec reserves as invalid."""
    if not traceparent:
        return None
    fields = traceparent.split("-")
    if len(fields) != 4:
        return None
    version, trace_id, span_id, flags = fields
    if (len(version), len(trace_id), len(span_id), len(flags)) != (2, 32, 16, 2):
        return None
    if not all(_is_hex(field) for field in fields):
        return None
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None
    return trace_id


def _is_hex(value: str) -> bool:
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def with_resource_marker(inherited: str | None, *, role_slug: str, trace_id: str) -> str:
    """`OTEL_RESOURCE_ATTRIBUTES` for a spawned process: the inherited comma-separated
    list, with `agentic.role.slug` and `agentic.trace_id` added (or refreshed, if the
    inherited value already carries a stale one of ours — a role re-invoking itself,
    say). Values are percent-encoded so a comma or `=` inside `role_slug` cannot
    truncate or corrupt the list.

    Every other inherited key-value survives untouched, including an entry this
    function cannot parse (no `=`): we do not own someone else's entries, so a
    malformed one is preserved as is, never 'repaired'. Never emits a `service.*` key
    of its own (invariant 5)."""
    marker_keys = {RESOURCE_ROLE_SLUG_KEY, RESOURCE_TRACE_ID_KEY}
    kept = [raw for key, raw in _parse(inherited) if key not in marker_keys]
    added = [
        f"{RESOURCE_ROLE_SLUG_KEY}={quote(role_slug, safe='')}",
        f"{RESOURCE_TRACE_ID_KEY}={quote(trace_id, safe='')}",
    ]
    return ",".join(kept + added)


def _parse(inherited: str | None) -> list[tuple[str | None, str]]:
    """Each entry of `inherited` as `(key, raw entry)`; `key` is None for an entry with
    no `=`, so the caller can keep it verbatim instead of guessing at its shape."""
    if not inherited:
        return []
    entries = []
    for raw in inherited.split(","):
        raw = raw.strip()
        if not raw:
            continue
        key = raw.split("=", 1)[0].strip() if "=" in raw else None
        entries.append((key, raw))
    return entries
