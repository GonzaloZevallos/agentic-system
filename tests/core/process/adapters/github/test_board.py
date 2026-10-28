"""GH-375, ADR-0071: the GraphQL adapter over an injected `gh` run. The responses are shaped
like what `gh api graphql` prints for the Project API."""

import json
import re
import subprocess
from datetime import date
from pathlib import Path

import pytest

from agentic.core.process.adapters.github import board as board_module
from agentic.core.process.adapters.github.board import GhBoard
from agentic.core.process.domain.board import FieldWrite

ETAPAS = [
    "Corriendo",
    "En PR",
    "Trabada",
    "Necesita humano",
    "En cola",
    "Fuera del sistema",
    "Hecha",
]
ISSUE_FIELDS = "number,id,labels,state,closedAt"


def identity(skip: str | None = None, options: list[str] | None = None) -> dict:
    fields = [
        {"id": "F_bloque", "name": "Bloque", "dataType": "SINGLE_SELECT", "options": []},
        {
            "id": "F_etapa",
            "name": "Etapa",
            "dataType": "SINGLE_SELECT",
            "options": [{"id": f"O_{i}", "name": n} for i, n in enumerate(options or ETAPAS)],
        },
        {"id": "F_bloqueo", "name": "Bloqueo", "dataType": "TEXT"},
        {"id": "F_costo", "name": "Costo USD", "dataType": "NUMBER"},
        {"id": "F_inicio", "name": "Inicio", "dataType": "DATE"},
        {"id": "F_fin", "name": "Fin", "dataType": "DATE"},
        {},
    ]
    kept = [f for f in fields if f.get("name") != skip]
    project = {"id": "PVT_1", "fields": {"nodes": kept}}
    return {"data": {"repositoryOwner": {"projectV2": project}}}


def assert_variables_are_all_used(query: str) -> None:
    """GraphQL validation (spec 5.8.4): a declared variable the query never uses is an error."""
    head, _, rest = query.partition(") {")
    declared = set(re.findall(r"\$(\w+):", head))
    used = set(re.findall(r"\$(\w+)", rest))
    assert declared == used, f"declared and used variables differ: {declared ^ used}"


def test_the_variable_check_rejects_a_declared_variable_the_query_never_uses():
    with pytest.raises(AssertionError):
        assert_variables_are_all_used("mutation($a: ID!, $b: ID!) { x: f(id: $a) { id } }")
    assert_variables_are_all_used("mutation($a: ID!) { x: f(id: $a) { id } }")


class FakeGh:
    """Answers `gh` calls: records the GraphQL bodies it was given (read from the `--input`
    file while it still exists) and answers from `responses`, one per graphql call."""

    def __init__(self, responses=(), listing="[]", returncode=0):
        self.responses = list(responses)
        self.listing = listing
        self.returncode = returncode
        self.calls: list[list[str]] = []
        self.bodies: list[dict] = []

    def __call__(self, args, cwd, capture_output, text, check):
        self.calls.append(list(args))
        if args[1:3] == ["api", "graphql"]:
            body = json.loads(Path(args[args.index("--input") + 1]).read_text())
            self.bodies.append(body)
            assert_variables_are_all_used(body["query"])
            out = self.responses.pop(0)
            out = out if isinstance(out, str) else json.dumps(out)
            return subprocess.CompletedProcess(args, self.returncode, out, "")
        return subprocess.CompletedProcess(args, 0, self.listing, "")


def board(gh) -> GhBoard:
    return GhBoard(Path("/p"), "GonzaloZevallos", 3, gh, log=lambda _line: None)


def added(*numbers: int) -> dict:
    return {"data": {f"i{n}_add": {"item": {"id": f"PVTI_{n}"}} for n in numbers}}


def issue_node(number: int, **extra) -> dict:
    node = {
        "number": number,
        "id": f"I_{number}",
        "state": "OPEN",
        "labels": [{"name": "agentic"}],
        "closedAt": "",
    }
    return {**node, **extra}


def prime(gh: FakeGh, b: GhBoard, *numbers: int) -> None:
    gh.listing = json.dumps([issue_node(n) for n in numbers])
    b.open_issues()


WRITES = [
    FieldWrite(7, "etapa", "Trabada"),
    FieldWrite(7, "bloqueo", "#3"),
    FieldWrite(7, "costo", 1.5),
    FieldWrite(7, "inicio", date(2026, 10, 20)),
    FieldWrite(7, "fin", None),
]


