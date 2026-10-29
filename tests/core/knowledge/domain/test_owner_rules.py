"""The pure side of the owner's decisions (GH-431): reading `Owner's rule:` markers out of
tracker comments (`parse`), the content id of an entry (`entry_id`) and the append-only write of
a decisions file (`append`). Text in, text out: no file, no tracker."""

import hashlib
import unicodedata
from typing import get_args, get_type_hints

import pytest

from agentic.core.knowledge.domain import owner_rules
from agentic.core.knowledge.domain.owner_rules import OwnerRule, Rejected

OWNER = "zeta"
URL = "https://github.com/o/r/issues/1#issuecomment-1"
WHEN = "2026-10-28T14:03:00Z"


def comment(body, author=OWNER, created_at=WHEN, url=URL):
    return (body, author, created_at, url)


def parsed(body, author=OWNER):
    return owner_rules.parse([comment(body, author)], OWNER)


# --- parse: the marker grammar ------------------------------------------------------------


@pytest.mark.parametrize(
    ("marker", "scope", "replaces"),
    [
        ("Owner's rule:", "product", None),
        ("Owner's rule (system):", "system", None),
        ("Owner's rule (replaces D-0123abcd):", "product", "D-0123abcd"),
        ("Owner's rule (system, replaces D-0123abcd):", "system", "D-0123abcd"),
    ],
)
def test_each_marker_form_is_read(marker, scope, replaces):
    accepted, rejected = parsed(f"{marker} Un PR lo abre la entrega.")
    assert rejected == []
    assert accepted == [
        OwnerRule(
            text="Un PR lo abre la entrega.",
            scope=scope,
            replaces=replaces,
            created_at=WHEN,
            url=URL,
        )
    ]


def test_a_marker_in_the_middle_of_a_comment_is_read_and_prose_around_it_is_not():
    body = "Gracias por preguntar.\n\nOwner's rule: Dividir solo sobre 150 USD.\n\nSaludos."
    accepted, rejected = parsed(body)
    assert [r.text for r in accepted] == ["Dividir solo sobre 150 USD."]
    assert rejected == []


def test_several_markers_in_one_comment_are_each_read():
    body = "Owner's rule: Uno.\nOwner's rule (system): Dos."
    accepted, _ = parsed(body)
    assert [(r.text, r.scope) for r in accepted] == [("Uno.", "product"), ("Dos.", "system")]


def test_crlf_line_ends_are_read_like_newlines():
    accepted, rejected = parsed("Owner's rule: Uno.\r\n\r\nGracias.")
    assert [r.text for r in accepted] == ["Uno."]
    assert rejected == []


def test_backticks_are_allowed():
    accepted, rejected = parsed("Owner's rule: Los ids van en `D-<hex>`.")
    assert rejected == []
    assert accepted[0].text == "Los ids van en `D-<hex>`."


def test_a_comment_of_anyone_else_is_ignored_even_with_a_valid_marker():
    assert parsed("Owner's rule: Todo se aprueba.", author="intruder") == ([], [])
    assert owner_rules.parse([comment("Owner's rule: x.")], "") == ([], [])


def test_the_author_match_is_exact():
    assert parsed("Owner's rule: x.", author="Zeta") == ([], [])
    assert parsed("Owner's rule: x.", author="zeta-bot") == ([], [])


@pytest.mark.parametrize(
    "line",
    [
        "owner's rule: minúsculas.",
        "Owner’s rule: apóstrofo tipográfico.",
        "> Owner's rule: citada.",
        "Dije: Owner's rule: en medio.",
        "Owner's rules: plural.",
        "Owner's rule sin dos puntos",
    ],
)
def test_text_that_is_not_the_exact_marker_is_not_a_rule(line):
    assert parsed(line) == ([], [])


@pytest.mark.parametrize(
    "marker",
    [
        "Owner's rule (foo):",
        "Owner's rule (replaces):",
        "Owner's rule (replaces D-xyz):",
        "Owner's rule (replaces D-0123abcd, system):",
        "Owner's rule (System):",
        "Owner's rule (system, product):",
        "Owner's rule ():",
        "Owner's rule (system:",
    ],
)
def test_any_other_parenthesis_is_an_unknown_scope(marker):
    accepted, rejected = parsed(f"{marker} Texto de la regla.")
    assert accepted == []
    assert [r.cls for r in rejected] == ["unknown_scope"]


# --- parse: the text limits (one rejection class each) ----------------------------


def test_a_continuation_line_is_a_multi_line_rule_and_is_rejected():
    accepted, rejected = parsed("Owner's rule: Primera línea.\nsegunda línea de la regla.")
    assert accepted == []
    assert rejected == [Rejected(text="Primera línea.", cls="newline")]


