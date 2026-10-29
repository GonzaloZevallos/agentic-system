"""GH-375, ADR-0073: the GraphQL adapter over an injected `gh` run. The responses are shaped
like what `gh api graphql` prints for the Project API."""

import json
import re
import subprocess
from datetime import date
from pathlib import Path

import pytest

from agentic.core.process.adapters.github import board as board_module
from agentic.core.process.adapters.github.board import GhBoard
from agentic.core.process.adapters.github.run import GhRetry
from agentic.core.process.application.ports import (
    BoardWritten,
    TrackerRateLimited,
    TrackerRefused,
    TrackerTimedOut,
    TrackerTransient,
    TrackerUnreachable,
)
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


def board(gh, log=lambda _line: None) -> GhBoard:
    return GhBoard(Path("/p"), "GonzaloZevallos", 3, gh, log=log)


def added(*numbers: int) -> dict:
    return {"data": {f"i{n}_add": {"item": {"id": f"PVTI_{n}"}} for n in numbers}}


def wrote(writes) -> dict:
    """What GitHub answers to a field request that succeeded: each written alias maps to the
    mutation's payload."""
    return {"data": {f"i{w.issue}_{w.field}": {"clientMutationId": None} for w in writes}}


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


def select(field_id: str, name: str, options: list[str], kind: str = "SINGLE_SELECT") -> dict:
    node: dict = {"id": field_id, "name": name, "dataType": kind}
    if kind == "SINGLE_SELECT":
        node["options"] = [{"id": f"{field_id}_{i}", "name": n} for i, n in enumerate(options)]
    return node


OTHERS = [
    {"id": "F_bloqueo", "name": "Bloqueo", "dataType": "TEXT"},
    {"id": "F_costo", "name": "Costo USD", "dataType": "NUMBER"},
    {"id": "F_inicio", "name": "Inicio", "dataType": "DATE"},
    {"id": "F_fin", "name": "Fin", "dataType": "DATE"},
]


def project_with(*fields: dict) -> dict:
    project = {"id": "PVT_1", "fields": {"nodes": [*fields, *OTHERS]}}
    return {"data": {"repositoryOwner": {"projectV2": project}}}


def resolve(*fields: dict):
    return board_module._identity_of(project_with(*fields), "o/3")


def test_a_status_field_with_the_seven_options_and_no_etapa_is_the_etapa_field():
    ids = resolve(select("F_status", "Status", ETAPAS))
    assert ids.fields["etapa"] == "F_status"
    assert ids.options == {n: f"F_status_{i}" for i, n in enumerate(ETAPAS)}


def test_status_with_extra_options_is_still_accepted():
    assert resolve(select("F_status", "Status", [*ETAPAS, "Otra"])).fields["etapa"] == "F_status"


def test_a_complete_etapa_wins_over_a_status_that_is_also_there():
    ids = resolve(select("F_status", "Status", ETAPAS), select("F_etapa", "Etapa", ETAPAS))
    assert ids.fields["etapa"] == "F_etapa"


def test_an_incomplete_etapa_does_not_fall_back_to_a_complete_status():
    with pytest.raises(ValueError, match="has no Etapa option 'Hecha'") as raised:
        resolve(select("F_etapa", "Etapa", ETAPAS[:-1]), select("F_status", "Status", ETAPAS))
    assert "Status" not in str(raised.value)


def test_an_etapa_of_another_type_counts_as_absent():
    text = select("F_etapa", "Etapa", [], kind="TEXT")
    ids = resolve(text, select("F_status", "Status", ETAPAS))
    assert ids.fields["etapa"] == "F_status"


def test_a_status_missing_an_option_names_it_and_the_field():
    with pytest.raises(ValueError) as raised:
        resolve(select("F_status", "Status", ETAPAS[:-1]))
    assert (
        str(raised.value) == "the board project o/3 has no Etapa option 'Hecha' on its Status field"
    )


def test_neither_etapa_nor_status_is_one_message_naming_both():
    text_status = select("F_status", "Status", [], kind="TEXT")
    for fields in ([], [text_status]):
        with pytest.raises(ValueError) as raised:
            resolve(*fields)
        assert str(raised.value) == (
            "the board project o/3 has no SINGLE_SELECT field 'Etapa' "
            "nor a SINGLE_SELECT field 'Status'"
        )


