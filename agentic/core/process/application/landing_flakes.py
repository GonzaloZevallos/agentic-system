"""The flake issues of the landing (GH-239, ADR-0048): a check that failed on a head and
passed on a rerun of the same head is flaky, and the system files one `agentic` issue per
check name instead of leaving it to the human to notice. Deduplicated on the marker in the
body of every open issue, read locally through `Inbox.open_issue_bodies`."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from ..domain import landing as landing_rules
from .ports import Ports

FLAKE_LABEL = "agentic"


def file_flakes(
    ports: Ports, task_id: str, head: str, checks: Iterable[str], log: Callable[[str], None]
) -> list[str]:
    """The URL of each flake issue opened, one per check name with no open one. A platform
    that cannot answer is logged and skipped: a flake issue never blocks a merge."""
    try:
        bodies = [body for _, body in ports.inbox.open_issue_bodies()]
    except (ValueError, OSError) as exc:
        log(f"agentic: land: {task_id}: flake issues not filed: {exc}")
        return []
    opened: list[str] = []
    for name in dict.fromkeys(checks):
        marker = landing_rules.flake_marker(name)
        if any(marker in body for body in bodies):
            continue
        body = (
            f"{marker}\nThe check `{name}` failed and then passed on a rerun of the same head "
            f"`{head}` of {task_id}: it is flaky. Find what makes it depend on anything but "
            f"the code under test."
        )
        try:
            opened.append(ports.tracker.open_issue(f"Flaky check: {name}", body, label=FLAKE_LABEL))
        except (ValueError, OSError) as exc:
            log(f"agentic: land: {task_id}: flake issue for {name} not filed: {exc}")
            continue
        bodies.append(body)
    return opened


def _records(raw: object) -> dict[str, list[dict[str, object]]]:
    """The task's `flakes` records grouped by check, in the order they were recorded; an
    absent or malformed value yields none (a document written before ADR-0066 has none)."""
    value = raw.get("flakes") if isinstance(raw, dict) else None
    grouped: dict[str, list[dict[str, object]]] = {}
    for entry in value if isinstance(value, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("check"), str):
            grouped.setdefault(str(entry["check"]), []).append(entry)
    return grouped


def _ids(records: list[dict[str, object]]) -> list[str]:
    found: list[str] = []
    for entry in records:
        tests = entry.get("tests")
        found += [t for t in tests if isinstance(t, str)] if isinstance(tests, list) else []
    return list(dict.fromkeys(found))


def _lines(task_id: str, records: list[dict[str, object]]) -> str:
    """One line per record: the task, the head the gate observed it on (not the merge
    head), the gate, and the test ids when the failure named any."""
    lines = []
    for entry in records:
        line = f"- {task_id}: head `{entry.get('head')}`, gate `{entry.get('gate')}`"
        if ids := _ids([entry]):
            line += ", tests: " + ", ".join(f"`{t}`" for t in ids)
        lines.append(line)
    return "\n".join(lines)


def file_task_flakes(
    ports: Ports, task_id: str, raw: object, log: Callable[[str], None]
) -> list[str]:
    """File or update the flake issue of each check the task's `flakes` record names
    (ADR-0066), sharing `file_flakes`'s marker so the CI path and the gates keep one issue
    per check. A check with no open issue gets one; a check with one gets a single comment
    per (issue, task), and only when the task brings a test id the issue body does not name
    (`flake_comments` remembers which, so a retried merge does not repeat it). Returns the
    URLs opened. A platform that cannot answer is logged and skipped: a flake never blocks a
    merge."""
    grouped = _records(raw)
    if not grouped:
        return []
    try:
        issues = ports.inbox.open_issue_bodies()
    except (ValueError, OSError) as exc:
        log(f"agentic: land: {task_id}: flake issues not filed: {exc}")
        return []
    commented = raw.get("flake_comments") if isinstance(raw, dict) else None
    commented = [r for r in commented if isinstance(r, str)] if isinstance(commented, list) else []
    opened: list[str] = []
    for name, records in grouped.items():
        marker = landing_rules.flake_marker(name)
        text = _lines(task_id, records)
        try:
            existing = next(((ref, body) for ref, body in issues if marker in body), None)
            if existing is None:
                body = f"{marker}\nThe check `{name}` failed and then passed on a rerun:\n{text}"
                opened.append(
                    ports.tracker.open_issue(f"Flaky check: {name}", body, label=FLAKE_LABEL)
                )
                issues = [*issues, ("", body)]
                continue
            reference, issue_body = existing
            if reference in commented or all(t in issue_body for t in _ids(records)):
                continue
            ports.tracker.comment_on_issue(
                reference, f"The check `{name}` was flaky again:\n{text}"
            )
            commented = [*commented, reference]
            ports.documents.record_closure(task_id, {"flake_comments": commented})
        except (ValueError, OSError) as exc:
            log(f"agentic: land: {task_id}: flake issue for {name} not filed: {exc}")
    return opened


def rerun_checks_on(raw: object, head: str) -> list[str]:
    """The check names the `rerun` entry on `head` recorded, none when there is none."""
    entries = raw.get("landing") if isinstance(raw, dict) else None
    names: list[str] = []
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and entry.get("step") == "rerun" and entry.get("head") == head:
            value = entry.get("checks")
            names += [n for n in value if isinstance(n, str)] if isinstance(value, list) else []
    return names
