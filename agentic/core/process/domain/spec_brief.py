"""The approved Spec slice as a role reads it (GH-594, part 3a): the one writer of
`SPEC_FENCE` in Process, and the data guard that goes with it.

The slice reaches a role only through `run_role.context_for`, as the fenced section this
module builds: never through `render`, the system prompt or its hash (ADR-0096). What the
system prompt may carry is a constant block per role group, named in `SPEC_BLOCKS`, which
ships empty until a later part fills it.

Pure: it reads nothing, neither the spec file nor the front matter, only the `SpecContract`
a caller hands it. `data_refusal` duplicates, on purpose, the data rules the Knowledge
validator applies to a spec (`core.knowledge.domain.spec`): Process may not import Knowledge,
and the data returns from the task document on a resume, so it is checked again before a
prompt is built.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

from .spec_delivery import SPEC_FENCE, SPEC_MAX_BYTES, SpecContract

HEADING = "## Approved Spec slice"

# Role slug -> name of the constant block its system prompt carries. Closed and empty here.
SPEC_BLOCKS: dict[str, str] = {}

DataRefusal = Literal["line_break", "fence", "template", "comment", "oversize"]


def block_for(slug: str) -> str | None:
    """The name of the block `slug` carries when a spec exists, `None` when the table has none."""
    return SPEC_BLOCKS.get(slug)


def fenced_section(spec: SpecContract) -> str:
    """The slice between two `SPEC_FENCE` lines under `HEADING`: the slice id, the criteria
    numbered `C1…Cn` in contract order, each screen with its copy lines, and the risks.
    Total: it checks nothing; `data_refusal` is the guard."""
    lines = [HEADING, "", SPEC_FENCE, f"Slice: {spec['slice']}", "", "Criteria:"]
    lines += [f"C{number}. {text}" for number, text in enumerate(spec["criteria"], start=1)]
    lines += ["", "Screens:"]
    for screen in spec["screens"]:
        lines.append(screen["name"])
        lines += [f"- {line}" for line in screen["copy"]]
    lines += ["", "Risks:"]
    lines += [f"- {risk}" for risk in spec["risks"]]
    lines.append(SPEC_FENCE)
    return "\n".join(lines)


def _fields(spec: SpecContract) -> Iterator[str]:
    """Every piece of text `fenced_section` renders from the contract."""
    yield spec["slice"]
    yield from spec["criteria"]
    for screen in spec["screens"]:
        yield screen["name"]
        yield from screen["copy"]
    yield from spec["risks"]


def data_refusal(spec: SpecContract) -> DataRefusal | None:
    """Why `spec` may not be fenced into a prompt, or `None`. The reason is one of a closed
    set and never carries field text. A field with a line break, a field that is the fence
    on a line of its own, `{{`, `<!--`, or a section over `SPEC_MAX_BYTES` of UTF-8."""
    for field in _fields(spec):
        if "\n" in field or "\r" in field:
            return "line_break"
        if field.strip() == SPEC_FENCE:
            return "fence"
        if "{{" in field:
            return "template"
        if "<!--" in field:
            return "comment"
    # `surrogatepass` so text the Knowledge validator would call unencodable is still measured.
    size = len(fenced_section(spec).encode("utf-8", errors="surrogatepass"))
    return "oversize" if size > SPEC_MAX_BYTES else None
