"""Nothing outside `shared/configuration.py` builds the input of `arm_label` by naming the
switches (GH-471): a dict literal keyed by an `ARM_SWITCHES` member is how a label input
was hand-built three times, and how a new switch would have been left out of two of them.
Every caller goes through `arm_input(cfg)`."""

import ast
from pathlib import Path

import agentic
from agentic.shared.configuration import ARM_SWITCHES

PACKAGE = Path(agentic.__file__).parent
OWNER = PACKAGE / "shared" / "configuration.py"


def naming_switches(source: str) -> list[int]:
    """The lines of `source` with a dict literal, or a `dict(...)` call, keyed by a switch."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            if any(key in ARM_SWITCHES for key in keys):
                lines.append(node.lineno)
        elif isinstance(node, ast.Call) and getattr(node.func, "id", None) == "dict":
            if any(kw.arg in ARM_SWITCHES for kw in node.keywords):
                lines.append(node.lineno)
    return lines


def test_the_scan_refuses_a_hand_built_label_input():
    assert naming_switches('x = {"review_mode": cfg.review_mode, "other": 1}') == [1]
    assert naming_switches('x = {"plan_shape": cfg.plan_shape}') == [1]
    assert naming_switches("x = dict(skip_ephemeral=str(cfg.skip_ephemeral))") == [1]
    assert naming_switches('x = {"experiment": "e", "switches": switches}') == []
    assert naming_switches("x = arm_input(cfg)") == []


def test_no_module_but_the_configuration_names_the_switches_in_a_dict():
    found = {
        f"{path.relative_to(PACKAGE)}:{line}"
        for path in PACKAGE.rglob("*.py")
        if path != OWNER
        for line in naming_switches(path.read_text())
    }
    assert found == set(), "build the label input with shared.configuration.arm_input"