def project_three() -> dict:
    """The shape of GonzaloZevallos/3 as the issue (GH-421) and ADR-0073 describe it, not a
    live API read: the built-in Status field carries the seven Etapa options."""
    return project_with(
        {"id": "PVTF_title", "name": "Title", "dataType": "TITLE"},
        {"id": "PVTF_assignees", "name": "Assignees", "dataType": "ASSIGNEES"},
        select("PVTSSF_lAHOAuV6bM4Bo5rQzhnXo0o", "Status", ETAPAS),
        select("PVTSSF_bloque", "Bloque", ["A", "B"]),
        select("PVTSSF_hito", "Hito", ["H1"]),
    )


def test_project_three_resolves_all_five_fields_and_seven_options():
    ids = board_module._identity_of(project_three(), "GonzaloZevallos/3")
    status = "PVTSSF_lAHOAuV6bM4Bo5rQzhnXo0o"
    assert ids.fields == {
        "etapa": status,
        "bloqueo": "F_bloqueo",
        "costo": "F_costo",
        "inicio": "F_inicio",
        "fin": "F_fin",
    }
    assert ids.options == {n: f"{status}_{i}" for i, n in enumerate(ETAPAS)}


def test_an_etapa_write_on_project_three_goes_to_the_status_field_with_the_option_id():
    ids = board_module._identity_of(project_three(), "GonzaloZevallos/3")
    _, variables = board_module._fields(ids, {7: [FieldWrite(7, "etapa", "En PR")]}, {7: "PVTI_7"})
    status = "PVTSSF_lAHOAuV6bM4Bo5rQzhnXo0o"
    assert variables["f_etapa"] == status
    assert variables["i7_etapa"] == f"{status}_1"


def test_a_project_that_is_not_there_is_a_value_error():
    with pytest.raises(ValueError, match="not found"):
        board(FakeGh([{"data": {"repositoryOwner": None}}])).project_id()


def test_a_batch_is_two_requests_with_every_value_a_typed_variable():
    gh = FakeGh([identity(), added(7), wrote(WRITES)])
    b = board(gh)
    prime(gh, b, 7)
    assert b.write(WRITES).written == frozenset({7})
    _, adding, fields = gh.bodies
    assert "i7_add: addProjectV2ItemById" in adding["query"]
    assert adding["variables"] == {"project": "PVT_1", "i7_item": "I_7"}
    query, variables = fields["query"], fields["variables"]
    assert "$i7_bloqueo: String!" in query and variables["i7_bloqueo"] == "#3"
    assert "$i7_costo: Float!" in query and variables["i7_costo"] == 1.5
    assert "$i7_inicio: Date!" in query and variables["i7_inicio"] == "2026-10-20"
    assert "$i7_etapa: String!" in query and variables["i7_etapa"] == "O_2"
    assert "i7_fin: clearProjectV2ItemFieldValue" in query and "i7_fin" not in variables
    assert variables["i7_item"] == "PVTI_7" and variables["f_etapa"] == "F_etapa"
    assert not any(k in variables for k in ("f_bloque", "f_hito"))


def test_a_batch_declares_only_the_field_ids_its_writes_use():
    gh = FakeGh([identity(), added(7), wrote([FieldWrite(7, "etapa", "En cola")])])
    b = board(gh)
    prime(gh, b, 7)
    assert b.write([FieldWrite(7, "etapa", "En cola")]).written == frozenset({7})
    fields = gh.bodies[-1]
    assert "$f_etapa: ID!" in fields["query"] and "$f_bloqueo" not in fields["query"]
    assert set(fields["variables"]) == {"project", "f_etapa", "i7_item", "i7_etapa"}


def test_only_aliases_of_the_fixed_form_are_in_the_query_text():
    gh = FakeGh([identity(), added(7), wrote(WRITES)])
    b = board(gh)
    prime(gh, b, 7)
    b.write(WRITES)
    for body in gh.bodies[1:]:
        text = re.sub(r"\$?\bi\d+_[a-z]+\b", "", body["query"])
        for value in ("Trabada", "#3", "1.5", "2026-10-20", "PVTI_7", "I_7", "PVT_1"):
            assert value not in text


