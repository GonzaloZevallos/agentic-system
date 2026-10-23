"""The Tech Writer's text findings (README.md, 4): how a review's findings become rows of
the findings table, which rows are still open, and the corrected texts in force.

A text finding is a row of the same table as the Tech Lead's, told apart by its
`location`, which is an identifier: `copy:<n>.<i>:<location>`, `n` the run of `Tech
Writer: review` that raised it and `i` the finding's position in that pass. The identifier
makes every finding its own row, so settling one never settles another on the same line,
and a defect found again after it was settled is a new row. Only `copy_location` builds
it and only `copy_location_parts` takes it apart; no other module touches the prefix.

A copy row is never `blocking` (the schema has no such value), so no Tech Lead approval
ever covers one: only `copy_records` settles them, naming the exact identifier."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .schemas import CopyReview
from .task import FindingRecord, TaskState

_COPY = re.compile(r"^copy:(\d+)\.(\d+):(.+)$", re.S)

# The one row the system writes itself: the product has no writing criteria, so the role
# says so and proposes a starting document instead of inventing rules silently.
WRITING_FILE = "WRITING_STANDARDS.md"
NO_WRITING_LOCATION = f"copy:{WRITING_FILE}"
NO_WRITING_CLAIM = (
    f"The product has no `{WRITING_FILE}`: its texts are written and reviewed with no criteria"
)
NO_WRITING_NOTE = (
    f"No `{WRITING_FILE}`: the product has no writing criteria. These texts follow the "
    "request, the definition and the glossary only; the review proposes a starting document."
)


def copy_location(n: int, i: int, location: str) -> str:
    """The identifier of the `i`-th finding of review run `n` at `location`."""
    return f"copy:{n}.{i}:{location}"


def copy_location_parts(identifier: str) -> tuple[int, int, str] | None:
    """`(n, i, location)` of a copy row's identifier, `None` for any other location. The
    no-criteria row has no pass: it reads `(0, 0, "WRITING_STANDARDS.md")`."""
    if identifier == NO_WRITING_LOCATION:
        return 0, 0, WRITING_FILE
    if (found := _COPY.match(identifier)) is None:
        return None
    return int(found.group(1)), int(found.group(2)), found.group(3)


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def _open_copy_rows(state: TaskState) -> list[FindingRecord]:
    """The copy rows still open, one per identifier and claim: a copy row closes only
    because a review named its identifier in `settled_findings`."""
    settled = {_norm(location) for location in state.get("settled_findings", [])}
    rows: dict[tuple[str, str], FindingRecord] = {}
    for record in state.get("findings", []):
        if copy_location_parts(record["location"]) is None:
            continue
        key = (_norm(record["location"]), _norm(record["claim"]))
        rows.setdefault(key, record)
    return [
        row
        for (location, _), row in rows.items()
        if row["status"] == "open" and location not in settled
    ]


def _real_key(row: FindingRecord) -> tuple[str, str]:
    parts = copy_location_parts(row["location"])
    return _norm(parts[2] if parts else row["location"]), _norm(row["claim"])


def copy_records(
    state: TaskState,
    n: int,
    review: CopyReview,
    owners_by_path: Mapping[str, Sequence[str]],
    fallback_unit: str,
    *,
    standards_missing: bool = False,
) -> tuple[list[FindingRecord], list[str]]:
    """What review run `n` adds to the table: `(records, settled)`.

    One record per finding, `version = n`, its `subtask` the owners of the file joined by
    `", "` (`owners_by_path` maps a path, the part of the finding's location before the
    first `:`, to the approved subtasks whose diff touches it) or `fallback_unit` when
    none does. A finding whose real location and claim match a row still open is dropped:
    a pass never duplicates a defect that is still there. `settled` keeps only the
    identifiers of open copy rows, so a Tech Lead location closes nothing.

    The product without writing criteria (`standards_missing`) adds the fixed row once:
    `important`, scope `outside`, its `proposed` the starting document the review gave in
    `proposed_standards`. A product that has the file never gets the row."""
    open_rows = _open_copy_rows(state)
    by_identifier = {_norm(row["location"]): row["location"] for row in open_rows}
    settled = list(
        dict.fromkeys(
            by_identifier[key] for item in review.settled if (key := _norm(item)) in by_identifier
        )
    )
    closing = {_norm(item) for item in settled}
    still_open = [row for row in open_rows if _norm(row["location"]) not in closing]
    seen = {_real_key(row) for row in still_open}
    records: list[FindingRecord] = []
    for position, item in enumerate(review.findings, start=1):
        found = (_norm(item.location), _norm(item.claim))
        if found in seen:
            continue
        seen.add(found)
        owners = owners_by_path.get(item.location.partition(":")[0], ())
        records.append(
            {
                "subtask": ", ".join(dict.fromkeys(owners)) or fallback_unit,
                "version": n,
                "location": copy_location(n, position, item.location),
                "claim": item.claim,
                "severity": item.severity,
                "scope": item.scope,
                "category": item.category,
                "status": "dismissed"
                if item.severity == "observation" and not item.evidence.strip()
                else "open",
                "proposed": item.proposed,
            }
        )
    if standards_missing and not any(row["location"] == NO_WRITING_LOCATION for row in still_open):
        records.append(
            {
                "subtask": fallback_unit,
                "version": n,
                "location": NO_WRITING_LOCATION,
                "claim": NO_WRITING_CLAIM,
                "severity": "important",
                "scope": "outside",
                "category": "copy-criteria-written",
                "status": "open",
                "proposed": review.proposed_standards,
            }
        )
    return records, settled


def current_texts(state: TaskState) -> list[tuple[str, str]]:
    """The corrected texts in force: `(real location, proposed)` of every settled copy
    row that proposed one, the latest per location. Next to the `Tech Writer` section,
    which the system never rewrites, they are what the review compares the diff with."""
    settled = {_norm(location) for location in state.get("settled_findings", [])}
    latest: dict[str, str] = {}
    for record in state.get("findings", []):
        parts = copy_location_parts(record["location"])
        if (
            parts is None
            or record["location"] == NO_WRITING_LOCATION
            or _norm(record["location"]) not in settled
            or not record.get("proposed", "").strip()
        ):
            continue
        latest.pop(parts[2], None)
        latest[parts[2]] = record["proposed"]
    return list(latest.items())
