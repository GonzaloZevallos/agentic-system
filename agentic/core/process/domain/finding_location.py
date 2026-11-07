"""Where a finding points and what family its category belongs to (GH-473): the two inputs of
the shadow report's match (`measurement/domain/shadow_report.build`), and nothing else.

A pure leaf: the standard library only. It reads the structured `location` and `category` of a
finding, never its claim or evidence.
"""

from __future__ import annotations

import re

KNOWN_EXTENSIONS = (
    ".py",
    ".md",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".txt",
    ".sh",
    ".cfg",
    ".ini",
    ".lock",
    ".mmd",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".mjs",
    ".cjs",
    ".sql",
    ".rst",
    ".go",
    ".rs",
)

_LINE_ANCHOR = re.compile(r"#L\d+(-L\d+)?$")


def finding_file(location: str) -> str | None:
    """The repo-relative file a finding's `location` points at, or `None`.

    The text is stripped, cut at the first `::` (a pytest id), cut at the first `:` (a line
    suffix), freed of a trailing `#L<n>[-L<n>]` and of a leading `./`. It is `None` when
    nothing is left, when it holds whitespace, starts with `/` or `~`, or has `..` as a segment
    (a URL never gets here: its `:` cut it to a bare scheme); otherwise it is the path only when
    it contains `/` or ends in one of `KNOWN_EXTENSIONS`. So `full_checks:<check>`,
    `copy:<n>.<i>:<loc>`, plan or brief references and prose give `None`, and the shadow
    finding there is `unlocated`.

    The extension list is closed on purpose: anything it does not know falls to `unlocated`,
    the safe direction. A one-token prose with a slash, like `input/output`, localizes: the
    residual is pinned by a test. Renamed files are not followed: a file renamed between the
    two reviews is two files here.
    """
    candidate = location.strip().split("::", 1)[0].split(":", 1)[0]
    candidate = _LINE_ANCHOR.sub("", candidate).removeprefix("./")
    if (
        not candidate
        or re.search(r"\s", candidate)
        or candidate.startswith(("/", "~"))
        or ".." in candidate.split("/")
    ):
        return None
    if "/" in candidate or candidate.endswith(KNOWN_EXTENSIONS):
        return candidate
    return None


def category_family(category: str) -> str:
    """The text of a finding category before its first `-`. Two reviewers rarely pick the
    same member of a family for one defect, so the match is by family."""
    return category.partition("-")[0]