def test_a_reason_that_looks_like_graphql_arrives_intact_as_a_variable():
    reason = '"} mutation { x'
    writes = [FieldWrite(7, "etapa", "Trabada"), FieldWrite(7, "bloqueo", reason)]
    gh = FakeGh([identity(), added(7), wrote(writes)])
    b = board(gh)
    prime(gh, b, 7)
    b.write(writes)
    assert gh.bodies[2]["variables"]["i7_bloqueo"] == reason
    assert "mutation { x" not in gh.bodies[2]["query"]


def test_an_error_on_one_alias_mid_batch_excludes_exactly_that_issue():
    ok = wrote([FieldWrite(n, "etapa", "En cola") for n in (7, 9)])["data"]
    fail = {
        "errors": [{"message": "boom", "path": ["i8_bloqueo"]}],
        "data": {**ok, "i8_bloqueo": None},
    }
    gh = FakeGh([identity(), added(7, 8, 9), fail])
    b = board(gh)
    prime(gh, b, 7, 8, 9)
    writes = [FieldWrite(n, "etapa", "En cola") for n in (7, 8, 9)]
    writes.append(FieldWrite(8, "bloqueo", "x"))
    assert b.write(writes).written == frozenset({7, 9})


def test_an_error_on_an_add_keeps_that_issue_out_of_the_field_request():
    data = {"i8_add": None, **added(7)["data"]}
    err = {"errors": [{"message": "nope", "path": ["i8_add"]}], "data": data}
    gh = FakeGh([identity(), err, wrote([FieldWrite(7, "etapa", "En cola")])])
    b = board(gh)
    prime(gh, b, 7, 8)
    writes = [FieldWrite(n, "etapa", "En cola") for n in (7, 8)]
    assert b.write(writes).written == frozenset({7})
    assert "i8_etapa" not in gh.bodies[2]["query"]


def test_the_body_gh_prints_with_exit_one_still_attributes_the_error():
    ok = wrote([FieldWrite(7, "etapa", "En cola")])["data"]
    fail = {"errors": [{"message": "boom", "path": ["i8_etapa"]}], "data": ok}
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
    assert b.write(writes).written == frozenset({7})


@pytest.mark.parametrize(
    "response",
    ["<html>502 Bad Gateway</html>", "[]", {"errors": [{"message": "rate limited"}]}],
)
def test_an_unparseable_or_unattributable_response_writes_nothing(response):
    gh = FakeGh([identity(), response])
    b = board(gh)
    prime(gh, b, 7)
    assert b.write(WRITES).written == frozenset()
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
    assert b.write(WRITES).written == frozenset()


def test_thirty_issues_with_one_write_each_go_in_one_add_and_two_field_requests():
    numbers = list(range(1, 31))
    first, rest = numbers[:20], numbers[20:]
    writes = [FieldWrite(n, "etapa", "En cola") for n in numbers]
    gh = FakeGh(
        [
            identity(),
            added(*first),
            wrote([w for w in writes if w.issue in first]),
            added(*rest),
            wrote([w for w in writes if w.issue in rest]),
        ]
    )
    b = board(gh)
    prime(gh, b, *numbers)
    got = b.write(writes)
    assert got == BoardWritten(frozenset(numbers), True) and len(gh.bodies) == 5


def test_an_issue_not_read_this_process_has_its_node_looked_up():
    gh = FakeGh([identity(), added(7), wrote([FieldWrite(7, "etapa", "En cola")])])
    b = board(gh)
    gh.listing = json.dumps(issue_node(7))
    assert b.write([FieldWrite(7, "etapa", "En cola")]).written == frozenset({7})
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


# -- GH-425: declared types, blame anywhere in the path, sent only when `data` confirms ------

SCHEMA = json.loads((Path(__file__).parent / "fixtures" / "project_v2_schema.json").read_text())
FIELD_VALUE = SCHEMA["ProjectV2FieldValue"]
ISSUE_ERROR = {
    "message": "Type mismatch on variable $i406_etapa and argument singleSelectOptionId "
    "(ID! / String)",
    "path": ["mutation", "i406_etapa", "input", "value", "singleSelectOptionId"],
    "extensions": {"code": "variableMismatch"},
}
ALL_FIVE = [
    FieldWrite(7, "etapa", "Trabada"),
    FieldWrite(7, "bloqueo", "#3"),
    FieldWrite(7, "costo", 1.5),
    FieldWrite(7, "inicio", date(2026, 10, 20)),
    FieldWrite(7, "fin", date(2026, 10, 21)),
]


