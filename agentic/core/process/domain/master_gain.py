"""What master gained since a recorded check pass, and whether that gain is inert.

Only `glob_refusal` is here yet: the loader of the stack key `merge_inert` calls it, and the
rest of the module (the matcher and the verdict) lands with the `master-gain` subtask, whose
file replaces this one on integration.
"""

from __future__ import annotations


def glob_refusal(glob: str) -> str | None:
    """Why a `merge_inert` glob may not be declared, or None. A glob that would whitelist
    everything or reach outside the repository is refused: a bare `*` or `**`, one that
    matches only by extension (`*.md`), an absolute path, a `..` segment."""
    if not glob.strip():
        return "is blank"
    if glob.startswith("/"):
        return "is an absolute path"
    segments = glob.split("/")
    if ".." in segments:
        return "has a `..` segment"
    if all(segment in ("*", "**") for segment in segments):
        return "matches every path"
    if len(segments) == 1 and segments[0].startswith("*"):
        return "matches by name alone, in any directory"
    return None
