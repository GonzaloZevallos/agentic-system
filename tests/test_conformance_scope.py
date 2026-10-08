"""The doubles' conformance to the ports is only as good as its inventory and its bite
(GH-227, ADR-0021's 2026-10-08 addendum).

The inventory: every protocol `ports.py` defines is a `Ports` field, assigned in
`tests/conformance.py` (`COVERED`), or named with the reason mypy cannot see its stand-ins
(`UNCOVERED`). The bite: a protocol copied from `ports.py` with one signature altered is
refused by the same mypy this repo runs, and the unaltered copy is not (ADR-0023)."""

from __future__ import annotations

import inspect
import re
import subprocess
import sys
import typing
from collections.abc import Collection, Mapping
from pathlib import Path

from agentic.core.process.application import ports
from agentic.core.process.application.ports import Launcher, Ports, ProductStack, RunRegistry
from tests.conformance import COVERED, UNCOVERED

REPO = Path(__file__).resolve().parent.parent

_HEADER = (
    "from __future__ import annotations\n"
    "from typing import Protocol\n"
    "from agentic.core.process.application.ports import *\n\n"
)
_RENAME = ("class ProductStack(Protocol):", "class DriftedStack(Protocol):")
_DRIFT = ("def checks(self, revision: str)", "def checks(self, revision: int)")
_ASSERTION = "\n\ndef f(x: DriftedStack) -> None:\n    _s: ProductStack = x\n"


def _protocols_defined_in_ports() -> set[type]:
    return {
        obj
        for obj in vars(ports).values()
        if inspect.isclass(obj)
        and obj.__module__ == ports.__name__
        and getattr(obj, "_is_protocol", False)
        and obj is not typing.Protocol
    }


def _ports_field_types() -> set[type]:
    return {t for t in typing.get_type_hints(Ports).values() if inspect.isclass(t)}


def protocol_violations(
    protocols: Collection[type],
    port_fields: Collection[type],
    covered: Collection[type],
    uncovered: Mapping[type, str],
) -> list[str]:
    """What is wrong with how `protocols` are accounted for; empty is sound."""
    found: list[str] = []
    for protocol in sorted(protocols, key=lambda p: p.__name__):
        name = protocol.__name__
        in_ports, in_covered, in_uncovered = (
            protocol in port_fields,
            protocol in covered,
            protocol in uncovered,
        )
        if not (in_ports or in_covered or in_uncovered):
            found.append(f"{name} is a protocol of ports.py and is accounted for nowhere")
        if in_covered and in_uncovered:
            found.append(f"{name} is in both COVERED and UNCOVERED")
        if in_uncovered:
            reason = uncovered[protocol]
            if not reason.strip():
                found.append(f"{name} is UNCOVERED with an empty reason")
            elif not re.search(r"tests/\S+\.py:\d+", reason):
                found.append(f"{name} is UNCOVERED with a reason that names no tests/ file:line")
    return found


class _Alone(typing.Protocol):
    def one(self) -> None: ...


def test_the_real_inventory_of_protocols_is_accounted_for():
    inventory = _protocols_defined_in_ports()
    assert {Launcher, RunRegistry} <= inventory
    assert protocol_violations(inventory, _ports_field_types(), COVERED, UNCOVERED) == []


def test_a_protocol_accounted_for_nowhere_is_a_violation():
    found = protocol_violations({_Alone}, set(), set(), {})
    assert found == ["_Alone is a protocol of ports.py and is accounted for nowhere"]


def test_a_protocol_in_both_covered_and_uncovered_is_a_violation():
    reason = "stand-in at tests/x.py:1 stays unchecked"
    found = protocol_violations({_Alone}, set(), {_Alone}, {_Alone: reason})
    assert found == ["_Alone is in both COVERED and UNCOVERED"]


def test_an_uncovered_protocol_with_an_empty_reason_is_a_violation():
    found = protocol_violations({_Alone}, set(), set(), {_Alone: "  "})
    assert found == ["_Alone is UNCOVERED with an empty reason"]


def test_an_uncovered_reason_naming_no_test_location_is_a_violation():
    found = protocol_violations({_Alone}, set(), set(), {_Alone: "inline somewhere"})
    assert found == ["_Alone is UNCOVERED with a reason that names no tests/ file:line"]


def test_every_uncovered_reason_names_a_stand_in_that_exists():
    for protocol, reason in UNCOVERED.items():
        for path, line in re.findall(r"(tests/\S+\.py):(\d+)", reason):
            lines = (REPO / path).read_text(encoding="utf-8").splitlines()
            assert int(line) <= len(lines), f"{protocol.__name__}: {path}:{line} is past the end"
            assert lines[int(line) - 1].lstrip().startswith("class "), (
                f"{protocol.__name__}: {path}:{line} is no longer a class: {lines[int(line) - 1]!r}"
            )


def _once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    assert count == 1, f"{old!r} must match exactly once in ProductStack's source, matched {count}"
    return text.replace(old, new)


def _probe(tmp_path: Path, *, drift: bool) -> subprocess.CompletedProcess[str]:
    body = _once(inspect.getsource(ProductStack), *_RENAME)
    if drift:
        body = _once(body, *_DRIFT)
    probe = tmp_path / "drifted.py"
    probe.write_text(_HEADER + body + _ASSERTION, encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            "pyproject.toml",
            "--cache-dir",
            str(tmp_path / "cache"),
            str(probe),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )


def _errors(run: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in run.stdout.splitlines() if ": error:" in line]


def test_mypy_refuses_a_protocol_whose_method_signature_drifted(tmp_path):
    run = _probe(tmp_path, drift=True)
    errors = _errors(run)
    assert run.returncode != 0, run.stdout + run.stderr
    assert len(errors) == 1, run.stdout + run.stderr
    assert "Incompatible types in assignment" in errors[0]
    assert '"ProductStack"' in errors[0]
    assert errors[0].startswith(str(tmp_path / "drifted.py") + ":")


def test_mypy_accepts_the_same_protocol_copied_without_drift(tmp_path):
    run = _probe(tmp_path, drift=False)
    assert run.returncode == 0, run.stdout + run.stderr
    assert _errors(run) == []
