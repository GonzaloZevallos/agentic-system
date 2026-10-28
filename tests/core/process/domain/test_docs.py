"""The text the roles see when a product declares `docs:` (GH-398): the planning rule, the
review rule and the conflict line. Each is empty for a product that declares none, which is
what keeps every rendered text of such a product what it was before."""

from agentic.core.process.domain.docs import Docs, conflict_line, plan_text, review_text

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
