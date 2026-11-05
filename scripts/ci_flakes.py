"""The CI side of the flaky-test work (GH-464, ADR-0085): from the two junit files of the pytest
step, print the `agentic-flakes:` block with the tests that failed on the first run and passed
on the retry.

Usage: `python scripts/ci_flakes.py --check <name> <first.xml> <second.xml>`. Both files are
pytest's `xunit1` junit (`-o junit_family=xunit1`), at any nesting of `testsuite` elements:
that family keeps the `file` attribute, which rebuilds the node id the gate reads from pytest's
`FAILED <nodeid>` lines.

A flake is a testcase with a `<failure>` child in the first file whose id appears in the second
file with no `failure`, `error` or `skipped` child. An `<error>` in the first file is never a
candidate (the gate's `FAILED` pattern never sees `ERROR` lines). Missing evidence is never a
flake: an id absent from the second file, a missing, unreadable or malformed file in either
position, or a testcase whose id cannot be rebuilt prints no block.

The script prints one line at most and always exits 0: it only reports, the step's exit is the
second pytest run's. The prefix, the version and the id cleaning come from
`agentic.core.process.domain.ci`.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Sequence
from pathlib import Path

from agentic.core.process.domain.ci import (
    FLAKE_BLOCK_PREFIX,
    FLAKE_BLOCK_VERSION,
    clean_flake_ids,
)

BAD_CHILDREN = frozenset({"failure", "error", "skipped"})


def node_id(case: ET.Element) -> str | None:
    """The pytest node id of one `xunit1` testcase, `None` when it cannot be rebuilt: the `file`
    attribute, then `::` and each class part (the dotted parts of `classname` after the module
    path derived from `file`), then `::` and `name`."""
    file = case.get("file")
    name = case.get("name")
    classname = case.get("classname", "")
    if not file or name is None:
        return None
    module = file.removesuffix(".py").replace("/", ".")
    if classname == module:
        classes: list[str] = []
    elif classname.startswith(module + "."):
        classes = classname[len(module) + 1 :].split(".")
    else:
        return None
    return "::".join([file, *classes, name])


def _cases(path: Path) -> Iterator[tuple[str, set[str]]]:
    """`(node id, tags of its direct children)` of every testcase in the file at `path`. Raises
    on a file that is missing or not well-formed XML."""
    for case in ET.parse(path).getroot().iter("testcase"):
        ident = node_id(case)
        if ident is not None:
            yield ident, {child.tag for child in case}


def flakes(first: Path, second: Path) -> list[str]:
    """The ids that failed in `first` and are present and clean in `second`, in `first`'s order."""
    retried: dict[str, set[str]] = {}
    for ident, tags in _cases(second):
        retried.setdefault(ident, set()).update(tags)
    return [
        ident
        for ident, tags in _cases(first)
        if "failure" in tags and ident in retried and not retried[ident] & BAD_CHILDREN
    ]


def main(argv: Sequence[str]) -> int:
    """Print the block when there is a flake. Exit 0 whatever happens."""
    try:
        parser = argparse.ArgumentParser(prog="ci_flakes.py", exit_on_error=False)
        parser.add_argument("--check", required=True)
        parser.add_argument("first", type=Path)
        parser.add_argument("second", type=Path)
        args = parser.parse_args(argv)
        tests = clean_flake_ids(flakes(args.first, args.second))
        if tests:
            block = {"v": FLAKE_BLOCK_VERSION, "check": args.check, "tests": tests}
            print(FLAKE_BLOCK_PREFIX + json.dumps(block))
    except (Exception, SystemExit):
        # A report that cannot be built is no report: the step's verdict is pytest's.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
