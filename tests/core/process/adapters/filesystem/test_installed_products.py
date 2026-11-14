"""`SystemdUnitInstalledProducts` (GH-726) over a real directory: the units it reads, the dirs it
returns, and every way a unit dir or a unit is unreadable. Only `tmp_path` is read, never the
real `~/.config/systemd/user`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentic.core.process.adapters.filesystem.installed_products import (
    SystemdUnitInstalledProducts,
)
from agentic.core.process.domain.stage0_rotation import Unreadable


def unit(directory: Path, name: str, product: str) -> Path:
    path = directory / name
    path.write_text(f'[Service]\nExecStart="/s/agentic" poll --product-dir "{product}"\n')
    return path


def test_two_units_give_their_two_product_dirs_sorted(tmp_path: Path) -> None:
    unit(tmp_path, "agentic-poll-b.service", "/srv/b")
    unit(tmp_path, "agentic-poll-a.service", "/srv/a")

    assert SystemdUnitInstalledProducts(tmp_path).read() == (Path("/srv/a"), Path("/srv/b"))


def test_only_agentic_poll_services_are_read(tmp_path: Path) -> None:
    unit(tmp_path, "agentic-poll-a.service", "/srv/a")
    (tmp_path / "other.service").write_text("not a unit")
    (tmp_path / "agentic-poll-a.service.bak").write_text("not a unit")
    (tmp_path / "agentic-poll-x.service.d").mkdir()

    assert SystemdUnitInstalledProducts(tmp_path).read() == (Path("/srv/a"),)


def test_one_unit_that_is_not_understood_makes_the_whole_read_unreadable(tmp_path: Path) -> None:
    unit(tmp_path, "agentic-poll-a.service", "/srv/a")
    (tmp_path / "agentic-poll-b.service").write_text("[Service]\nExecStart=/bin/true\n")
    unit(tmp_path, "agentic-poll-c.service", "/srv/c")

    assert SystemdUnitInstalledProducts(tmp_path).read() == Unreadable()


def test_no_units_is_an_empty_tuple(tmp_path: Path) -> None:
    assert SystemdUnitInstalledProducts(tmp_path).read() == ()


def test_a_missing_dir_is_an_empty_tuple(tmp_path: Path) -> None:
    assert SystemdUnitInstalledProducts(tmp_path / "absent").read() == ()


def test_a_path_that_is_not_a_dir_is_unreadable(tmp_path: Path) -> None:
    file = tmp_path / "file"
    file.write_text("")

    assert SystemdUnitInstalledProducts(file).read() == Unreadable()


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any mode")
def test_a_dir_that_cannot_be_listed_is_unreadable(tmp_path: Path) -> None:
    units = tmp_path / "units"
    units.mkdir()
    unit(units, "agentic-poll-a.service", "/srv/a")
    units.chmod(0)
    try:
        assert SystemdUnitInstalledProducts(units).read() == Unreadable()
    finally:
        units.chmod(0o700)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any mode")
def test_a_unit_that_cannot_be_read_is_unreadable(tmp_path: Path) -> None:
    unit(tmp_path, "agentic-poll-a.service", "/srv/a")
    unit(tmp_path, "agentic-poll-b.service", "/srv/b").chmod(0)

    assert SystemdUnitInstalledProducts(tmp_path).read() == Unreadable()


def test_a_unit_that_is_not_text_is_unreadable(tmp_path: Path) -> None:
    (tmp_path / "agentic-poll-a.service").write_bytes(b"\xff\xfe\x00")

    assert SystemdUnitInstalledProducts(tmp_path).read() == Unreadable()


def test_a_dir_two_units_name_is_returned_once(tmp_path: Path) -> None:
    unit(tmp_path, "agentic-poll-a.service", "/srv/a")
    unit(tmp_path, "agentic-poll-b.service", "/srv/a")
    unit(tmp_path, "agentic-poll-c.service", "/srv/c")

    assert SystemdUnitInstalledProducts(tmp_path).read() == (Path("/srv/a"), Path("/srv/c"))
