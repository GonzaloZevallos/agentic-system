"""The text the roles see when a product declares `docs:` (GH-398): the planning rule, the
review rule and the conflict line. Each is empty for a product that declares none, which is
what keeps every rendered text of such a product what it was before."""

from agentic.core.process.domain import docs as docs_module
from agentic.core.process.domain.docs import (
    DIAGRAM_BUDGET,
    DiagramBudget,
    Docs,
    conflict_line,
    plan_text,
    review_text,
)

DOCS = Docs(
    generate="uv run python tools/diagrams.py",
    paths=("docs/architecture/generated/**", "docs/api/*.md"),
)


def test_each_text_is_empty_for_a_product_without_docs():
    assert plan_text(None) == ""
    assert review_text(None) == ""
    assert conflict_line(None) == ""


def test_the_plan_text_puts_the_architecture_docs_in_the_subtask_that_changes_it():
    text = plan_text(DOCS)
    assert (
        "a container, a component, a contract between modules, the data model or a key flow" in text
    )
    assert "that same subtask" in text
    assert "`docs/architecture/`" in text
    assert "`docs/README.md`" in text
    assert "Mermaid" in text
    assert "sequence diagrams of key flows" in text


def test_the_plan_text_has_the_first_architecture_subtask_create_a_missing_readme():
    assert (
        "If `docs/README.md` does not exist at the base, the first architecture subtask "
        "creates it." in plan_text(DOCS)
    )


def test_the_plan_text_shows_generate_and_the_owned_paths_and_forbids_hand_edits():
    text = plan_text(DOCS)
    assert "`uv run python tools/diagrams.py`" in text
    assert "`docs/architecture/generated/**`" in text
    assert "`docs/api/*.md`" in text
    assert "never edited by hand" in text


def test_the_plan_text_sets_the_spanish_rule_and_leaves_the_files_to_the_developer():
    text = plan_text(DOCS)
    assert "Spanish (Rioplatense)" in text
    assert "identifiers and diagram names stay in English" in text
    assert "cites the file or symbol it rests on" in text
    assert "You never write the files; the Developer of the subtask does." in text


def test_the_review_text_blocks_a_changed_boundary_with_no_doc_change_and_asks_for_evidence():
    text = review_text(DOCS)
    assert "`blocking`" in text
    assert "no change under `docs/architecture/` or `docs/README.md` in the same subtask" in text
    assert "git diff --stat <base> -- docs/architecture docs/README.md" in text
    assert "the cited import, signature or model line" in text


def test_the_review_text_files_unreferenced_claims_and_non_spanish_prose_as_important():
    text = review_text(DOCS)
    assert "A claim with no file or symbol reference" in text
    assert "prose not in Spanish" in text
    assert "`important`" in text
    assert "only on the lines the diff adds or changes" in text


def test_the_review_text_marks_the_rest_of_a_touched_doc_outside():
    assert "scope `outside`" in review_text(DOCS)


def test_the_review_text_never_blocks_for_a_missing_readme_alone():
    text = review_text(DOCS)
    assert "only when the file exists at the base" in text
    assert "A missing one is never `blocking` by itself" in text
    assert "the plan said to create it and the subtask did not" in text


def test_the_review_text_leaves_generated_files_to_the_docs_check():
    text = review_text(DOCS)
    assert "`docs/architecture/generated/**`" in text
    assert "`uv run python tools/diagrams.py`" in text


def test_the_conflict_line_regenerates_instead_of_merging_by_hand():
    line = conflict_line(DOCS)
    assert "`docs/architecture/generated/**`" in line
    assert "do not merge them by hand" in line
    assert "run `uv run python tools/diagrams.py` and commit its output" in line


