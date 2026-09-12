from agentic import document


def test_write_then_read_roundtrip(tmp_path):
    state = {
        "id": "T-1",
        "title": "Export report",
        "description": "Users want CSV.",
        "type": "feature",
        "status": "definition_ready",
        "current_role": "PM",
        "flags": {"touches_ui": False},
        "returns": {"PM→Product Analyst": 1},
        "failures": [{"role": "PM", "attempt": 1, "error": "x"}],
        "escalated": None,
        "cost_usd": 0.12345,
        "prompts": {"pjm": "abcd1234"},
        "sections": {
            "PjM": "Clear feature.",
            # Sub-headings inside a section must not split it.
            "Product Analyst": "### Competitors\n\nNone.\n\n### For the next role\n\nValidate.",
        },
    }
    path = document.write(state, tmp_path)
    assert path == tmp_path / ".agentic/tasks/T-1.md"

    data = document.read(path)
    assert data["id"] == "T-1"
    assert data["type"] == "feature"
    assert data["returns"] == {"PM→Product Analyst": 1}
    assert data["failures"] == 1
    assert data["cost_usd"] == 0.1235
    assert data["prompts"] == {"pjm": "abcd1234"}
    assert data["description"] == "Users want CSV."
    assert data["sections"] == state["sections"]


def test_read_rejects_missing_frontmatter(tmp_path):
    path = tmp_path / "bad.md"
    path.write_text("# no frontmatter\n")
    try:
        document.read(path)
    except ValueError as exc:
        assert "frontmatter" in str(exc)
    else:
        raise AssertionError("expected ValueError")
