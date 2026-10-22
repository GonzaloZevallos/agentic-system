"""The `Tracker` fake's `ensure_label` (GH-294): what exists, what it created, and the mode
that fails the way `gh` does, for the landing's degradation to comment."""

import pytest

from tests.fakes import MemoryTracker


def test_ensure_label_creates_a_missing_label_once():
    tracker = MemoryTracker()
    tracker.ensure_label("needs-triage")
    tracker.ensure_label("needs-triage")
    assert tracker.created_labels == ["needs-triage"]
    assert "needs-triage" in tracker.existing_labels


def test_ensure_label_leaves_an_existing_label_alone():
    tracker = MemoryTracker()
    tracker.existing_labels.add("needs-triage")
    tracker.ensure_label("needs-triage")
    assert tracker.created_labels == []


def test_ensure_label_failure_mode_raises_value_error_and_creates_nothing():
    tracker = MemoryTracker()
    tracker.ensure_label_error = "gh label create failed: scripted"
    with pytest.raises(ValueError, match="scripted"):
        tracker.ensure_label("needs-triage")
    assert tracker.created_labels == [] and tracker.existing_labels == set()