# The texts as they were before the diagram budget (GH-430), captured from the code at that
# commit: the budget rule only appends to them.
PLAN_BEFORE = (
    "\nThis product declares technical docs (`docs:` in `.agentic/stack.yaml`). When a subtask "
    "changes architecture (a container, a component, a contract between modules, the data model "
    "or a key flow), that same subtask updates the written docs: the C4 context, container and "
    "component views and the sequence diagrams of key flows, in Mermaid, under "
    "`docs/architecture/`, and the technical sections of `docs/README.md` (architecture, API or "
    "CLI reference, ADR index). If `docs/README.md` does not exist at the base, the first "
    "architecture subtask creates it. Generated files under `docs/architecture/generated/**`, "
    "`docs/api/*.md` are never edited by hand: the brief has the Developer regenerate them by "
    "running `uv run python tools/diagrams.py`. The prose is in Spanish (Rioplatense); code, "
    "identifiers and diagram names stay in English; every claim cites the file or symbol it "
    "rests on. You never write the files; the Developer of the subtask does."
)
REVIEW_BEFORE = (
    "\n\nThis product declares technical docs (`docs:`). Review `docs/architecture/` and "
    "`docs/README.md` against the code, on the diff:\n\n"
    "- A boundary changed in the diff (a container, a component, a contract between modules, the "
    "data model or a key flow) with no change under `docs/architecture/` or `docs/README.md` in "
    "the same subtask is a `blocking` finding. Its evidence is the cited import, signature or "
    "model line plus an empty `git diff --stat <base> -- docs/architecture docs/README.md`.\n"
    "- A claim with no file or symbol reference, and prose not in Spanish, are `important` "
    "findings, judged only on the lines the diff adds or changes under those targets. Anything "
    "else in a doc the diff touches is scope `outside`.\n"
    "- A stale `docs/README.md` is raised only when the file exists at the base. A missing one "
    "is never `blocking` by itself; it is `important` only when the plan said to create it and "
    "the subtask did not.\n"
    "- Generated files under `docs/architecture/generated/**`, `docs/api/*.md` belong to the "
    "`docs` check: never ask for a hand edit, the fix is to run "
    "`uv run python tools/diagrams.py`."
)
CONFLICT_BEFORE = (
    " For conflicted files under `docs/architecture/generated/**`, `docs/api/*.md`, do not merge "
    "them by hand: take either side, run `uv run python tools/diagrams.py` and commit its output."
)


def test_the_diagram_budget_is_twenty_nodes_thirty_edges_fifteen_classes():
    assert (DIAGRAM_BUDGET.nodes, DIAGRAM_BUDGET.edges, DIAGRAM_BUDGET.classes) == (20, 30, 15)


def test_the_conflict_line_is_what_it_was_before_the_budget():
    assert conflict_line(DOCS) == CONFLICT_BEFORE


def test_the_plan_text_keeps_its_old_text_as_a_prefix_and_adds_the_budget_rule():
    text = plan_text(DOCS)
    assert text.startswith(PLAN_BEFORE)
    tail = text[len(PLAN_BEFORE) :]
    for number in (DIAGRAM_BUDGET.nodes, DIAGRAM_BUDGET.edges, DIAGRAM_BUDGET.classes):
        assert str(number) in tail
    assert "by meaning, never by count alone" in tail
    assert "in Spanish what it shows" in tail
    assert "parent and siblings" in tail


def test_the_review_text_keeps_its_old_text_as_a_prefix_and_adds_the_budget_bullet():
    text = review_text(DOCS)
    assert text.startswith(REVIEW_BEFORE)
    tail = text[len(REVIEW_BEFORE) :]
    assert tail.startswith("\n- ")
    for number in (DIAGRAM_BUDGET.nodes, DIAGRAM_BUDGET.edges, DIAGRAM_BUDGET.classes):
        assert str(number) in tail
    assert "`important`" in tail
    assert "counted nodes, edges and classes" in tail
    assert "parent and siblings" in tail


def test_the_budget_numbers_in_the_texts_follow_the_constant(monkeypatch):
    monkeypatch.setattr(
        docs_module, "DIAGRAM_BUDGET", DiagramBudget(nodes=71, edges=72, classes=73)
    )
    for text in (plan_text(DOCS), review_text(DOCS)):
        assert "71" in text and "72" in text and "73" in text