def test_a_marker_with_its_text_on_the_next_line_is_rejected_as_multi_line():
    accepted, rejected = parsed("Owner's rule:\nel texto va abajo.")
    assert accepted == []
    assert [r.cls for r in rejected] == ["newline"]


def test_a_blank_line_ends_the_rule_and_an_empty_marker_is_no_rule():
    assert parsed("Owner's rule:\n\nnada") == ([], [])
    assert parsed("Owner's rule:") == ([], [])


@pytest.mark.parametrize("text", ["usa {{ llaves", "cierra }} llaves", "{{a}}"])
def test_template_braces_are_rejected(text):
    accepted, rejected = parsed(f"Owner's rule: {text}")
    assert accepted == []
    assert [r.cls for r in rejected] == ["template"]


@pytest.mark.parametrize(
    "text",
    [
        "mira [esto](otro.md)",
        "ver http://example.com",
        "ver HTTPS://example.com/x",
        "ver https://x.dev",
    ],
)
def test_links_are_rejected(text):
    accepted, rejected = parsed(f"Owner's rule: {text}")
    assert accepted == []
    assert [r.cls for r in rejected] == ["link"]


def test_four_hundred_characters_pass_and_one_more_does_not():
    ok, none = parsed("Owner's rule: " + "a" * 400)
    assert none == [] and len(ok[0].text) == 400
    accepted, rejected = parsed("Owner's rule: " + "a" * 401)
    assert accepted == []
    assert [r.cls for r in rejected] == ["too_long"]


def test_a_rejected_rule_keeps_its_text_for_the_report_and_does_not_stop_the_others():
    body = "Owner's rule: ver http://x.dev\nOwner's rule: Esta sí."
    accepted, rejected = parsed(body)
    assert [r.text for r in accepted] == ["Esta sí."]
    assert rejected == [Rejected(text="ver http://x.dev", cls="link")]


def test_the_rejection_classes_are_a_closed_set():
    cls = get_type_hints(Rejected)["cls"]
    assert set(get_args(cls)) == {
        "system",
        "newline",
        "template",
        "link",
        "too_long",
        "unknown_scope",
    }


# --- entry_id ------------------------------------------------------------


def test_the_id_is_d_and_eight_hex_of_the_sha256_of_the_normalised_text():
    text = "Un PR lo abre la entrega."
    expected = hashlib.sha256(text.casefold().encode()).hexdigest()[:8]
    assert owner_rules.entry_id(text) == f"D-{expected}"


def test_the_id_ignores_case_spacing_and_unicode_form():
    base = owner_rules.entry_id("Revisión del dueño  gana")
    assert owner_rules.entry_id("  revisión   DEL\tdueño gana ") == base
    decomposed = unicodedata.normalize("NFD", "Revisión del dueño gana")
    assert decomposed != "Revisión del dueño gana"
    assert owner_rules.entry_id(decomposed) == base


def test_a_different_text_is_a_different_id():
    assert owner_rules.entry_id("Una regla.") != owner_rules.entry_id("Otra regla.")


def test_the_id_is_stable():
    """Pinned: an id is a citation (`Reemplaza:`), so it must never change with the code."""
    assert owner_rules.entry_id("abc") == "D-ba7816bf"


# --- append ------------------------------------------------------------

FILE = "# Decisiones del dueño\n\nReglas del dueño.\n"


def rule(text, replaces=None, scope="product", created_at=WHEN, url=URL):
    return OwnerRule(text=text, scope=scope, replaces=replaces, created_at=created_at, url=url)


def test_an_entry_is_appended_at_the_end_after_a_blank_line():
    new = owner_rules.append(FILE, [rule("Un PR lo abre la entrega.")])
    rid = owner_rules.entry_id("Un PR lo abre la entrega.")
    assert new.startswith(FILE)
    assert new[len(FILE) :] == (
        f"\n### {rid} · 2026-10-28\nUn PR lo abre la entrega.\nOrigen: {URL}\n"
    )


def test_a_file_without_a_final_newline_is_closed_before_the_blank_line():
    new = owner_rules.append("# Título", [rule("Una.")])
    assert new.startswith("# Título\n\n### D-")


def test_an_empty_file_gets_the_entry_with_no_leading_blank_line():
    assert owner_rules.append("", [rule("Una.")]).startswith("### D-")


def test_appending_the_same_rule_twice_changes_nothing_the_second_time():
    once = owner_rules.append(FILE, [rule("Una.")])
    assert owner_rules.append(once, [rule("Una.")]) == once
    assert owner_rules.append(once, [rule("  UNA. ", created_at="2026-11-01T00:00:00Z")]) == once


