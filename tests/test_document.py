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
        "failures": [{"role": "PM", "attempt": 1, "error": "x", "cost_usd": 0.01}],
        "runs": [
            {
                "role": "PjM",
                "version": 1,
                "session_id": "s1",
                "model": "m",
                "cost_usd": 0.1,
                "turns": 1,
            },
            {
                "role": "Product Analyst",
                "version": 1,
                "session_id": "s2",
                "model": "m",
                "cost_usd": 0.2,
                "turns": 2,
            },
        ],
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
    assert data["failures"] == state["failures"]
    assert data["runs"] == state["runs"]
    assert data["cost_usd"] == 0.1235
    assert data["prompts"] == {"pjm": "abcd1234"}
    assert data["description"] == "Users want CSV."
    assert data["sections"] == state["sections"]


def test_superseded_sections_are_written_before_the_current_one(tmp_path):
    state = {
        "id": "T-2",
        "title": "t",
        "description": "d",
        "sections": {
            "PjM": "classified",
            "Product Analyst": "v3 current",
            "PM": "approved",
            # Insertion order is the merge order; the writer must not rely on it.
            "Product Analyst (v2)": "second",
            "Product Analyst (v1)": "first",
            "PM (v1)": "returned",
        },
    }
    text = document.write(state, tmp_path).read_text()
    order = [line[3:] for line in text.splitlines() if line.startswith("## ")]
    assert order == [
        "Original request",
        "PjM",
        "Product Analyst (v1)",
        "Product Analyst (v2)",
        "Product Analyst",
        "PM (v1)",
        "PM",
    ]
    assert document.read(tmp_path / ".agentic/tasks/T-2.md")["sections"] == document.ordered(
        state["sections"]
    )


def test_read_rejects_missing_frontmatter(tmp_path):
    path = tmp_path / "bad.md"
    path.write_text("# no frontmatter\n")
    try:
        document.read(path)
    except ValueError as exc:
        assert "frontmatter" in str(exc)
    else:
        raise AssertionError("expected ValueError")