def schema_type(argument: str) -> str:
    if argument in FIELD_VALUE:
        return FIELD_VALUE[argument]["type"]
    found = {t[argument]["type"] for n, t in SCHEMA.items() if n != "_source" and argument in t}
    assert len(found) == 1, f"{argument}: the input types disagree {found}"
    return found.pop()


def mismatches(query: str) -> tuple[list[str], dict[str, str]]:
    """The declared variable types the schema fixture rejects, and each variable's argument.
    The argument of a variable is read from the emitted text only."""
    head, _, rest = query.partition(") {")
    declared = dict(re.findall(r"\$(\w+): ([\w!]+)", head))
    arguments: dict[str, str] = {}
    for key, var in re.findall(r"value: \{(\w+): \$(\w+)\}", rest):
        arguments[var] = key
    for key, var in re.findall(r"(projectId|itemId|fieldId|contentId): \$(\w+)", rest):
        arguments[var] = key
    problems = []
    for var, type_name in declared.items():
        if var not in arguments:
            problems.append(f"${var} is declared and no argument takes it")
            continue
        takes = schema_type(arguments[var])
        base_ok = type_name.rstrip("!") == takes.rstrip("!")
        bang_ok = type_name.endswith("!") or not takes.endswith("!")
        if not (base_ok and bang_ok):
            problems.append(f"${var}: {type_name} but {arguments[var]} takes {takes}")
    return problems, arguments


def emitted(writes, numbers=(7,)):
    ids = board_module._identity_of(identity(), "o/3")
    batch = {n: [w for w in writes if w.issue == n] for n in numbers}
    items = {n: f"PVTI_{n}" for n in numbers}
    return board_module._fields(ids, batch, items)[0], board_module._adds(
        ids, batch, {n: f"I_{n}" for n in numbers}
    )[0]


def test_every_declared_variable_has_the_type_its_argument_takes():
    fields, adds = emitted(ALL_FIVE)
    problems, arguments = mismatches(fields)
    assert problems == []
    assert arguments == {
        "i7_etapa": "singleSelectOptionId",
        "i7_bloqueo": "text",
        "i7_costo": "number",
        "i7_inicio": "date",
        "i7_fin": "date",
        "f_etapa": "fieldId",
        "f_bloqueo": "fieldId",
        "f_costo": "fieldId",
        "f_inicio": "fieldId",
        "f_fin": "fieldId",
        "project": "projectId",
        "i7_item": "itemId",
    }
    problems, arguments = mismatches(adds)
    assert problems == []
    assert arguments == {"project": "projectId", "i7_item": "contentId"}


def test_the_type_check_rejects_an_id_variable_on_single_select_option_id():
    control = (
        "mutation($x: ID!) { a: updateProjectV2ItemFieldValue("
        "input: {value: {singleSelectOptionId: $x}}) { clientMutationId } }"
    )
    problems, _ = mismatches(control)
    assert problems == ["$x: ID! but singleSelectOptionId takes String"]
    assert mismatches(control.replace("ID!", "String!"))[0] == []
    assert mismatches(control.replace("ID!", "String"))[0] == []
    assert mismatches("mutation($x: String) { a: f(input: {itemId: $x}) { id } }")[0] != []
    assert mismatches("mutation($x: ID!) { a: f(input: {itemId: $y}) { id } }")[0] != []


def test_each_field_is_written_under_the_key_its_type_takes():
    keys = {
        "etapa": "singleSelectOptionId",
        "bloqueo": "text",
        "costo": "number",
        "inicio": "date",
        "fin": "date",
    }
    fields, _ = emitted(ALL_FIVE)
    for field, key in keys.items():
        assert f"i7_{field}: updateProjectV2ItemFieldValue(" in fields
        assert re.search(rf"value: \{{{key}: \$i7_{field}\}}", fields), field


def test_only_the_single_select_option_id_type_is_observed_and_it_rejects_id():
    observed = {
        name
        for table in SCHEMA.values()
        for name, entry in table.items()
        if isinstance(entry, dict) and entry.get("evidence") == "observed"
    }
    assert observed == {"singleSelectOptionId"}
    assert FIELD_VALUE["singleSelectOptionId"] == {
        "type": "String",
        "evidence": "observed",
        "cite": "#425",
    }