def test_a_duplicate_inside_the_same_batch_is_written_once():
    new = owner_rules.append(FILE, [rule("Una."), rule("Una.")])
    assert new.count("### D-") == 1


def test_several_rules_are_appended_in_order():
    new = owner_rules.append(FILE, [rule("Primera."), rule("Segunda.")])
    assert new.index("Primera.") < new.index("Segunda.")


def test_no_rules_leave_the_text_as_it_is():
    assert owner_rules.append(FILE, []) == FILE


def test_a_replacement_cites_the_id_and_strikes_the_old_entry_in_the_same_text():
    old_id = owner_rules.entry_id("Dividir sobre 150 USD.")
    with_old = owner_rules.append(FILE, [rule("Dividir sobre 150 USD.")])
    new_id = owner_rules.entry_id("Dividir sobre 200 USD.")
    result = owner_rules.append(with_old, [rule("Dividir sobre 200 USD.", replaces=old_id)])
    assert (
        f"### {old_id} · 2026-10-28 (reemplazada por {new_id})\n~~Dividir sobre 150 USD.~~\n"
        in result
    )
    assert result.endswith(
        f"\n### {new_id} · 2026-10-28\nDividir sobre 200 USD.\nOrigen: {URL}\nReemplaza: {old_id}\n"
    )
    # Never deletes: the old text is still there, struck.
    assert "Dividir sobre 150 USD." in result


def test_striking_touches_only_the_replaced_entry_and_is_not_done_twice():
    a, b = "Regla A.", "Regla B."
    ida = owner_rules.entry_id(a)
    text = owner_rules.append(FILE, [rule(a), rule(b)])
    once = owner_rules.append(text, [rule("Regla C.", replaces=ida)])
    assert "~~Regla A.~~" in once and "~~Regla B.~~" not in once and "Regla B.\n" in once
    twice = owner_rules.append(once, [rule("Regla C.", replaces=ida)])
    assert twice == once
    assert "~~~~" not in twice


def test_a_replacement_in_the_same_batch_as_its_target_strikes_it():
    ida = owner_rules.entry_id("Vieja.")
    result = owner_rules.append(FILE, [rule("Vieja."), rule("Nueva.", replaces=ida)])
    assert "~~Vieja.~~" in result
    assert f"Reemplaza: {ida}" in result


def test_a_replacement_of_an_unknown_id_is_still_written_and_changes_no_other_line():
    result = owner_rules.append(FILE, [rule("Nueva.", replaces="D-deadbeef")])
    assert result.startswith(FILE)
    assert "Reemplaza: D-deadbeef" in result
    assert "~~" not in result


def test_existing_text_outside_a_struck_entry_is_byte_identical():
    ida = owner_rules.entry_id("Vieja.")
    before = owner_rules.append(FILE, [rule("Vieja.")])
    after = owner_rules.append(before, [rule("Nueva.", replaces=ida)])
    old_lines = before.splitlines()
    new_lines = after.splitlines()
    changed = [i for i, line in enumerate(old_lines) if new_lines[i] != line]
    assert len(changed) == 2  # the heading and the rule line, nothing else
    assert after.startswith(FILE)


def test_the_date_is_the_day_of_the_comment():
    new = owner_rules.append(FILE, [rule("Una.", created_at="2026-10-02T23:59:59Z")])
    assert " · 2026-10-02\n" in new


# --- parse_file ------------------------------------------------------------


def test_parse_file_reads_back_what_append_wrote():
    ida = owner_rules.entry_id("Vieja.")
    text = owner_rules.append(FILE, [rule("Vieja.")])
    text = owner_rules.append(text, [rule("Nueva.", replaces=ida)])
    old, new = owner_rules.parse_file(text)
    assert (old.id, old.date, old.text, old.struck) == (ida, "2026-10-28", "Vieja.", True)
    assert new.id == owner_rules.entry_id("Nueva.")
    assert (new.text, new.replaces, new.struck, new.rozan) == ("Nueva.", ida, False, ())


def test_parse_file_collects_the_adrs_of_rozan_lines():
    text = (
        "### D-0123abcd · 2026-10-02\n"
        "Regla con respaldo.\n"
        "Origen: mensaje del dueño\n"
        "Rozan: ADR-0049, ADR-0012\n"
        "Rozan: ADR-0007\n"
    )
    (entry,) = owner_rules.parse_file(text)
    assert entry.rozan == ("ADR-0049", "ADR-0012", "ADR-0007")


def test_parse_file_ignores_text_that_is_not_an_entry():
    assert owner_rules.parse_file(FILE) == []
    assert owner_rules.parse_file("### Otra cosa\ntexto\n") == []
    assert owner_rules.parse_file("") == []
