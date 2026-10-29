"""The owner's decisions file the system ships (GH-431): the real `docs/decisiones-del-dueño.md`
read the way a rules module reads it, the constants its entries state, the merge attribute that
lets parallel branches append, and the prompts that tell a role to apply a live rule.

`rules-domain` (a sibling subtask) owns `parse_file` and `entry_id`; they are not on this branch,
so `parse_entries` and `entry_id` below follow the same format and the same normalisation. When
the branches integrate they should be swapped for the production functions."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pytest

from agentic.core.knowledge.domain import knowledge, knowledge_index, owner_rules
from agentic.core.process.adapters.filesystem.prompts import FilesystemPrompts
from agentic.core.process.application.preflight import PLACEHOLDERS
from agentic.core.process.domain import playbook, roles, rules

REPO = Path(__file__).resolve().parent.parent
DECISIONS = REPO / "docs" / "decisiones-del-dueño.md"
ADRS = REPO / "docs" / "adr"

_HEADING = re.compile(
    r"### (?P<id>D-[0-9a-f]{8}) · (?P<date>\d{4}-\d{2}-\d{2})"
    r"(?: \(reemplazada por (?P<by>D-[0-9a-f]{8})\))?"
)
_METADATA = ("Origen:", "Reemplaza:", "Rozan:")


def entry_id(text: str) -> str:
    normal = re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip().casefold()
    return "D-" + hashlib.sha256(normal.encode("utf-8")).hexdigest()[:8]


@dataclass(frozen=True)
class Entry:
    id: str
    text: str
    struck: bool
    replaces: str | None
    rozan: tuple[str, ...]
    raw: str


def parse_entries(text: str) -> list[Entry]:
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if _HEADING.fullmatch(line.rstrip())]
    entries = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        body = lines[start + 1 : end]
        heading = _HEADING.fullmatch(lines[start].rstrip())
        assert heading is not None
        rule = next((x.strip() for x in body if x.strip() and not x.startswith(_METADATA)), "")
        struck = re.fullmatch(r"~~(.*)~~", rule)
        replaces = next(
            (x.split(":", 1)[1].strip() for x in body if x.startswith("Reemplaza:")), None
        )
        rozan = tuple(
            a for x in body if x.startswith("Rozan:") for a in re.findall(r"ADR-\d{4}", x)
        )
        entries.append(
            Entry(
                heading["id"],
                struck.group(1) if struck else rule,
                bool(struck),
                replaces,
                rozan,
                "\n".join(lines[start:end]).strip(),
            )
        )
    return entries


def problems(text: str) -> list[str]:
    """What is wrong with a decisions file, one line each."""
    found = []
    entries = parse_entries(text)
    ids = {entry.id for entry in entries}
    if "{{" in text or "}}" in text:
        found.append("a template marker")
    for entry in entries:
        if entry.id != entry_id(entry.text):
            found.append(f"{entry.id}: the id is not the hash of its text")
        if entry.replaces and entry.replaces not in ids:
            found.append(f"{entry.id}: Reemplaza: {entry.replaces} points to no entry")
    for entry_id_ in ids:
        copies = {_unstruck(e.raw) for e in entries if e.id == entry_id_}
        if len(copies) > 1:
            found.append(f"{entry_id_}: repeated with different text")
    return found


def _unstruck(raw: str) -> str:
    return re.sub(r"~~|\s\(reemplazada por D-[0-9a-f]{8}\)", "", raw)


def attributes_problems(text: str) -> list[str]:
    """What `.gitattributes` lacks for the decisions files to merge as appends."""
    declared = {
        unicodedata.normalize("NFC", line.split()[0]): line.split()[1:]
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    }
    found = [
        f"{path}: no merge=union"
        for path in knowledge.OWNER_DECISIONS
        if declared.get(path) != ["merge=union"]
    ]
    found += [
        f"{path}: not an owner file" for path in declared if path not in knowledge.OWNER_DECISIONS
    ]
    return found


# --- The real file ---


def test_the_real_file_has_no_problem():
    assert problems(DECISIONS.read_text(encoding="utf-8")) == []


def test_the_seed_has_the_system_rules():
    assert len(parse_entries(DECISIONS.read_text(encoding="utf-8"))) >= 18


def test_the_seed_is_in_force_whole():
    text = DECISIONS.read_text(encoding="utf-8")
    assert owner_rules.live(text) == text


def test_a_wrong_id_a_dangling_reemplaza_and_a_template_marker_are_each_reported():
    good = "### D-00000000 · 2026-10-28\nUna regla.\nOrigen: x\n"
    wrong_id = problems(good)
    dangling = problems(f"### {entry_id('Otra.')} · 2026-10-28\nOtra.\nReemplaza: D-ffffffff\n")
    marked = "Hola {{x}}"
    template = problems(f"### {entry_id(marked)} · 2026-10-28\n{marked}\n")
    assert "the id is not the hash" in wrong_id[0]
    assert "points to no entry" in dangling[0]
    assert template == ["a template marker"]


def test_a_repeated_id_is_reported_unless_the_copies_differ_only_by_the_strike():
    rid = entry_id("Una regla.")
    plain = f"### {rid} · 2026-10-28\nUna regla.\n"
    struck = f"### {rid} · 2026-10-28 (reemplazada por D-aaaaaaaa)\n~~Una regla.~~\n"
    other = f"### {rid} · 2026-10-28\nUna regla.\nOrigen: otro\n"
    assert problems(plain + "\n" + struck) == []
    assert problems(plain + "\n" + other) == [f"{rid}: repeated with different text"]


def test_every_rozan_names_an_adr_in_force():
    cited = {adr for e in parse_entries(DECISIONS.read_text(encoding="utf-8")) for adr in e.rozan}
    assert cited
    for adr in sorted(cited):
        found = list(ADRS.glob(f"{adr.removeprefix('ADR-')}-*.md"))
        assert len(found) == 1, adr
        assert not knowledge_index.superseded(found[0].read_text(encoding="utf-8")), adr


# The entry that states each number the code enforces, and the constant it must equal.
STATED = {
    "D-d3cd64da": (("150", rules.SPLIT_COST_USD), ("40", rules.SPLIT_FILES)),
    "D-99072874": (("300", rules.PART_WHOLE_FACTOR * rules.SPLIT_COST_USD),),
    "D-5c088d42": (("300", rules.PART_WHOLE_FACTOR * rules.SPLIT_COST_USD),),
}
STATED_BY_TEXT = (
    ("Los pull requests de lecciones del playbook", "240", playbook.LESSON_MAX_CHARS),
)


@pytest.mark.parametrize("rule_id", sorted(STATED))
def test_the_numbers_an_entry_states_are_the_ones_the_code_enforces(rule_id):
    entry = next(e for e in parse_entries(DECISIONS.read_text(encoding="utf-8")) if e.id == rule_id)
    for number, constant in STATED[rule_id]:
        assert number in entry.text
        assert int(number) == constant


@pytest.mark.parametrize(("start", "number", "constant"), STATED_BY_TEXT)
def test_the_lesson_cap_the_entry_states_is_the_playbooks(start, number, constant):
    entry = next(
        e for e in parse_entries(DECISIONS.read_text(encoding="utf-8")) if e.text.startswith(start)
    )
    assert f"{number} caracteres" in entry.text
    assert int(number) == constant


# --- .gitattributes and real merges ---


def test_gitattributes_lists_exactly_the_owner_decision_paths_with_merge_union():
    assert attributes_problems((REPO / ".gitattributes").read_text(encoding="utf-8")) == []


def test_a_gitattributes_missing_a_path_or_listing_another_is_reported():
    only_one = f"{knowledge.OWNER_DECISIONS[0]} merge=union\n"
    extra = (REPO / ".gitattributes").read_text(encoding="utf-8") + "docs/x.md merge=union\n"
    wrong = "".join(f"{p} merge=ours\n" for p in knowledge.OWNER_DECISIONS)
    assert attributes_problems(only_one) == [f"{knowledge.OWNER_DECISIONS[1]}: no merge=union"]
    assert attributes_problems(extra) == ["docs/x.md: not an owner file"]
    assert len(attributes_problems(wrong)) == 2


def entry(text: str) -> str:
    return f"\n### {entry_id(text)} · 2026-10-28\n{text}\nOrigen: prueba\n"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }
    return subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=False
    )


def repo_with(tmp_path: Path, attributes: str) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    target = repo / knowledge.OWNER_DECISIONS[1]
    target.parent.mkdir(parents=True, exist_ok=True)
    (repo / ".gitattributes").write_text(attributes, encoding="utf-8")
    target.write_text("# Decisiones del dueño\n" + entry("Regla base."), encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    return repo, target


def branch_edit(repo: Path, target: Path, name: str, edit) -> None:
    git(repo, "checkout", "-q", "-b", name, "main")
    target.write_text(edit(target.read_text(encoding="utf-8")), encoding="utf-8")
    git(repo, "commit", "-q", "-am", name)


def merged(repo: Path, target: Path, edits) -> tuple[subprocess.CompletedProcess[str], str]:
    for name, edit in edits:
        branch_edit(repo, target, name, edit)
    git(repo, "checkout", "-q", edits[0][0])
    done = git(repo, "merge", "--no-edit", edits[1][0])
    return done, target.read_text(encoding="utf-8")


def replace_base(text: str) -> str:
    old, new = entry_id("Regla base."), entry("Regla nueva.")
    struck = text.replace(
        "· 2026-10-28\nRegla base.",
        f"· 2026-10-28 (reemplazada por {entry_id('Regla nueva.')})\n~~Regla base.~~",
        1,
    )
    assert struck != text and old in struck
    return struck + new + f"Reemplaza: {old}\n"


UNION = (REPO / ".gitattributes").read_text(encoding="utf-8")


def test_two_branches_that_append_different_entries_merge_without_conflict(tmp_path):
    repo, target = repo_with(tmp_path, UNION)
    done, text = merged(
        repo,
        target,
        [("a", lambda t: t + entry("Regla A.")), ("b", lambda t: t + entry("Regla B."))],
    )
    assert done.returncode == 0, done.stdout
    assert "Regla A." in text and "Regla B." in text
    assert problems(text.replace("Origen: prueba", "Origen: prueba")) == []


def test_a_replacement_and_an_append_merge_without_conflict(tmp_path):
    repo, target = repo_with(tmp_path, UNION)
    done, text = merged(repo, target, [("a", replace_base), ("b", lambda t: t + entry("Regla B."))])
    assert done.returncode == 0, done.stdout
    assert "~~Regla base.~~" in text and "Regla nueva." in text and "Regla B." in text


def test_without_the_attribute_the_same_appends_conflict(tmp_path):
    repo, target = repo_with(tmp_path, "")
    done, _ = merged(
        repo,
        target,
        [("a", lambda t: t + entry("Regla A.")), ("b", lambda t: t + entry("Regla B."))],
    )
    assert done.returncode != 0


# --- The prompts ---

PM_PROMPTS = (roles.PM, roles.PM_BUG, roles.PM_INTERVIEWEE)
TECH_LEAD_PROMPTS = (
    roles.TECH_LEAD,
    roles.TECH_LEAD_PLATFORM,
    roles.TECH_LEAD_INTERVIEWEE,
    roles.TECH_LEAD_INTERVIEWEE_PLATFORM,
    roles.TECH_LEAD_INTERVIEWEE_STACK,
)
INSTRUCTION = (
    "decides the question it covers: apply it without escalating and cite its `D-<hex>` id"
)
PRECEDENCE = "a rule dated after the ADR wins and the task leaves an `outside` finding"


def rendered(role: roles.Role, tmp_path: Path) -> str:
    raw, source = FilesystemPrompts(tmp_path).raw(role)
    values = {**PLACEHOLDERS, "max_changes": 2, "max_turns": role.max_turns, "flags": "- none"}
    return " ".join(roles.render(raw, source, **values).text.split())


@pytest.mark.parametrize("role", PM_PROMPTS + TECH_LEAD_PROMPTS, ids=lambda r: r.slug)
def test_the_rendered_prompt_tells_the_role_to_apply_a_live_rule_without_escalating(role, tmp_path):
    text = rendered(role, tmp_path)
    assert "decisiones-del-dueño.md" in text
    assert INSTRUCTION in text
    assert PRECEDENCE in text


def test_a_prompt_without_the_instruction_would_be_reported(tmp_path):
    assert INSTRUCTION not in rendered(roles.PJM, tmp_path)


def test_the_seed_in_the_context_reaches_the_roles_whole_and_the_prompts_still_render(tmp_path):
    seed = DECISIONS.read_text(encoding="utf-8")
    built = knowledge_index.build_index(
        {}, {}, {}, None, None, owner={knowledge.OWNER_DECISIONS[1]: seed}
    )
    first = parse_entries(seed)[0]
    assert first.id in built.text and first.text in built.text
    for role in (roles.PM, roles.TECH_LEAD):
        assert rendered(role, tmp_path)


def test_the_real_orchestration_file_is_indexed_and_the_decisions_file_goes_whole():
    built = knowledge_index.build_index(
        {},
        {},
        {},
        None,
        None,
        owner={
            knowledge.OWNER_DECISIONS[1]: DECISIONS.read_text(encoding="utf-8"),
            knowledge.ORCHESTRATION: (REPO / knowledge.ORCHESTRATION).read_text(encoding="utf-8"),
        },
    )
    assert knowledge.ORCHESTRATION in "".join(built.files.values())
    assert knowledge.OWNER_DECISIONS[1] in built.text
    assert parse_entries(DECISIONS.read_text(encoding="utf-8"))[0].id in built.text