def error_response(*errors, data=None, **extra) -> dict:
    return {"errors": list(errors), "data": data, **extra}


def test_an_error_whose_alias_is_deep_in_the_path_blames_that_issue():
    gh = FakeGh([error_response(ISSUE_ERROR)])
    data, blamed = board(gh)._send("mutation { x }", {})
    assert blamed == frozenset({406}) and data == {}


def test_a_validation_response_records_no_issue_as_sent():
    gh = FakeGh([identity(), added(406, 407), error_response(ISSUE_ERROR)])
    b = board(gh)
    prime(gh, b, 406, 407)
    writes = [FieldWrite(n, "etapa", "En cola") for n in (406, 407)]
    assert b.write(writes).written == frozenset()


def test_an_issue_whose_alias_is_missing_or_null_in_data_is_not_sent():
    writes = [FieldWrite(n, "etapa", "En cola") for n in (7, 8, 9)]
    data = {"i7_etapa": {"clientMutationId": None}, "i8_etapa": None}
    gh = FakeGh([identity(), added(7, 8, 9), {"data": data}])
    b = board(gh)
    prime(gh, b, 7, 8, 9)
    assert b.write(writes).written == frozenset({7})


def test_an_issue_is_sent_only_when_each_alias_it_wrote_is_in_data():
    writes = [FieldWrite(7, "etapa", "En cola"), FieldWrite(7, "fin", None)]
    short = {"data": {"i7_etapa": {"clientMutationId": None}}}
    gh = FakeGh([identity(), added(7), short])
    b = board(gh)
    prime(gh, b, 7)
    assert b.write(writes).written == frozenset()


def logged_write(response, writes=None):
    lines: list[str] = []
    gh = FakeGh([identity(), added(7), response])
    b = board(gh, log=lines.append)
    prime(gh, b, 7)
    done = b.write(writes or [FieldWrite(7, "etapa", "En cola")]).written
    return done, lines, gh


def test_an_error_with_no_alias_is_logged_once_with_its_summary_by_write():
    error = {"message": "rate limited", "path": ["mutation"], "extensions": {"code": "RATE"}}
    done, lines, _ = logged_write(error_response(error))
    summary = board_module._summary([error])
    assert done == frozenset()
    assert "rate limited" in summary and "RATE" in summary
    assert "\n".join(lines).count(summary) == 1


def test_an_error_that_blames_an_issue_is_logged_once_with_its_summary_by_send():
    done, lines, _ = logged_write(
        error_response(ISSUE_ERROR, data={"i7_etapa": {"clientMutationId": None}})
    )
    summary = board_module._summary([ISSUE_ERROR])
    assert done == frozenset({7})  # the error blames 406, not 7
    assert "\n".join(lines).count(summary) == 1
    assert not any("batch failed" in line for line in lines)


@pytest.mark.parametrize("path", [["mutation"], ["i7_bloqueo"]])
def test_a_request_variable_and_other_body_keys_never_reach_the_log(path):
    sentinel = "SENTINEL-bloqueo-value"
    leak = "SENTINEL-body-field"
    error = {"message": "boom", "path": path, "extensions": {"code": "X", "leak": leak}}
    response = error_response(error, data=None, leak=leak, extensions={"leak": leak})
    writes = [FieldWrite(7, "etapa", "En cola"), FieldWrite(7, "bloqueo", sentinel)]
    done, lines, gh = logged_write(response, writes)
    assert gh.bodies[2]["variables"]["i7_bloqueo"] == sentinel
    text = "\n".join(lines)
    assert text and sentinel not in text and leak not in text and done == frozenset()


def test_a_summary_has_two_errors_a_count_of_the_rest_and_at_most_300_characters():
    errors = [{"message": f"m{i}\n  spaced", "path": ["a", i]} for i in range(5)]
    summary = board_module._summary(errors)
    assert "m0" in summary and "m1" in summary and "m2" not in summary
    assert "+3 more" in summary and "\n" not in summary and "  " not in summary
    assert len(board_module._summary([{"message": "x" * 1000}])) == 300


