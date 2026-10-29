"""`GhBoard`: the GitHub Project the poller mirrors state to, over `gh` (GH-375, ADR-0073).

Write-only as far as state goes. The one thing read from the Project is its identity: the
node ids of the project, its fields and its Etapa options, looked up once per process. The
fields are found by name and type; Etapa is the SINGLE_SELECT `Etapa` or, absent it, the
built-in `Status` carrying the seven options (`_etapa_field`). Cards are added with
`addProjectV2ItemById` (idempotent) and their fields written with
`updateProjectV2ItemFieldValue` / `clearProjectV2ItemFieldValue`.

Requests are sized by mutation count (`MUTATIONS_PER_REQUEST`, `_chunks`), an issue never split
across two: each chunk of adds is followed by the field chunks of the issues it added (GH-438).
A failed chunk does not end the round. With a `GhRetry` policy (writes only; reads and the
identity keep today's calls) `_verdict` decides what a failure does: `stop` ends the write,
`split` waits and halves the chunk down to one issue, `skip` logs it and goes on. The write
stops at `BOARD_WRITE_DEADLINE_S`, measured with the injected monotonic `clock`, and never
raises once an issue was sent: `BoardWritten` says what was and whether a transport cause cut
the round short.

Every value travels as a typed GraphQL variable in a JSON body (`gh api graphql --input`):
`-f`/`-F` would let `gh` guess types and read `@file`. Only aliases of the form
`i<number>_<field>`, the number an `int` and the field from `_ALIAS_FIELDS`, are written
into the query text. A reason such as `"} mutation { x` therefore arrives as data. A log line
carries what `gh` printed only as a three-digit status, `(HTTP nnn)`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path
from typing import Any, Literal, NoReturn, TypeVar, get_args

from ...application.ports import (
    BoardWritten,
    IssueRow,
    TrackerCallFailed,
    TrackerRateLimited,
    TrackerRefused,
    TrackerTimedOut,
    TrackerTransient,
    TrackerUnreachable,
)
from ...domain.board import Etapa, Field, FieldWrite
from .run import GH_EXIT_AUTH, GhRetry, Run, bounded_run, http_status, print_line, run_gh

BOARD_TIMEOUT_S: float = 30
"""Each Project call's own bound (the tracker's is `GH_TIMEOUT_S`, 60)."""
# GH-438: a document of about 125 aliased mutations (25 issues x 5 fields) got a 502 from
# GitHub's gateway, while one issue with one write went through. The limit bounds the aliased
# mutations of one request, adds and field writes alike; it is the order of magnitude that
# passed, not a number GitHub publishes. An issue is never split across two requests, so it
# must hold the writes of one issue.
MUTATIONS_PER_REQUEST = 20
LIST_LIMIT = 1000
# The write policy (GH-438), built by the composition root: one retry after 5 s, no wait over
# 30 s, and the whole write given up 120 s in (one request, at most about 65 s, may still be
# in flight). A partial or failed round rests 15 minutes (`domain.board.BOARD_RETRY_AFTER`).
BOARD_WRITE_ATTEMPTS = 2
BOARD_WRITE_BACKOFF_S: tuple[float, ...] = (5,)
BOARD_WRITE_WAIT_MAX_S: float = 30
BOARD_WRITE_DEADLINE_S: float = 120
# A 403 or a 429 is GitHub refusing the caller, not a gateway failing: more requests would
# only be refused too, so the write stops.
BOARD_STOP_STATUSES: frozenset[int] = frozenset({403, 429})
ERROR_SUMMARY_CHARS = 300
ERRORS_SHOWN = 2

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
# The declared type is the one the key's argument takes, not the type of the id it carries:
# `singleSelectOptionId` takes `String`, so the option id travels as `String!`.
_VALUE: dict[Field, tuple[str, str]] = {
    "etapa": ("String!", "singleSelectOptionId"),
    "bloqueo": ("String!", "text"),
    "costo": ("Float!", "number"),
    "inicio": ("Date!", "date"),
    "fin": ("Date!", "date"),
}
# The only words that follow `i<number>_` in the query text.
_ALIAS_FIELDS: tuple[str, ...] = ("add", "item", *_FIELD_NAMES)
_ALIAS = re.compile(r"i(\d+)_([a-z]+)")
assert MUTATIONS_PER_REQUEST >= len(_FIELD_NAMES), "one issue's writes must fit in one request"

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


def _chunks(costs: Mapping[int, int], limit: int) -> list[tuple[int, ...]]:
    """The issues in ascending order, grouped so each group's total cost stays within `limit`.
    An issue is never split: one that alone costs more than `limit` travels by itself."""
    chunks: list[tuple[int, ...]] = []
    current: list[int] = []
    total = 0
    for number in sorted(costs):
        if current and total + costs[number] > limit:
            chunks.append(tuple(current))
            current, total = [], 0
        current.append(number)
        total += costs[number]
    if current:
        chunks.append(tuple(current))
    return chunks


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
        retry: GhRetry | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.cwd = cwd
        self.owner = owner
        self.number = number
        self.run: Run = run if run is not None else partial(bounded_run, timeout_s=BOARD_TIMEOUT_S)
        self.log = log
        self.retry = retry
        self.clock = clock
        self._exit = 0
        self._identity: _Identity | None = None
        self._nodes: dict[int, str] = {}

    # -- the transport --------------------------------------------------------------------

    def _gh(self, *args: str, write: bool = False) -> str:
        """A read or the identity as always; a write goes through the policy, and what `gh`
        refuses with is classified by `_write_failed`."""
        return run_gh(
            self._keeping_errors,
            args,
            cwd=self.cwd,
            not_installed="gh is not installed, so the board cannot be mirrored",
            failed=self._write_failed
            if write
            else lambda _stderr: f"gh {' '.join(args[:2])} failed",
            log=self.log,
            retry=self.retry if write else None,
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
        self._exit = result.returncode
        raise subprocess.CalledProcessError(result.returncode, args, result.stdout, result.stderr)

    def _write_failed(self, stderr: str) -> NoReturn:
        """What a write `gh` refused with is, for `_verdict`: not signed in, an answer with a
        status, or no status at all (an unreadable body). Only `http_status` reads `stderr`."""
        status = http_status(stderr)
        if self._exit == GH_EXIT_AUTH:
            raise _Unauthenticated("gh is not authenticated, so the board cannot be written")
        if status is None:
            raise _UnreadableBody("gh api graphql answered nothing readable")
        raise TrackerRefused(f"gh api graphql failed (HTTP {status})", status=status)

    def _graphql(
        self, query: str, variables: Mapping[str, object], *, write: bool = False
    ) -> dict[str, Any]:
        handle, name = tempfile.mkstemp(prefix="board-", suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                out.write(json.dumps({"query": query, "variables": variables}))
            text = self._gh("api", "graphql", "--input", name, write=write)
        finally:
            Path(name).unlink(missing_ok=True)
        try:
            body = json.loads(text)
        except ValueError as exc:
            if write:
                raise _UnreadableBody("gh api graphql answered something that is not JSON") from exc
            raise
        if not isinstance(body, dict):
            message = "gh api graphql answered something other than an object"
            raise (_UnreadableBody if write else ValueError)(message)
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

    def write(self, writes: Sequence[FieldWrite]) -> BoardWritten:
        by_issue: dict[int, list[FieldWrite]] = defaultdict(list)
        for item in writes:
            by_issue[item.issue].append(item)
        ids = self._ids()
        numbers = sorted(by_issue)
        deadline = None if self.retry is None else self.clock() + BOARD_WRITE_DEADLINE_S
        rnd = _Round(pending=set(numbers), deadline=deadline)
        for adds in _chunks(dict.fromkeys(numbers, 1), MUTATIONS_PER_REQUEST):
            items: dict[int, str] = {}
            for got in self._attempt(rnd, adds, lambda part: self._add(ids, part, by_issue, rnd)):
                items.update(got)
            costs = {n: len(by_issue[n]) for n in items}
            for chunk in _chunks(costs, MUTATIONS_PER_REQUEST):
                for done in self._attempt(
                    rnd, chunk, lambda part: self._set_fields(ids, part, by_issue, items, rnd)
                ):
                    rnd.written |= done
        if rnd.stopped is not None and not rnd.written:
            raise rnd.stopped
        return BoardWritten(frozenset(rnd.written), rnd.complete)

    def _attempt(
        self,
        rnd: _Round,
        numbers: tuple[int, ...],
        step: Callable[[tuple[int, ...]], _T],
    ) -> list[_T]:
        """`step` over `numbers`, and what it returned for each piece that went through. A
        failure is decided by `_verdict`: a stop ends the whole write, a split waits and
        halves (a single issue that fails again is given up), a skip logs and goes on."""
        if self._halted(rnd):
            return []
        try:
            return [step(numbers)]
        except (ValueError, OSError) as exc:
            verdict = _verdict(exc)
            if verdict == "stop":
                self._stop(rnd, exc)
                return []
            if verdict == "skip":
                self.log(f"agentic: board: a chunk was not written: {_shown(exc)}")
                rnd.pending -= set(numbers)
                return []
            if self.retry is None or len(numbers) == 1:
                self.log(f"agentic: board: gave up on issues {_listed(numbers)}: {_shown(exc)}")
                rnd.pending -= set(numbers)
                rnd.complete = False
                return []
            if not self.retry.wait(self.retry.backoff(1)):
                self._halt(rnd, "agentic: board: write stopped because the process is stopping")
                return []
            half = len(numbers) // 2
            return [
                *self._attempt(rnd, numbers[:half], step),
                *self._attempt(rnd, numbers[half:], step),
            ]

    def _halted(self, rnd: _Round) -> bool:
        """Checked before each request: the write already ended, the stop flag is set, or the
        deadline passed."""
        if rnd.halted:
            return True
        retry = self.retry
        if retry is not None and retry.stop is not None and retry.stop.is_set():
            self._halt(rnd, "agentic: board: write stopped because the process is stopping")
        elif rnd.deadline is not None and self.clock() > rnd.deadline:
            self._halt(
                rnd,
                f"agentic: board: write deadline reached; {len(rnd.pending)} issues "
                "deferred to the next round",
            )
        return rnd.halted

    def _halt(self, rnd: _Round, line: str) -> None:
        self.log(line)
        rnd.halted = True
        rnd.complete = False

    def _stop(self, rnd: _Round, exc: BaseException) -> None:
        rnd.stopped = rnd.stopped or exc
        self._halt(rnd, f"agentic: board: write stopped because of {type(exc).__name__}")

    def _add(
        self,
        ids: _Identity,
        numbers: tuple[int, ...],
        by_issue: Mapping[int, list[FieldWrite]],
        rnd: _Round,
    ) -> dict[int, str]:
        """Adds `numbers` to the Project: the card id of each one that was added."""
        for number in numbers:
            if number not in self._nodes:
                self.issue(number)
        added, bad_adds = self._send(*_adds(ids, by_issue, self._nodes, numbers), write=True)
        items: dict[int, str] = {}
        for number in numbers:
            item = (added.get(_alias(number, "add")) or {}).get("item") or {}
            if number not in bad_adds and isinstance(item.get("id"), str):
                items[number] = item["id"]
        rnd.pending -= set(numbers) - set(items)
        return items

    def _set_fields(
        self,
        ids: _Identity,
        numbers: tuple[int, ...],
        by_issue: Mapping[int, list[FieldWrite]],
        items: Mapping[int, str],
        rnd: _Round,
    ) -> frozenset[int]:
        """Writes the fields of `numbers`: the issues whose every alias came back."""
        data, bad_writes = self._send(*_fields(ids, by_issue, items, numbers), write=True)
        rnd.pending -= set(numbers)
        return frozenset(
            number
            for number in numbers
            if number not in bad_writes
            and all(data.get(_alias(number, w.field)) is not None for w in by_issue[number])
        )

    def _send(
        self, query: str, variables: Mapping[str, object], *, write: bool = False
    ) -> tuple[dict[str, Any], frozenset[int]]:
        """The response's `data` and the issues an error names, anywhere in its path. An error
        that names none, or a response that is not parseable, fails the whole request:
        `ValueError` carrying the summary, which `_attempt` logs. When every error blames an
        issue, the summary is logged here, once. A `RATE_LIMITED` error arrives as HTTP 200
        and ends the write (`_RateLimited`)."""
        body = self._graphql(query, variables, write=write)
        data = body.get("data")
        errors = body.get("errors") or []
        if any(_rate_limited(error) for error in errors):
            raise _RateLimited("GitHub's GraphQL rate limit was reached")
        bad: set[int] = set()
        for error in errors:
            blamed = _blamed(error)
            if not blamed:
                raise ValueError(_summary(errors))
            bad |= blamed
        if errors:
            self.log(f"agentic: board: the response carried errors: {_summary(errors)}")
        return (data if isinstance(data, dict) else {}), frozenset(bad)


_T = TypeVar("_T")


@dataclass
class _Round:
    """What one `write` has done so far: the issues still unsettled, the ones sent, and
    whether the write ended (`halted`) and why (`stopped`, the exception of a stop)."""

    pending: set[int]
    deadline: float | None
    written: set[int] = field(default_factory=set)
    complete: bool = True
    halted: bool = False
    stopped: BaseException | None = None


class _UnreadableBody(ValueError):
    """A write's answer was not a GraphQL response: nothing to attribute, the chunk is split."""


