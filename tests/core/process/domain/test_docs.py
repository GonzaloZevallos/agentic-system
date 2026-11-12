"""The text the roles see when a product declares `docs:` (GH-398): the planning rule, the
review rule and the conflict line. Each is empty for a product that declares none, which is
what keeps every rendered text of such a product what it was before."""

import subprocess
from pathlib import Path

import pytest

from agentic.core.process.domain import docs as docs_module
from agentic.core.process.domain.docs import (
    DIAGRAM_BUDGET,
    DiagramBudget,
    Docs,
    conflict_line,
    owned,
    plan_text,
    review_text,
    settle_source,
    split_conflicts,
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


# What the three texts said before `Docs.mode` existed, pinned byte for byte (GH-603): a
# product in `commit` mode, or one that declares no mode, must keep every one of them.
PINNED_PLAN = (
    "\n"
    "This product declares technical docs (`docs:` in `.agentic/stack.yaml`"
    "). When a subtask changes architecture (a container, a component, a co"
    "ntract between modules, the data model or a key flow), that same subta"
    "sk updates the written docs: the C4 context, container and component v"
    "iews and the sequence diagrams of key flows, in Mermaid, under `docs/a"
    "rchitecture/`, and the technical sections of `docs/README.md` (archite"
    "cture, API or CLI reference, ADR index). If `docs/README.md` does not "
    "exist at the base, the first architecture subtask creates it. Generate"
    "d files under `docs/architecture/generated/**`, `docs/api/*.md` are ne"
    "ver edited by hand: the brief has the Developer regenerate them by run"
    "ning `uv run python tools/diagrams.py`. The prose is in Spanish (Riopl"
    "atense); code, identifiers and diagram names stay in English; every cl"
    "aim cites the file or symbol it rests on. You never write the files; t"
    "he Developer of the subtask does. A Mermaid diagram over budget (20 no"
    "des or 30 edges for a flowchart or graph, 15 classes for a classDiagra"
    "m) is split by meaning, never by count alone; each split page is a `.m"
    "d` that starts by saying in Spanish what it shows and lists its parent"
    " and siblings."
)
PINNED_REVIEW = (
    "\n"
    "\n"
    "This product declares technical docs (`docs:`). Review `docs/architect"
    "ure/` and `docs/README.md` against the code, on the diff:\n"
    "\n"
    "- A boundary changed in the diff (a container, a component, a contract"
    " between modules, the data model or a key flow) with no change under `"
    "docs/architecture/` or `docs/README.md` in the same subtask is a `bloc"
    "king` finding. Its evidence is the cited import, signature or model li"
    "ne plus an empty `git diff --stat <base> -- docs/architecture docs/REA"
    "DME.md`.\n"
    "- A claim with no file or symbol reference, and prose not in Spanish, "
    "are `important` findings, judged only on the lines the diff adds or ch"
    "anges under those targets. Anything else in a doc the diff touches is "
    "scope `outside`.\n"
    "- A stale `docs/README.md` is raised only when the file exists at the "
    "base. A missing one is never `blocking` by itself; it is `important` o"
    "nly when the plan said to create it and the subtask did not.\n"
    "- Generated files under `docs/architecture/generated/**`, `docs/api/*."
    "md` belong to the `docs` check: never ask for a hand edit, the fix is "
    "to run `uv run python tools/diagrams.py`.\n"
    "- A diagram under `docs/architecture/` over budget (more than 20 nodes"
    " or 30 edges in a flowchart or graph, more than 15 classes in a classD"
    "iagram) is an `important` finding. Its evidence is the counted nodes, "
    "edges and classes against the budget. The fix is to split it by meanin"
    "g into `.md` pages that say what they show and list their parent and s"
    "iblings."
)
PINNED_CONFLICT = (
    " For conflicted files under `docs/architecture/generated/**`, `docs/api/*.md`, do not "
    "merge them by hand: take either side, run `uv run python tools/diagrams.py` and commit "
    "its output."
)


COMMIT_MODES = (DOCS, Docs(DOCS.generate, DOCS.paths, "commit"))


@pytest.mark.parametrize("docs", COMMIT_MODES)
def test_commit_mode_and_an_absent_mode_render_the_texts_byte_for_byte_as_before(docs):
    assert plan_text(docs) == PINNED_PLAN
    assert review_text(docs) == PINNED_REVIEW
    assert conflict_line(docs) == PINNED_CONFLICT


VALIDATE = Docs(DOCS.generate, DOCS.paths, "validate")


def test_the_mode_defaults_to_commit():
    assert DOCS.mode == "commit"


def test_validate_plan_text_keeps_generated_files_out_of_a_feature_branch():
    text = plan_text(VALIDATE)
    assert "`docs/architecture/generated/**`, `docs/api/*.md`" in text
    assert "never edited nor committed in a feature branch" in text
    assert "master regenerates them after each merge" in text
    assert "regenerate them by running" not in text
    assert "no brief asks the Developer to run `uv run python tools/diagrams.py`" in text
    assert "A Mermaid diagram over budget" in text


def test_validate_review_text_says_the_owned_paths_belong_to_the_system():
    text = review_text(VALIDATE)
    assert "belong to the system" in text
    assert "never ask for a hand edit or for a regeneration" in text
    assert "the delivery gate restores them" in text
    assert "the fix is to run" not in text
    assert text.startswith(REVIEW_BEFORE.split("- Generated files")[0])
    assert "A diagram under `docs/architecture/` over budget" in text


def test_validate_conflict_line_leaves_the_owned_paths_to_the_system():
    line = conflict_line(VALIDATE)
    assert "The system resolves a conflict confined to" in line
    assert "`docs/architecture/generated/**`, `docs/api/*.md`" in line
    assert "you never touch those files" in line
    assert "commit its output" not in line


def test_the_validate_texts_are_still_empty_for_a_product_without_docs():
    assert conflict_line(None) == plan_text(None) == review_text(None) == ""


SAMPLE_FILES = (
    "top.txt",
    "a/b.txt",
    "a/c/d.txt",
    "a/c/e/f.txt",
    "site/api/x.txt",
    "site/api/y.txt",
    "site/api/sub/z.txt",
    "site/architecture/generated/g.mmd",
    "site/architecture/generated/deep/h.mmd",
    "site/architecture/written.txt",
    "with space/x.txt",
    "with space/sub dir/y.txt",
    "[odd]/q.txt",
)
GLOB_CASES = (
    "site/architecture/generated/**",
    "site/**",
    "**/*.txt",
    "**/x.txt",
    "site/api/*.txt",
    "site/api/*",
    "site/*/x.txt",
    "site/**/z.txt",
    "site/**/h.mmd",
    "a/**/d.txt",
    "a/**/b.txt",
    "a/*/d.txt",
    "a/*",
    "a",
    "site/api",
    "site/ap?/x.txt",
    "site/api/[xy].txt",
    "site/api/[!x].txt",
    "with space/*",
    "with space/**",
    "site/architecture/generated/**/*.mmd",
    "nothing/**",
    "site/api/x.txt",
)


@pytest.fixture(scope="module")
def sample_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    repo = tmp_path_factory.mktemp("sample")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    for name in SAMPLE_FILES:
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    return repo


@pytest.mark.parametrize("pattern", GLOB_CASES)
def test_owned_agrees_with_git_glob_pathspecs_on_a_sample_tree(sample_repo, pattern):
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", f":(glob){pattern}"],
        cwd=sample_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    expected = {name for name in listed if name}
    block = Docs("true", (pattern,))
    assert {name for name in SAMPLE_FILES if owned(block, name)} == expected


def test_owned_is_true_when_any_of_the_paths_matches_and_false_for_none():
    block = Docs("true", ("site/api/*.txt", "site/architecture/generated/**"))
    assert owned(block, "site/api/x.txt")
    assert owned(block, "site/architecture/generated/deep/h.mmd")
    assert not owned(block, "site/api/sub/z.txt")
    assert not owned(block, "site/architecture/written.txt")
    assert not owned(block, "src/app.py")


def test_split_conflicts_separates_the_owned_paths_with_the_matcher_alone():
    owned_paths, others = split_conflicts(
        DOCS,
        ("docs/api/a.md", "app/main.py", "docs/architecture/generated/x/y.md", "docs/api/a/b.md"),
    )
    assert owned_paths == ("docs/api/a.md", "docs/architecture/generated/x/y.md")
    assert others == ("app/main.py", "docs/api/a/b.md")


def test_split_conflicts_of_nothing_is_nothing():
    assert split_conflicts(DOCS, ()) == ((), ())


@pytest.mark.parametrize(
    ("ancestry", "expected"),
    [
        ("same", "ours"),
        ("ours-older", "theirs"),
        ("theirs-older", "ours"),
    ],
)
def test_settle_source_is_the_newer_of_the_two_master_bases(ancestry, expected):
    assert settle_source("ours", "theirs", ancestry) == expected


def test_settle_source_is_none_for_bases_that_are_not_comparable():
    assert settle_source("ours", "theirs", "diverged") is None