def test_a_chunk_with_an_error_naming_no_issue_does_not_block_the_next_one():
    numbers = list(range(1, 42))
    writes = [FieldWrite(n, "etapa", "En cola") for n in numbers]
    gh = FakeGh(
        [
            identity(),
            added(*numbers[:20]),
            {"errors": [{"message": "no alias"}]},
            added(*numbers[20:40]),
            wrote(writes[20:40]),
            added(41),
            wrote(writes[40:]),
        ]
    )
    b = board(gh)
    prime(gh, b, *numbers)
    got = b.write(writes)
    assert got == BoardWritten(frozenset(numbers[20:]), True)
    assert len(gh.bodies) == 7


# -- GH-438: chunks, retries, halving, verdicts, deadline ---------------------------------------

BAD_GATEWAY = "<center><h1>502 Bad Gateway</h1></center>\n<hr><center>nginx</center>"
MUTATION = re.compile(r"\b(i(\d+)_([a-z]+)): (\w+)\(")


class FakeClock:
    """A monotonic clock that only moves when something sleeps or a `gh` call is made."""

    def __init__(self, tick: float = 0.0):
        self.now = 0.0
        self.tick = tick
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class SizedGh:
    """A `gh` that understands the mutations it is sent and answers each alias like GitHub.
    `reject_over` answers anything with more mutations than that as the gateway does (exit 1,
    HTML on stdout, `gh: HTTP 502` on stderr); `refuse` maps the 1-based number of a mutation
    request to the (exit code, stdout, stderr) it is answered with instead."""

    def __init__(self, reject_over=None, refuse=None, clock=None):
        self.reject_over = reject_over
        self.refuse = refuse or {}
        self.clock = clock
        self.sizes: list[int] = []
        self.adds: list[int] = []
        self.calls: list[list[str]] = []

    def __call__(self, args, cwd, capture_output, text, check):
        self.calls.append(list(args))
        if self.clock is not None:
            self.clock.now += self.clock.tick
        if args[1:3] != ["api", "graphql"]:
            return subprocess.CompletedProcess(args, 0, "[]", "")
        query = json.loads(Path(args[args.index("--input") + 1]).read_text())["query"]
        if not query.startswith("mutation"):
            return subprocess.CompletedProcess(args, 0, json.dumps(identity()), "")
        found = MUTATION.findall(query)
        self.sizes.append(len(found))
        number = len(self.sizes)
        if number in self.refuse:
            code, out, err = self.refuse[number]
            return subprocess.CompletedProcess(args, code, out, err)
        if self.reject_over is not None and len(found) > self.reject_over:
            return subprocess.CompletedProcess(args, 1, BAD_GATEWAY, "gh: HTTP 502")
        data = {}
        for alias, issue, field, _ in found:
            if field == "add":
                self.adds.append(int(issue))
                data[alias] = {"item": {"id": f"PVTI_{issue}"}}
            else:
                data[alias] = {"clientMutationId": None}
        return subprocess.CompletedProcess(args, 0, json.dumps({"data": data}), "")


def retrying_board(gh, clock=None, lines=None, stop=None) -> GhBoard:
    clock = clock or FakeClock()
    retry = GhRetry(
        max_wait_s=board_module.BOARD_WRITE_WAIT_MAX_S,
        attempts=board_module.BOARD_WRITE_ATTEMPTS,
        backoff_s=board_module.BOARD_WRITE_BACKOFF_S,
        sleep=clock.sleep,
        now=clock,
        stop=stop,
    )
    log = (lambda _line: None) if lines is None else lines.append
    return GhBoard(Path("/p"), "o", 3, gh, log=log, retry=retry, clock=clock)


def sync_of(numbers, fields=("etapa", "bloqueo", "costo", "inicio", "fin")):
    return [FieldWrite(n, f, None) for n in numbers for f in fields]


def run_with_listing(gh: SizedGh, numbers):
    """`gh` that also answers `issue list` with these issues."""
    listing = json.dumps([issue_node(n) for n in numbers])

    def run(args, cwd, capture_output, text, check):
        if args[1:3] == ["issue", "list"]:
            gh.calls.append(list(args))
            return subprocess.CompletedProcess(args, 0, listing, "")
        return gh(args, cwd, capture_output, text, check)

    return run