class _RateLimited(ValueError):
    """A GraphQL error of type `RATE_LIMITED`: GitHub's answer to everything that follows."""


class _Unauthenticated(TrackerRefused):
    """`gh` exited 4: not signed in. No later request would be any different."""


def _rate_limited(error: object) -> bool:
    if not isinstance(error, dict):
        return False
    extensions = error.get("extensions")
    code = extensions.get("code") if isinstance(extensions, dict) else None
    return error.get("type") == "RATE_LIMITED" or code == "RATE_LIMITED"


def _verdict(exc: BaseException) -> Literal["stop", "split", "skip"]:
    """What one failed chunk does to the write, from the exception alone."""
    stopping = (TrackerUnreachable, TrackerRateLimited, _RateLimited, _Unauthenticated, OSError)
    if isinstance(exc, stopping):
        return "stop"
    if isinstance(exc, TrackerCallFailed | TrackerRefused) and exc.status in BOARD_STOP_STATUSES:
        return "stop"
    if isinstance(exc, ValueError) and isinstance(exc.__cause__, FileNotFoundError):
        return "stop"
    if isinstance(exc, TrackerTransient | TrackerTimedOut | _UnreadableBody):
        return "split"
    return "skip"


def _shown(exc: BaseException) -> str:
    """An exception as a log line says it: our own wording, or only the class name."""
    return str(exc) if isinstance(exc, ValueError) else type(exc).__name__


