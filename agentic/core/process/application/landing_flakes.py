"""The flake issues of the landing (GH-239, ADR-0048, GH-464): a check that failed on a head
and passed on a rerun of the same head is flaky and recorded; the system files one `agentic`
issue per check name only once a key is over the threshold (`flake_issue_tasks` tasks within
`flake_issue_days` days), instead of leaving it to the human to notice. Found among the open
issues by marker or exact title, under the per-product flake lock."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime, timedelta
from typing import get_args

from agentic.shared.configuration import Configuration

from ..domain import landing as landing_rules
from ..domain.landing import IssueRef
from ..domain.presubmit import FlakeGate
from ..domain.task import FlakeRecord
from .ports import Ports

FLAKE_LABEL = "agentic"


_GATES: dict[object, FlakeGate] = {gate: gate for gate in get_args(FlakeGate)}


def _clean(value: object) -> list[FlakeRecord]:
    """The well-formed records of a `flakes` value: a dict naming its check, head and a known
    gate, its `tests` reduced to the strings in it, `at` kept only when it is a string.
    Anything else a hand-edited or older document holds (an absent value, a non-list) yields
    none."""
    records: list[FlakeRecord] = []
    for entry in value if isinstance(value, list) else []:
        if not isinstance(entry, dict):
            continue
        check, head, gate = entry.get("check"), entry.get("head"), entry.get("gate")
        if not (isinstance(check, str) and isinstance(head, str) and gate in _GATES):
            continue
        tests = entry.get("tests")
        clean = [t for t in tests if isinstance(t, str)] if isinstance(tests, list) else []
        record = FlakeRecord(check=check, head=head, gate=_GATES[gate], tests=clean)
        at = entry.get("at")
        if isinstance(at, str):
            record["at"] = at
        records.append(record)
    return records


def _grouped(records: list[FlakeRecord]) -> dict[str, list[FlakeRecord]]:
    """The records by check, in the order they were recorded."""
    grouped: dict[str, list[FlakeRecord]] = {}
    for entry in records:
        grouped.setdefault(entry["check"], []).append(entry)
    return grouped


def _lines(task_id: str, records: list[FlakeRecord]) -> str:
    """One line per record: the task, the head the gate observed it on (not the merge
    head), the gate, and the test ids when the failure named any."""
    lines = []
    for entry in records:
        line = f"- {task_id}: head `{entry.get('head')}`, gate `{entry.get('gate')}`"
        if entry["tests"]:
            line += ", tests: " + ", ".join(f"`{t}`" for t in dict.fromkeys(entry["tests"]))
        lines.append(line)
    return "\n".join(lines)


def rerun_checks_on(raw: object, head: str) -> list[str]:
    """The check names the `rerun` entry on `head` recorded, none when there is none."""
    entries = raw.get("landing") if isinstance(raw, dict) else None
    names: list[str] = []
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and entry.get("step") == "rerun" and entry.get("head") == head:
            value = entry.get("checks")
            names += [n for n in value if isinstance(n, str)] if isinstance(value, list) else []
    return names


def _rerun_records(raw: object, head: str, now: datetime) -> list[FlakeRecord]:
    """The landing's `rerun` step on `head` as flake records at the merge gate, with no test
    ids: they count under the check-level key `(check, "")` (GH-464)."""
    at = now.isoformat(timespec="seconds")
    return [
        {"check": name, "head": head, "gate": "merge", "tests": [], "at": at}
        for name in dict.fromkeys(rerun_checks_on(raw, head))
    ]


def _counted(
    ports: Ports, task_id: str, own: list[FlakeRecord], now: datetime, cfg: Configuration
) -> dict[str, list[str]]:
    """The tests over the issue threshold, by check, among the flake keys this task takes
    part in: the records on disk within the window plus the task's own, one task counted once
    per key. A check-level key over the threshold yields its check with no test."""
    try:
        recent = ports.documents.flakes_since(now - timedelta(days=cfg.flake_issue_days))
    except OSError:
        recent = {}
    over = landing_rules.keys_over_threshold(
        {**recent, task_id: own}, now, cfg.flake_issue_tasks, cfg.flake_issue_days
    )
    found: dict[str, list[str]] = {}
    for (check, test), tasks in over.items():
        if task_id in tasks:
            tests = found.setdefault(check, [])
            if test:
                tests.append(test)
    return found


def file_task_flakes(
    ports: Ports,
    task_id: str,
    raw: object,
    log: Callable[[str], None],
    *,
    cfg: Configuration,
    now: datetime,
    flake_lock: Callable[[], AbstractContextManager[bool]] | None,
    head: str | None = None,
) -> list[str]:
    """The one filing path of the flake issues (ADR-0066, GH-464). The task's `flakes` record
    plus its landing `rerun` step on `head` are counted with the records of the other tasks
    on disk; only when a key the task takes part in is over `flake_issue_tasks` within
    `flake_issue_days` does anything touch the tracker: the per-product lock first, then the
    open issues, then, per check, one issue naming the tests over the threshold when none is
    open, or one comment per (issue, task) when the task brings a test id the issue body does
    not name (`flake_comments` remembers which, so a retried merge does not repeat it).
    Returns the URLs opened. A lock not taken, a list the tracker cannot give whole, or a
    tracker that fails is logged and skipped: a flake never blocks a merge."""
    own = _clean(raw.get("flakes") if isinstance(raw, dict) else None)
    if head is not None:
        own += _rerun_records(raw, head, now)
    if not own:
        return []
    over = _counted(ports, task_id, own, now, cfg)
    if not over:
        return []
    prefix = f"agentic: land: {task_id}: "
    with flake_lock() if flake_lock is not None else nullcontext(False) as held:
        if not held:
            log(f"{prefix}flakes: lock_unavailable")
            return []
        try:
            issues = ports.inbox.open_issue_refs()
        except ValueError as exc:
            log(f"{prefix}flakes: list_saturated: {exc}")
            return []
        except OSError as exc:
            log(f"{prefix}flakes: list_unreadable: {exc}")
            return []
        return _file(ports, task_id, raw, _grouped(own), over, issues, cfg, log)


def _file(
    ports: Ports,
    task_id: str,
    raw: object,
    grouped: dict[str, list[FlakeRecord]],
    over: dict[str, list[str]],
    issues: list[IssueRef],
    cfg: Configuration,
    log: Callable[[str], None],
) -> list[str]:
    """Open or comment, for each check with a key over the threshold, inside the lock."""
    commented = raw.get("flake_comments") if isinstance(raw, dict) else None
    commented = [r for r in commented if isinstance(r, str)] if isinstance(commented, list) else []
    opened: list[str] = []
    for name, tests in over.items():
        text = _lines(task_id, grouped.get(name, []))
        try:
            existing = landing_rules.flake_issue_for(issues, name)
            if existing is None:
                title = f"Flaky check: {name}"
                named = " Over the threshold: " + ", ".join(f"`{t}`" for t in tests) + "."
                body = (
                    f"{landing_rules.flake_marker(name)}\n"
                    f"The check `{name}` failed and then passed on a rerun in "
                    f"{cfg.flake_issue_tasks} or more tasks within {cfg.flake_issue_days} days."
                    f"{named if tests else ''}\n{text}"
                )
                url = ports.tracker.open_issue(title, body, label=FLAKE_LABEL)
                opened.append(url)
                issues = [*issues, IssueRef(url, title, body)]
                continue
            if existing.url in commented or all(t in existing.body for t in tests):
                continue
            ports.tracker.comment_on_issue(
                existing.url, f"The check `{name}` was flaky again:\n{text}"
            )
            commented = [*commented, existing.url]
            ports.documents.record_closure(task_id, {"flake_comments": commented})
        except (ValueError, OSError) as exc:
            log(f"agentic: land: {task_id}: flake issue for {name} not filed: {exc}")
    return opened
