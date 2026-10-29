"""`owner_rules.live`: the entries of a decisions file a role is given whole."""

from agentic.core.knowledge.domain import owner_rules

HEADER = "# Decisiones del dueño\n\nReglas vigentes.\n\n"
A = "- D-aaaaaaaa: Un PR lo abre la entrega. (2026-10-02, issue #1)"
B = "- D-bbbbbbbb: Una parte se construye entera. (2026-10-03, issue #2) Reemplaza: D-aaaaaaaa"
STRUCK_A = "- ~~D-aaaaaaaa: Un PR lo abre la entrega. (2026-10-02, issue #1)~~"
C = "- D-cccccccc: Fase 3. (2026-10-04, mensaje)"


def test_an_entry_cited_by_a_reemplaza_line_is_dropped_and_the_replacement_stays():
    text = HEADER + A + "\n" + B + "\n"
    kept = owner_rules.live(text)
    assert "D-aaaaaaaa: Un PR" not in kept
    assert "D-bbbbbbbb" in kept
    assert kept.startswith(HEADER)


def test_a_struck_entry_is_dropped_even_when_nothing_cites_it():
    kept = owner_rules.live(HEADER + STRUCK_A + "\n" + C + "\n")
    assert "D-aaaaaaaa" not in kept
    assert "D-cccccccc" in kept


def test_duplicate_ids_collapse_to_the_first():
    kept = owner_rules.live(HEADER + C + "\n" + C + "\n")
    assert kept.count("D-cccccccc") == 1


def test_a_file_without_entries_is_returned_unchanged():
    assert owner_rules.live(HEADER) == HEADER


def test_an_entry_continued_on_indented_lines_is_dropped_whole():
    text = HEADER + A + "\n  segunda línea del mismo texto\n" + B + "\n"
    kept = owner_rules.live(text)
    assert "segunda línea" not in kept and "D-bbbbbbbb" in kept


def test_a_citation_inside_a_struck_entry_still_replaces_what_it_cites():
    struck_b = "- ~~D-bbbbbbbb: x Reemplaza: D-aaaaaaaa~~"
    kept = owner_rules.live(HEADER + A + "\n" + struck_b + "\n")
    assert "D-aaaaaaaa" not in kept and "D-bbbbbbbb" not in kept
