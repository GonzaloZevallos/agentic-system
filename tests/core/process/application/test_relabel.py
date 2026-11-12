"""GH-591 part 2: the owner's labels are read again on a resume (resume entry)."""

from agentic.core.process.application.ports import IssueState
from agentic.core.process.application.relabel import relabelled
from tests.fakes import memory_ports


def _ports(labels):
    ports = memory_ports()
    ports.tracker.states[7] = IssueState("OPEN", "", (), None, labels)
    return ports


LIGHT = {"id": "GH-7", "path": "light", "initial_path": "light", "path_label": "none"}


def test_path_full_promotes_a_light_task_with_the_owner_as_the_role():
    patch = relabelled(LIGHT, _ports(("path:full",)))
    assert patch["path"] == "full"
    assert patch["promotion"]["from_role"] == "owner"
    assert patch["promotion"]["kind"] == "owner"
    assert "initial_path" not in patch


def test_no_label_and_path_light_change_nothing():
    assert relabelled(LIGHT, _ports(())) == {}
    assert relabelled(LIGHT, _ports(("path:light",))) == {}


def test_path_light_never_demotes_a_full_task():
    full = {**LIGHT, "path": "full"}
    assert relabelled(full, _ports(("path:light",))) == {}


def test_unreadable_labels_change_nothing():
    ports = memory_ports()  # the issue was never scripted: the tracker raises ValueError
    assert relabelled(LIGHT, ports) == {}


def test_a_path_full_label_on_an_already_full_task_adds_no_promotion():
    full = {**LIGHT, "path": "full"}
    assert "promotion" not in relabelled(full, _ports(("path:full",)))
