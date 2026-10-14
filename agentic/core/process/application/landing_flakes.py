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


def rerun_checks_on(raw: object, head: str) -> list[str]:
    """The check names the `rerun` entry on `head` recorded, none when there is none."""
    entries = raw.get("landing") if isinstance(raw, dict) else None
    names: list[str] = []
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and entry.get("step") == "rerun" and entry.get("head") == head:
            value = entry.get("checks")
            names += [n for n in value if isinstance(n, str)] if isinstance(value, list) else []
    return names