def _listed(numbers: Sequence[int]) -> str:
    return ", ".join(f"#{n}" for n in numbers)


def _blamed(error: object) -> set[int]:
    path = error.get("path") if isinstance(error, dict) else None
    if not isinstance(path, list):
        return set()
    matches = (_ALIAS.fullmatch(str(part)) for part in path)
    return {int(match.group(1)) for match in matches if match is not None}


def _describe(error: object) -> str:
    """One error as GitHub described it: only `message`, `extensions.code` and `path`."""
    if not isinstance(error, dict):
        return "unreadable error"
    extensions = error.get("extensions")
    code = extensions.get("code") if isinstance(extensions, dict) else None
    return f"{error.get('message')} (code={code}, path={error.get('path')})"


def _summary(errors: object) -> str:
    items = errors if isinstance(errors, list) else [errors]
    text = "; ".join(_describe(error) for error in items[:ERRORS_SHOWN])
    if len(items) > ERRORS_SHOWN:
        text += f"; +{len(items) - ERRORS_SHOWN} more"
    return " ".join(text.split())[:ERROR_SUMMARY_CHARS]


def _identity_of(body: Mapping[str, Any], name: str) -> _Identity:
    try:
        project = body["data"]["repositoryOwner"]["projectV2"]
        nodes = [n for n in project["fields"]["nodes"] if n]
        project_id = str(project["id"])
    except (KeyError, TypeError) as exc:
        raise ValueError(f"the board project {name} was not found") from exc
    etapa_node = _etapa_field(nodes, name)
    fields: dict[Field, str] = {"etapa": str(etapa_node["id"])}
    options = _options_of(etapa_node)
    for key, title in _FIELD_NAMES.items():
        if key == "etapa":
            continue
        found = [
            n for n in nodes if n.get("name") == title and n.get("dataType") == _FIELD_TYPES[key]
        ]
        if not found:
            raise ValueError(f"the board project {name} has no {_FIELD_TYPES[key]} field {title!r}")
        fields[key] = str(found[0]["id"])
    return _Identity(project_id, fields, options)


