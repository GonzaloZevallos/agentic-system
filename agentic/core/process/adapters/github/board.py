"""`GhBoard`: the GitHub Project the poller mirrors state to, over `gh` (GH-375, ADR-0071).

Write-only as far as state goes. The one thing read from the Project is its identity: the
node ids of the project, its fields and its Etapa options, looked up by name once per
process. Cards are added with `addProjectV2ItemById` (idempotent) and their fields written
with `updateProjectV2ItemFieldValue` / `clearProjectV2ItemFieldValue`, 25 issues per batch,
two requests per batch.

Every value travels as a typed GraphQL variable in a JSON body (`gh api graphql --input`):
`-f`/`-F` would let `gh` guess types and read `@file`. Only aliases of the form
`i<number>_<field>`, the number an `int` and the field from `_ALIAS_FIELDS`, are written
into the query text. A reason such as `"} mutation { x` therefore arrives as data.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path
from typing import Any, Literal, get_args

from ...application.ports import IssueRow
from ...domain.board import Etapa, Field, FieldWrite
from .run import Run, bounded_run, print_line, run_gh

BOARD_TIMEOUT_S: float = 30
"""Each Project call's own bound (the tracker's is `GH_TIMEOUT_S`, 60)."""
BATCH = 25
LIST_LIMIT = 1000

_FIELD_NAMES: dict[Field, str] = {
    "etapa": "Etapa",
    "bloqueo": "Bloqueo",
    "costo": "Costo USD",
    "inicio": "Inicio",
    "fin": "Fin",
}
_FIELD_TYPES: dict[Field, str] = {
    "etapa": "SINGLE_SELECT",
    "bloqueo": "TEXT",
    "costo": "NUMBER",
    "inicio": "DATE",
    "fin": "DATE",
}
# What each field's variable is declared as, and the key `ProjectV2FieldValue` takes it under.
_VALUE: dict[Field, tuple[str, str]] = {
    "etapa": ("ID!", "singleSelectOptionId"),
    "bloqueo": ("String!", "text"),
    "costo": ("Float!", "number"),
    "inicio": ("Date!", "date"),
    "fin": ("Date!", "date"),
}
# The only words that follow `i<number>_` in the query text.
_ALIAS_FIELDS: tuple[str, ...] = ("add", "item", *_FIELD_NAMES)
_ALIAS = re.compile(r"i(\d+)_([a-z]+)")

_IDENTITY = """
query($owner: String!, $number: Int!) {
  repositoryOwner(login: $owner) {
    ... on ProjectV2Owner {
      projectV2(number: $number) {
        id
        fields(first: 50) {
          nodes {
            ... on ProjectV2Field { id name dataType }
            ... on ProjectV2SingleSelectField { id name dataType options { id name } }
          }
        }
      }
    }
  }
}
"""
_ISSUE_JSON = "number,id,labels,state,closedAt"


@dataclass(frozen=True)
class _Identity:
    project: str
    fields: Mapping[Field, str]
    options: Mapping[str, str]


def _alias(number: int, name: str) -> str:
    if name not in _ALIAS_FIELDS:
        raise ValueError(f"not a board alias field: {name!r}")
    return f"i{int(number)}_{name}"


def _day(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value).astimezone(UTC).date()


def _row_of(raw: Mapping[str, Any]) -> tuple[IssueRow, str]:
    state: Literal["open", "closed"] = "open" if str(raw["state"]).upper() == "OPEN" else "closed"
    labels = frozenset(str(label["name"]) for label in raw["labels"])
    row = IssueRow(int(raw["number"]), state, labels, _day(raw.get("closedAt")))
    return row, str(raw["id"])


class GhBoard:
    def __init__(
        self,
        cwd: Path,
        owner: str,
        number: int,
        run: Run | None = None,
        log: Callable[[str], None] = print_line,
    ):
        self.cwd = cwd
        self.owner = owner
        self.number = number
        self.run: Run = run if run is not None else partial(bounded_run, timeout_s=BOARD_TIMEOUT_S)
        self.log = log
        self._identity: _Identity | None = None
        self._nodes: dict[int, str] = {}

    # -- the transport --------------------------------------------------------------------

    def _gh(self, *args: str) -> str:
        return run_gh(
            self._keeping_errors,
            args,
            cwd=self.cwd,
            not_installed="gh is not installed, so the board cannot be mirrored",
            failed=lambda _stderr: f"gh {' '.join(args[:2])} failed",
            log=self.log,
        )

    def _keeping_errors(
        self, args: Sequence[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        """`gh api graphql` exits 1 when the response carries `errors` but still prints the
        body, and the per-issue attribution needs that body: a failed call whose stdout is a
        GraphQL response is handed back as a result, any other failure as it was."""
        result = self.run(args, **{**kwargs, "check": False})
        if result.returncode == 0:
            return result
        try:
            body = json.loads(result.stdout)
        except ValueError:
            body = None
        if isinstance(body, dict) and "errors" in body:
            return subprocess.CompletedProcess(result.args, 0, result.stdout, result.stderr)
        raise subprocess.CalledProcessError(result.returncode, args, result.stdout, result.stderr)

    def _graphql(self, query: str, variables: Mapping[str, object]) -> dict[str, Any]:
        handle, name = tempfile.mkstemp(prefix="board-", suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                out.write(json.dumps({"query": query, "variables": variables}))
            text = self._gh("api", "graphql", "--input", name)
        finally:
            Path(name).unlink(missing_ok=True)
        body = json.loads(text)
        if not isinstance(body, dict):
            raise ValueError("gh api graphql answered something other than an object")
        return body

    # -- identity -------------------------------------------------------------------------

    def project_id(self) -> str:
        return self._ids().project

    def _ids(self) -> _Identity:
        if self._identity is None:
            body = self._graphql(_IDENTITY, {"owner": self.owner, "number": self.number})
            self._identity = _identity_of(body, f"{self.owner}/{self.number}")
        return self._identity

    # -- issue reads ----------------------------------------------------------------------

    def _rows(self, text: str) -> list[IssueRow]:
        rows = []
        for raw in json.loads(text):
            row, node = _row_of(raw)
            self._nodes[row.number] = node
            rows.append(row)
        return rows

    def open_issues(self) -> list[IssueRow]:
        listing = ("issue", "list", "--state", "open", "--limit", str(LIST_LIMIT))
        return self._rows(self._gh(*listing, "--json", _ISSUE_JSON))

    def all_issues(self) -> tuple[list[IssueRow], bool]:
        listing = ("issue", "list", "--state", "all", "--limit", str(LIST_LIMIT))
        rows = self._rows(self._gh(*listing, "--json", _ISSUE_JSON))
        return rows, len(rows) >= LIST_LIMIT

    def issue(self, number: int) -> IssueRow:
        row, node = _row_of(
            json.loads(self._gh("issue", "view", str(number), "--json", _ISSUE_JSON))
        )
        self._nodes[row.number] = node
        return row

    # -- writes ---------------------------------------------------------------------------

    def write(self, writes: Sequence[FieldWrite]) -> frozenset[int]:
        by_issue: dict[int, list[FieldWrite]] = defaultdict(list)
        for item in writes:
            by_issue[item.issue].append(item)
        ids = self._ids()
        done: set[int] = set()
        numbers = sorted(by_issue)
        for start in range(0, len(numbers), BATCH):
            batch = {n: by_issue[n] for n in numbers[start : start + BATCH]}
            try:
                done |= self._batch(ids, batch)
            except (ValueError, OSError):
                self.log("agentic: a board batch failed; its issues are not recorded as sent")
                break
        return frozenset(done)

    def _batch(self, ids: _Identity, batch: Mapping[int, list[FieldWrite]]) -> frozenset[int]:
        for number in batch:
            if number not in self._nodes:
                self.issue(number)
        added, bad_adds = self._send(*_adds(ids, batch, self._nodes))
        items: dict[int, str] = {}
        for number in batch:
            item = (added.get(_alias(number, "add")) or {}).get("item") or {}
            if number not in bad_adds and isinstance(item.get("id"), str):
                items[number] = item["id"]
        if not items:
            return frozenset()
        _, bad_writes = self._send(*_fields(ids, {n: batch[n] for n in items}, items))
        return frozenset(items) - bad_writes

    def _send(
        self, query: str, variables: Mapping[str, object]
    ) -> tuple[dict[str, Any], frozenset[int]]:
        """The response's `data` and the issues an error names. An error with no usable path,
        or a response that is not parseable, fails the whole request: `ValueError`."""
        body = self._graphql(query, variables)
        data = body.get("data")
        errors = body.get("errors") or []
        bad: set[int] = set()
        for error in errors:
            path = error.get("path") if isinstance(error, dict) else None
            match = _ALIAS.fullmatch(str(path[0])) if isinstance(path, list) and path else None
            if match is None:
                raise ValueError("a board response carried an error with no issue to blame")
            bad.add(int(match.group(1)))
        return (data if isinstance(data, dict) else {}), frozenset(bad)


def _identity_of(body: Mapping[str, Any], name: str) -> _Identity:
    try:
        project = body["data"]["repositoryOwner"]["projectV2"]
        nodes = [n for n in project["fields"]["nodes"] if n]
        project_id = str(project["id"])
    except (KeyError, TypeError) as exc:
        raise ValueError(f"the board project {name} was not found") from exc
    fields: dict[Field, str] = {}
    options: dict[str, str] = {}
    for key, title in _FIELD_NAMES.items():
        found = [
            n for n in nodes if n.get("name") == title and n.get("dataType") == _FIELD_TYPES[key]
        ]
        if not found:
            raise ValueError(f"the board project {name} has no {_FIELD_TYPES[key]} field {title!r}")
        fields[key] = str(found[0]["id"])
        if key == "etapa":
            options = {str(o["name"]): str(o["id"]) for o in found[0].get("options", [])}
    for etapa in get_args(Etapa):
        if etapa not in options:
            raise ValueError(f"the board project {name} has no Etapa option {etapa!r}")
    return _Identity(project_id, fields, options)


def _adds(
    ids: _Identity, batch: Mapping[int, Sequence[FieldWrite]], nodes: Mapping[int, str]
) -> tuple[str, dict[str, object]]:
    declared, calls = ["$project: ID!"], []
    variables: dict[str, object] = {"project": ids.project}
    for number in batch:
        content = _alias(number, "item")
        declared.append(f"${content}: ID!")
        variables[content] = nodes[number]
        calls.append(
            f"{_alias(number, 'add')}: addProjectV2ItemById("
            f"input: {{projectId: $project, contentId: ${content}}}) {{ item {{ id }} }}"
        )
    return _mutation(declared, calls), variables


def _fields(
    ids: _Identity, batch: Mapping[int, Sequence[FieldWrite]], items: Mapping[int, str]
) -> tuple[str, dict[str, object]]:
    declared = ["$project: ID!"]
    variables: dict[str, object] = {"project": ids.project}
    used = {write.field for writes in batch.values() for write in writes}
    for key, field_id in ids.fields.items():
        if key in used:  # GraphQL rejects a declared variable the query never uses
            declared.append(f"$f_{key}: ID!")
            variables[f"f_{key}"] = field_id
    calls = []
    for number, writes in batch.items():
        item = _alias(number, "item")
        declared.append(f"${item}: ID!")
        variables[item] = items[number]
        for write in writes:
            alias = _alias(number, write.field)
            common = f"projectId: $project, itemId: ${item}, fieldId: $f_{write.field}"
            if write.value is None:
                calls.append(
                    f"{alias}: clearProjectV2ItemFieldValue(input: {{{common}}}) "
                    "{ clientMutationId }"
                )
                continue
            type_name, value_key = _VALUE[write.field]
            declared.append(f"${alias}: {type_name}")
            variables[alias] = _plain(write, ids)
            calls.append(
                f"{alias}: updateProjectV2ItemFieldValue(input: {{{common}, "
                f"value: {{{value_key}: ${alias}}}}}) {{ clientMutationId }}"
            )
    return _mutation(declared, calls), variables


def _plain(write: FieldWrite, ids: _Identity) -> object:
    value = write.value
    if write.field == "etapa":
        return ids.options[str(value)]
    if isinstance(value, date):
        return value.isoformat()
    return value


def _mutation(declared: Sequence[str], calls: Sequence[str]) -> str:
    return f"mutation({', '.join(declared)}) {{ {' '.join(calls)} }}"
