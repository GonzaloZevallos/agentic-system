"""The environment the system hands down to what it spawns."""

from pathlib import Path

from agentic.shared.configuration import Configuration
from agentic.shared.environment import (
    own_configuration_unset,
    without_own_configuration,
    without_own_toolchain,
)

UV_RUN = {
    "HOME": "/home/z",
    "PATH": "/repo/.venv/bin:/home/z/.local/bin:/usr/bin",
    "VIRTUAL_ENV": "/repo/.venv",
    "UV": "/home/z/.local/bin/uv",
    "UV_RUN_RECURSION_DEPTH": "1",
    "UVICORN_PORT": "8000",  # not uv's: a product's variable that happens to start alike
}


def test_the_systems_own_interpreter_is_stripped_from_what_it_spawns():
    env = without_own_toolchain(UV_RUN)
    assert env == {
        "HOME": "/home/z",
        "PATH": "/home/z/.local/bin:/usr/bin",
        "UVICORN_PORT": "8000",
    }
    assert UV_RUN["VIRTUAL_ENV"] == "/repo/.venv"  # the input is not touched


def test_the_venv_bin_is_matched_as_a_path_however_it_is_spelled():
    env = without_own_toolchain(
        {"PATH": "/repo/.venv/bin/:/usr/bin:/repo/.venv/bin", "VIRTUAL_ENV": "/repo/.venv/"}
    )
    assert env == {"PATH": "/usr/bin"}


def test_nothing_to_strip_is_a_copy():
    plain = {"PATH": "/usr/bin", "HOME": "/home/z"}
    assert without_own_toolchain(plain) == plain


OWN_RUN = {
    "PATH": "/usr/bin",
    "AGENTIC_PRODUCT_DIR": "/repos/agentic-system",
    "AGENTIC_VERBOSE": "1",
    "AGENTIC_SYSTEM_VERSION": "deadbee",
    "ANTHROPIC_API_KEY": "sk-x",  # not ours: a prefix that happens to start alike
}


def test_the_systems_own_configuration_is_stripped_from_what_it_spawns():
    assert without_own_configuration(OWN_RUN) == {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-x"}
    assert OWN_RUN["AGENTIC_VERBOSE"] == "1"  # the input is not touched


def test_what_cannot_be_dropped_is_overwritten_with_nothing():
    """The SDK merges this process's environment into every role's, so the only way to
    take a variable out of there is to hand it back empty."""
    assert own_configuration_unset(OWN_RUN) == {
        "AGENTIC_PRODUCT_DIR": "",
        "AGENTIC_VERBOSE": "",
        "AGENTIC_SYSTEM_VERSION": "",
    }


def test_an_empty_variable_is_unset_for_the_configuration_that_reads_it():
    """What makes the blanking work at all (`Configuration.from_sources`)."""
    cfg = Configuration.from_sources(
        {"product_dir": Path("/p")}, {"AGENTIC_PRODUCT_DIR": "", "AGENTIC_VERBOSE": ""}
    )
    assert cfg.product_dir == Path("/p")
    assert cfg.verbose is False