def test_the_ids_are_looked_up_by_name_once_per_process():
    gh = FakeGh([identity()])
    b = board(gh)
    assert b.project_id() == "PVT_1" and b.project_id() == "PVT_1"
    assert len(gh.bodies) == 1
    assert gh.bodies[0]["variables"] == {"owner": "GonzaloZevallos", "number": 3}


@pytest.mark.parametrize("name", ["Etapa", "Bloqueo", "Costo USD", "Inicio", "Fin"])
def test_a_missing_field_is_a_value_error_naming_it(name):
    with pytest.raises(ValueError, match=name):
        board(FakeGh([identity(skip=name)])).project_id()


def test_a_missing_etapa_option_is_a_value_error_naming_it():
    with pytest.raises(ValueError, match="Hecha"):
        board(FakeGh([identity(options=ETAPAS[:-1])])).project_id()


def test_a_project_that_is_not_there_is_a_value_error():
    with pytest.raises(ValueError, match="not found"):
        board(FakeGh([{"data": {"repositoryOwner": None}}])).project_id()


def test_a_batch_is_two_requests_with_every_value_a_typed_variable():
    gh = FakeGh([identity(), added(7), {"data": {}}])
    b = board(gh)
    prime(gh, b, 7)
    assert b.write(WRITES) == frozenset({7})
    _, adding, fields = gh.bodies
    assert "i7_add: addProjectV2ItemById" in adding["query"]
    assert adding["variables"] == {"project": "PVT_1", "i7_item": "I_7"}
    query, variables = fields["query"], fields["variables"]
    assert "$i7_bloqueo: String!" in query and variables["i7_bloqueo"] == "#3"
    assert "$i7_costo: Float!" in query and variables["i7_costo"] == 1.5
    assert "$i7_inicio: Date!" in query and variables["i7_inicio"] == "2026-10-20"
    assert "$i7_etapa: ID!" in query and variables["i7_etapa"] == "O_2"
    assert "i7_fin: clearProjectV2ItemFieldValue" in query and "i7_fin" not in variables
    assert variables["i7_item"] == "PVTI_7" and variables["f_etapa"] == "F_etapa"
    assert not any(k in variables for k in ("f_bloque", "f_hito"))


def test_a_batch_declares_only_the_field_ids_its_writes_use():
    gh = FakeGh([identity(), added(7), {"data": {}}])
    b = board(gh)
    prime(gh, b, 7)
    assert b.write([FieldWrite(7, "etapa", "En cola")]) == frozenset({7})
    fields = gh.bodies[-1]
    assert "$f_etapa: ID!" in fields["query"] and "$f_bloqueo" not in fields["query"]
    assert set(fields["variables"]) == {"project", "f_etapa", "i7_item", "i7_etapa"}


def test_only_aliases_of_the_fixed_form_are_in_the_query_text():
    gh = FakeGh([identity(), added(7), {"data": {}}])
    b = board(gh)
    prime(gh, b, 7)
    b.write(WRITES)
    for body in gh.bodies[1:]:
        text = re.sub(r"\$?\bi\d+_[a-z]+\b", "", body["query"])
        for value in ("Trabada", "#3", "1.5", "2026-10-20", "PVTI_7", "I_7", "PVT_1"):
            assert value not in text


def test_a_reason_that_looks_like_graphql_arrives_intact_as_a_variable():
    reason = '"} mutation { x'
    gh = FakeGh([identity(), added(7), {"data": {}}])
    b = board(gh)
    prime(gh, b, 7)
    b.write([FieldWrite(7, "etapa", "Trabada"), FieldWrite(7, "bloqueo", reason)])
    assert gh.bodies[2]["variables"]["i7_bloqueo"] == reason
    assert "mutation { x" not in gh.bodies[2]["query"]


def test_an_error_on_one_alias_mid_batch_excludes_exactly_that_issue():
    fail = {"errors": [{"message": "boom", "path": ["i8_bloqueo"]}], "data": {"i8_bloqueo": None}}
    gh = FakeGh([identity(), added(7, 8, 9), fail])
    b = board(gh)
    prime(gh, b, 7, 8, 9)
    writes = [FieldWrite(n, "etapa", "En cola") for n in (7, 8, 9)]
    writes.append(FieldWrite(8, "bloqueo", "x"))
    assert b.write(writes) == frozenset({7, 9})


