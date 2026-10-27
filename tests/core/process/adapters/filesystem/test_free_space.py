"""`StatvfsFreeSpace`: what a real volume reports, read through `os.statvfs` (GH-391).
Only the shape is asserted on the real filesystem: no test depends on how much is free."""

import os

from agentic.core.process.adapters.filesystem.free_space import StatvfsFreeSpace
from agentic.core.process.domain.scratch import SpaceReading


def test_a_real_directory_reports_positive_bytes_and_inodes(tmp_path):
    reading = StatvfsFreeSpace().read(tmp_path)
    assert isinstance(reading, SpaceReading)
    assert reading.free_bytes > 0 and reading.free_inodes > 0


def test_bytes_are_what_an_unprivileged_user_may_write_and_inodes_the_available_ones(
    tmp_path, monkeypatch
):
    fake = os.statvfs_result((4096, 4096, 100, 90, 25, 20, 9, 7, 0, 255))
    monkeypatch.setattr(os, "statvfs", lambda _path: fake)
    assert StatvfsFreeSpace().read(tmp_path) == SpaceReading(free_bytes=25 * 4096, free_inodes=7)


def test_a_directory_not_made_yet_is_read_through_its_nearest_existing_parent(tmp_path):
    reading = StatvfsFreeSpace().read(tmp_path / "not" / "yet" / "made")
    assert reading == StatvfsFreeSpace().read(tmp_path)
