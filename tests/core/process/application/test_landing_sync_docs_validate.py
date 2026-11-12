"""GH-603, docs `validate` mode at the landing sync's upstream merge step: a conflict confined to
`docs.paths` is settled from the upstream sha and the sync concludes with no Developer; a mixed
one reopens the unit with only the other paths; `commit` mode reopens it with every path."""

from __future__ import annotations

import pytest

from agentic.core.process.application.ports import UpstreamMerge
from agentic.core.process.domain.docs import Docs
from tests.core.process.application.test_landing_sync import (
    HEAD,
    MASTER,
    _behind,
    _diffs,
    _unit,
)
from tests.fakes import MERGE_BASE

OWNED = "docs/api/a.md"
VALIDATE = Docs("make docs", ("docs/api/**",), mode="validate")
COMMIT = Docs("make docs", ("docs/api/**",))


def _conflicted(tmp_path, docs: Docs | None, *paths: str):
    ports, world = _behind(tmp_path)
    ports.stack.declared_docs = docs
    ports.workspace.upstream_merges[MASTER] = UpstreamMerge(
        clean=False, conflicted=paths, head=HEAD
    )
    ports.workspace.ancestries[(MERGE_BASE, MERGE_BASE)] = "same"
    _diffs(ports, merged=("upstream.py",), own=("feature.py",))
    return ports, world


def test_a_docs_only_conflict_concludes_the_sync_with_no_developer(tmp_path):
    ports, world = _conflicted(tmp_path, VALIDATE, OWNED)
    world.tick()
    [(_, paths, source)] = ports.workspace.settle_owned_calls
    assert (paths, source) == ((OWNED,), MERGE_BASE)
    assert ports.workspace.commits  # the merge was concluded
    assert ports.workspace.remote_tip_calls == []  # master is the upstream sha, not re-read
    assert world.resumed == [] and ports.workspace.pushed
    assert _unit(ports)["status"] != "returned"


def test_a_mixed_conflict_reopens_the_unit_with_only_the_other_paths(tmp_path):
    ports, world = _conflicted(tmp_path, VALIDATE, OWNED, "shared.py")
    world.tick()
    assert [call[1] for call in ports.workspace.settle_owned_calls] == [(OWNED,)]
    assert ports.workspace.commits == []
    unit = _unit(ports)
    assert unit["status"] == "returned"
    assert "shared.py" in unit["reason"] and OWNED not in unit["reason"]
    assert world.resumed == [5]


@pytest.mark.parametrize("docs", [COMMIT, None])
def test_commit_mode_reopens_the_unit_with_every_conflicted_path(tmp_path, docs):
    ports, world = _conflicted(tmp_path, docs, OWNED, "shared.py")
    world.tick()
    assert ports.workspace.settle_owned_calls == []
    unit = _unit(ports)
    assert unit["status"] == "returned"
    assert "shared.py" in unit["reason"] and OWNED in unit["reason"]


def test_a_settlement_that_fails_reopens_the_unit_with_the_whole_conflict(tmp_path):
    ports, world = _conflicted(tmp_path, VALIDATE, OWNED)
    ports.workspace.settle_owned_error = "boom"
    world.tick()
    unit = _unit(ports)
    assert unit["status"] == "returned" and OWNED in unit["reason"]