def test_an_error_on_an_add_keeps_that_issue_out_of_the_field_request():
    data = {"i8_add": None, **added(7)["data"]}
    err = {"errors": [{"message": "nope", "path": ["i8_add"]}], "data": data}
    gh = FakeGh([identity(), err, {"data": {}}])
    b = board(gh)
    prime(gh, b, 7, 8)
    writes = [FieldWrite(n, "etapa", "En cola") for n in (7, 8)]
    assert b.write(writes) == frozenset({7})
    assert "i8_etapa" not in gh.bodies[2]["query"]


def test_the_body_gh_prints_with_exit_one_still_attributes_the_error():
    fail = {"errors": [{"message": "boom", "path": ["i8_etapa"]}], "data": {}}
    gh = FakeGh([identity(), added(7, 8), fail])
    b = board(gh)
    prime(gh, b, 7, 8)

    def exit_one(args, cwd, capture_output, text, check):
        result = gh(args, cwd, capture_output, text, check)
        if len(gh.bodies) == 3:
            return subprocess.CompletedProcess(args, 1, result.stdout, "GraphQL error")
        return result

    b.run = exit_one
    writes = [FieldWrite(n, "etapa", "En cola") for n in (7, 8)]
    assert b.write(writes) == frozenset({7})


@pytest.mark.parametrize(
    "response",
    ["<html>502 Bad Gateway</html>", "[]", {"errors": [{"message": "rate limited"}]}],
)
def test_an_unparseable_or_unattributable_response_writes_nothing(response):
    gh = FakeGh([identity(), response])
    b = board(gh)
    prime(gh, b, 7)
    assert b.write(WRITES) == frozenset()
    assert len(gh.bodies) == 2


def test_a_transport_failure_ends_the_write_with_nothing_recorded():
    class Timeout(FakeGh):
        def __call__(self, args, cwd, capture_output, text, check):
            if args[1:3] == ["api", "graphql"] and len(self.bodies) == 1:
                raise subprocess.TimeoutExpired(args, 30)
            return super().__call__(args, cwd, capture_output, text, check)

    gh = Timeout([identity(), added(7)])
    b = board(gh)
    prime(gh, b, 7)
    assert b.write(WRITES) == frozenset()


def test_issues_go_in_batches_of_25_two_requests_each():
    numbers = list(range(1, 31))
    first, rest = numbers[:25], numbers[25:]
    gh = FakeGh([identity(), added(*first), {"data": {}}, added(*rest), {"data": {}}])
    b = board(gh)
    prime(gh, b, *numbers)
    got = b.write([FieldWrite(n, "etapa", "En cola") for n in numbers])
    assert got == frozenset(numbers) and len(gh.bodies) == 5


def test_an_issue_not_read_this_process_has_its_node_looked_up():
    gh = FakeGh([identity(), added(7), {"data": {}}])
    b = board(gh)
    gh.listing = json.dumps(issue_node(7))
    assert b.write([FieldWrite(7, "etapa", "En cola")]) == frozenset({7})
    assert ["gh", "issue", "view", "7", "--json", ISSUE_FIELDS] in gh.calls


def test_issue_reads_use_the_state_flag_and_parse_labels_and_the_close_day():
    closed = issue_node(4, state="CLOSED", closedAt="2026-10-20T23:30:00Z")
    gh = FakeGh(listing=json.dumps([closed]))
    rows, limited = board(gh).all_issues()
    assert gh.calls[0][:7] == ["gh", "issue", "list", "--state", "all", "--limit", "1000"]
    assert (rows[0].number, rows[0].state, rows[0].labels) == (4, "closed", frozenset({"agentic"}))
    assert rows[0].closed_at == date(2026, 10, 20) and limited is False


def test_all_issues_reports_the_limit_when_the_list_is_full():
    full = json.dumps([issue_node(n) for n in range(1000)])
    assert board(FakeGh(listing=full)).all_issues()[1] is True


def test_open_issues_lists_only_open_ones():
    gh = FakeGh(listing=json.dumps([issue_node(1)]))
    assert [r.number for r in board(gh).open_issues()] == [1]
    assert gh.calls[0][3:5] == ["--state", "open"]


def test_the_default_run_is_bounded_to_thirty_seconds(monkeypatch):
    seen = {}

    def bounded(args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "[]", "")

    monkeypatch.setattr(board_module, "bounded_run", bounded)
    GhBoard(Path("/p"), "o", 1).open_issues()
    assert seen["timeout_s"] == 30