def synced(numbers, gh: SizedGh, **kwargs):
    b = retrying_board(gh, **kwargs)
    b.run = run_with_listing(gh, numbers)
    b.open_issues()
    return b


def test_a_125_write_sync_goes_out_in_requests_of_at_most_the_limit_adds_included():
    numbers = list(range(1, 26))
    gh = SizedGh()
    got = synced(numbers, gh).write(sync_of(numbers))
    assert got == BoardWritten(frozenset(numbers), True)
    assert max(gh.sizes) <= board_module.MUTATIONS_PER_REQUEST
    assert sum(gh.sizes) == 25 + 125 and sorted(gh.adds) == numbers


def test_each_chunk_of_adds_is_followed_by_the_fields_of_the_issues_it_added():
    numbers = list(range(1, 26))
    gh = SizedGh()
    synced(numbers, gh).write(sync_of(numbers, fields=("etapa",)))
    assert gh.sizes == [20, 20, 5, 5]


def test_a_gateway_that_rejects_big_requests_still_gets_every_write_through(monkeypatch):
    monkeypatch.setattr(board_module, "MUTATIONS_PER_REQUEST", 40)
    numbers = list(range(1, 26))
    gh = SizedGh(reject_over=10)
    lines: list[str] = []
    got = synced(numbers, gh, lines=lines).write(sync_of(numbers))
    assert got == BoardWritten(frozenset(numbers), True)
    assert sum(size for size in gh.sizes if size <= 10) == 25 + 125
    assert any(size > 10 for size in gh.sizes)


def test_one_chunk_that_keeps_failing_does_not_block_the_next():
    numbers = list(range(1, 41))
    gh = SizedGh(refuse={1: (0, json.dumps({"errors": [{"message": "no alias"}]}), "")})
    lines: list[str] = []
    got = synced(numbers, gh, lines=lines).write(sync_of(numbers, fields=("etapa",)))
    assert got == BoardWritten(frozenset(range(21, 41)), True)
    assert any("a chunk was not written" in line for line in lines)


@pytest.mark.parametrize(
    "refusal",
    [
        (1, "", "gh: Resource not accessible by integration (HTTP 403)"),
        (1, "", "gh: HTTP 429"),
        (0, json.dumps({"errors": [{"type": "RATE_LIMITED", "message": "slow"}]}), ""),
        (0, json.dumps({"errors": [{"extensions": {"code": "RATE_LIMITED"}, "message": "x"}]}), ""),
    ],
)
def test_a_403_a_429_and_a_rate_limited_error_stop_the_write_with_no_later_request(refusal):
    numbers = list(range(1, 26))
    gh = SizedGh(refuse={3: refusal})
    lines: list[str] = []
    got = synced(numbers, gh, lines=lines).write(sync_of(numbers, fields=("etapa",)))
    assert got == BoardWritten(frozenset(range(1, 21)), False)
    assert len(gh.sizes) == 3
    assert any("write stopped because of" in line for line in lines)
    assert not any("<" in line or "slow" in line for line in lines)


def test_a_stop_with_nothing_sent_raises_its_cause():
    gh = SizedGh(refuse={1: (1, "", "gh: HTTP 429")})
    with pytest.raises(ValueError, match=r"\(HTTP 429\)"):
        synced([1], gh).write(sync_of([1]))
    assert len(gh.sizes) == 1


def test_gh_not_signed_in_stops_the_write():
    gh = SizedGh(refuse={1: (4, "", "To get started with GitHub CLI, please run: gh auth login")})
    with pytest.raises(ValueError, match="not authenticated"):
        synced([1], gh).write(sync_of([1]))
    assert len(gh.sizes) == 1


def test_a_missing_gh_stops_the_write():
    gh = SizedGh()
    b = synced([1], gh)

    def no_gh(args, **kwargs):
        raise FileNotFoundError(2, "gh")

    b.run = no_gh
    with pytest.raises(ValueError, match="not installed"):
        b.write(sync_of([1]))


def test_an_unreadable_body_is_split_until_it_reads():
    numbers = list(range(1, 5))
    gh = SizedGh(refuse={1: (0, "<html>", "")})
    got = synced(numbers, gh).write(sync_of(numbers, fields=("etapa",)))
    assert got == BoardWritten(frozenset(numbers), True)
    assert gh.sizes == [4, 2, 2, 4]


