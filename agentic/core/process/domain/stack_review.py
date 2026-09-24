"""Pure text algebra for a stack review's issue (ADR-0032): the idempotence marker,
the assembled issue body and its cap, and the open-proposal lookup over issue bodies
the adapter already fetched. No port, no filesystem: every fact this module needs is
a plain value the application module or `cli.py` hands in.
"""

from __future__ import annotations

from collections.abc import Sequence

# Below GitHub's own 65_536-character cap, with room for the marker and the headings
# this module wraps a proposal's pieces in.
ISSUE_BODY_MAX_CHARS = 60_000

# The idempotence marker (ADR-0032): an HTML comment, invisible once the issue
# renders, matched as a plain substring so a poller that runs many times a year opens
# one proposal, not one per run. `MARKER_PREFIX` alone is what `open_proposal` looks
# for -- a stale anchor still suppresses, because two open proposals for one product is
# never a state anybody wants.
MARKER_PREFIX = "<!-- agentic:stack-review"


def marker(anchor: str) -> str:
    """The marker for a review anchored at `anchor` (the commit that last touched the
    product's stack declaration, or a stand-in like `"none"` on a greenfield product)."""
    return f"{MARKER_PREFIX} anchor={anchor} -->"


def body(
    anchor: str, signals: str, verdict: str, stack_diff: str, standards: str, adr_draft: str
) -> str:
    """The issue body: the marker, the signals that triggered the review, then the
    verdict and whichever of the proposal's pieces are non-empty, each under its own
    heading. A piece left empty (the needs-info exit has no diff, no standards, no ADR
    draft) is skipped instead of written as an empty heading."""
    parts = [marker(anchor), f"## Signals\n\n{signals.strip()}", f"## Verdict\n\n{verdict.strip()}"]
    if stack_diff.strip():
        parts.append(f"## Stack diff\n\n{stack_diff.strip()}")
    if standards.strip():
        parts.append(f"## Standards\n\n{standards.strip()}")
    if adr_draft.strip():
        parts.append(f"## ADR draft\n\n{adr_draft.strip()}")
    return "\n\n".join(parts)


def body_refusal(text: str) -> str | None:
    """Why `text` cannot become an issue body, or `None` when it fits under
    `ISSUE_BODY_MAX_CHARS`: the total count, and each `## `-headed piece's own size, so
    a caller knows which piece to shrink without a failed `gh` call to read it off of."""
    count = len(text)
    if count <= ISSUE_BODY_MAX_CHARS:
        return None
    sizes = []
    for chunk in text.split("\n\n## ")[1:]:
        heading, _, rest = chunk.partition("\n")
        sizes.append(f"{heading.strip()}: {len(rest)} chars")
    listed = "; ".join(sizes) or "no labelled pieces"
    return f"issue body is {count} characters, over the {ISSUE_BODY_MAX_CHARS} cap ({listed})"


def open_proposal(issues: Sequence[tuple[str, str]]) -> str | None:
    """The URL of an issue this path already opened and is still open, or `None`.
    `issues` is every open issue's `(url, body)` as the adapter already fetched it
    (`Inbox.open_issues`); the marker is matched as a plain substring over that text,
    never through GitHub's own eventually consistent `--search` index (ADR-0032)."""
    for url, issue_body in issues:
        if MARKER_PREFIX in issue_body:
            return url
    return None