def _options_of(node: Mapping[str, Any]) -> dict[str, str]:
    return {str(o["name"]): str(o["id"]) for o in node.get("options", [])}


def _single_select(nodes: Sequence[Mapping[str, Any]], title: str) -> Mapping[str, Any] | None:
    found = [n for n in nodes if n.get("name") == title and n.get("dataType") == "SINGLE_SELECT"]
    return found[0] if found else None


def _first_missing_etapa(node: Mapping[str, Any]) -> str | None:
    options = _options_of(node)
    return next((etapa for etapa in get_args(Etapa) if etapa not in options), None)


def _etapa_field(nodes: Sequence[Mapping[str, Any]], name: str) -> Mapping[str, Any]:
    """The field node that carries the Etapa options: the SINGLE_SELECT `Etapa`, even when it
    is incomplete (no fallback then), else the built-in `Status` when it has all seven."""
    etapa_node = _single_select(nodes, "Etapa")
    if etapa_node is not None:
        missing = _first_missing_etapa(etapa_node)
        if missing is not None:
            raise ValueError(f"the board project {name} has no Etapa option {missing!r}")
        return etapa_node
    status_node = _single_select(nodes, "Status")
    if status_node is None:
        raise ValueError(
            f"the board project {name} has no SINGLE_SELECT field 'Etapa' "
            "nor a SINGLE_SELECT field 'Status'"
        )
    missing = _first_missing_etapa(status_node)
    if missing is not None:
        raise ValueError(
            f"the board project {name} has no Etapa option {missing!r} on its Status field"
        )
    return status_node


def _adds(
    ids: _Identity,
    batch: Mapping[int, Sequence[FieldWrite]],
    nodes: Mapping[int, str],
    only: Sequence[int] | None = None,
) -> tuple[str, dict[str, object]]:
    declared, calls = ["$project: ID!"], []
    variables: dict[str, object] = {"project": ids.project}
    for number in batch if only is None else only:
        content = _alias(number, "item")
        declared.append(f"${content}: ID!")
        variables[content] = nodes[number]
        calls.append(
            f"{_alias(number, 'add')}: addProjectV2ItemById("
            f"input: {{projectId: $project, contentId: ${content}}}) {{ item {{ id }} }}"
        )
    return _mutation(declared, calls), variables


def _fields(
    ids: _Identity,
    batch: Mapping[int, Sequence[FieldWrite]],
    items: Mapping[int, str],
    only: Sequence[int] | None = None,
) -> tuple[str, dict[str, object]]:
    batch = batch if only is None else {n: batch[n] for n in only}
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