def test_an_exit_with_no_status_and_no_json_is_an_unreadable_body_and_split():
    numbers = list(range(1, 5))
    gh = SizedGh(refuse={1: (1, "", "gh: something went wrong")})
    got = synced(numbers, gh).write(sync_of(numbers, fields=("etapa",)))
    assert got == BoardWritten(frozenset(numbers), True)
    assert gh.sizes[:2] == [4, 2]


def test_a_single_issue_that_keeps_failing_is_given_up_with_its_status_in_the_log():
    gh = SizedGh(reject_over=0)
    lines: list[str] = []
    got = synced([7], gh, lines=lines).write(sync_of([7], fields=("etapa",)))
    assert got == BoardWritten(frozenset(), False)
    gave_up = [line for line in lines if "gave up" in line]
    assert len(gave_up) == 1 and gave_up[0].endswith("(HTTP 502)")
    assert not any("Bad Gateway" in line or "nginx" in line for line in lines)


def test_a_split_waits_the_backoff_after_the_retry():
    clock = FakeClock()
    gh = SizedGh(reject_over=1)
    synced([1, 2], gh, clock=clock).write(sync_of([1, 2], fields=("etapa",)))
    # per level: 5 s before the retry of the rejected request, 5 s more before the split
    assert sum(clock.slept) == 2 * (5 + 5)


def test_without_a_policy_a_gateway_error_is_skipped_with_its_status_and_not_split():
    gh = SizedGh(reject_over=0)
    lines: list[str] = []
    b = GhBoard(Path("/p"), "o", 3, run_with_listing(gh, [1, 2]), log=lines.append)
    b.open_issues()
    got = b.write(sync_of([1, 2], fields=("etapa",)))
    assert got == BoardWritten(frozenset(), True)
    assert len(gh.sizes) == 1
    assert lines[-1].endswith("(HTTP 502)")


def test_a_policy_never_applies_to_the_reads():
    calls = []

    def failing(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 1, "", "gh: HTTP 502")

    b = retrying_board(SizedGh())
    b.run = failing
    with pytest.raises(ValueError):
        b.open_issues()
    assert len(calls) == 1


def test_the_deadline_defers_the_rest_and_says_how_many():
    clock = FakeClock(tick=70)
    gh = SizedGh(clock=clock)
    lines: list[str] = []
    numbers = list(range(1, 46))
    got = synced(numbers, gh, clock=clock, lines=lines).write(sync_of(numbers, fields=("etapa",)))
    assert got == BoardWritten(frozenset(range(1, 21)), False)
    assert len(gh.sizes) == 2
    deadline = "agentic: board: write deadline reached; 25 issues deferred to the next round"
    assert lines.count(deadline) == 1


def test_a_set_stop_flag_ends_the_write_before_any_request():
    class Set:
        def is_set(self) -> bool:
            return True

    gh = SizedGh()
    got = synced([1], gh, stop=Set()).write(sync_of([1]))
    assert got == BoardWritten(frozenset(), False) and gh.sizes == []


@pytest.mark.parametrize(
    ("exc", "verdict"),
    [
        (TrackerUnreachable("x"), "stop"),
        (TrackerRateLimited("x"), "stop"),
        (TrackerRefused("x", status=403), "stop"),
        (TrackerRefused("x", status=429), "stop"),
        (TrackerTransient("x", status=429), "stop"),
        (board_module._Unauthenticated("x"), "stop"),
        (board_module._RateLimited("x"), "stop"),
        (OSError("disk"), "stop"),
        (TrackerTransient("x", status=502), "split"),
        (TrackerTimedOut("x"), "split"),
        (board_module._UnreadableBody("x"), "split"),
        (ValueError("a graphql error that names no issue"), "skip"),
        (TrackerRefused("x", status=404), "skip"),
        (TrackerRefused("x"), "skip"),
    ],
)
def test_the_verdict_of_each_failure(exc, verdict):
    assert board_module._verdict(exc) == verdict


def test_a_value_error_caused_by_a_missing_gh_is_a_stop():
    try:
        try:
            raise FileNotFoundError(2, "gh")
        except FileNotFoundError as cause:
            raise ValueError("gh is not installed") from cause
    except ValueError as exc:
        assert board_module._verdict(exc) == "stop"
