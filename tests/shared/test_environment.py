"""The environment the system hands down to what it spawns."""

from agentic.shared.environment import without_own_toolchain

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
