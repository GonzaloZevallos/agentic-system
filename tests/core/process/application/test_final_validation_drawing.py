"""The final leads' drawing pass (GH-766 part 5.2): due screens, the `drawing_checks` record and
the return for departures. On the in-memory ports; nothing is published."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agentic.core.process.application import final_validation as node
from agentic.core.process.application.ports import GitError
from agentic.core.process.domain import drawing, rules
from agentic.core.process.domain.design import Capture, Design
from agentic.core.process.domain.task import TaskState, apply
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryWorkspace, ScriptedRunner, design_findings, memory_ports
from tests.fakes import final_validation as final_verdict

pytestmark = pytest.mark.anyio

FULL = "a" * 40
IMAGE = "docs/design/home.png"
OTHER = "docs/design/list.png"
CAPTURES = (
    Capture(image=IMAGE, screen="Home", paths=("web/home*",)),
    Capture(image=OTHER, screen="List", paths=("web/list*",)),
)


def design(compare: str | None = "uv run compare") -> Design:
    return Design(
        prototype="docs/design",
        entry="home.png",
        sample_app="run app",
        compare=compare,
        captures=CAPTURES,
    )


def state(reviewed: str = FULL, **over: Any) -> TaskState:
    base: TaskState = {
        "id": "GH-12",
        "title": "Home",
        "description": "d",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nOk."},
        "subtasks": {
            "s0": {
                "id": "s0",
                "title": "s0",
                "brief": "b",
                "seams": ["s"],
                "depends_on": [],
                "status": "approved",
                "reviewed_head": reviewed,
            }
        },
    }
    return apply(base, over) if over else base


def check(verdict: str, ref: str = IMAGE, element: str | None = "Save") -> dict[str, Any]:
    named = verdict != "unverified"
    return {
        "subject": "screen",
        "ref": ref,
        "verdict": verdict,
        "drawing": ref if named else None,
        "element": element if named else None,
    }


def departure(element: str = "Save", ref: str = IMAGE) -> dict[str, Any]:
    return {
        "drawing": ref,
        "element": element,
        "shows": "blue",
        "would_ship": "green",
        "recommendation": "keep the drawing",
        "reason": "approved",
        "raised_by": "ui_lead_final",
    }


def lead(checks: list[dict[str, Any]], departures: list[dict[str, Any]] | None = None):
    raw = design_findings("approved")
    data = {**raw.structured_output, "drawing_checks": checks, "departures": departures or []}
    return replace(raw, structured_output=data)


class Workspace(MemoryWorkspace):
    def __init__(self) -> None:
        super().__init__()
        self.touched = ("web/home.tsx",)

    def full_head(self, path: Path) -> str:
        return FULL


async def run(start: TaskState, runner: ScriptedRunner, tmp_path: Path, d: Design | None, **kw):
    ports = memory_ports(runner, workspace=kw.pop("workspace", Workspace()))
    ports.stack.declared_design = d  # type: ignore[attr-defined]
    if kw:
        ports.sample_app.result = kw["app"]  # type: ignore[attr-defined]
    patch = await node.final_validation(
        start,
        Configuration(product_dir=tmp_path),
        ports,
        checkout=ports.workspace.task_checkout(start["id"]),
    )
    return patch, ports


def runner(ux: Any, ui: Any, *, pm: bool = True) -> ScriptedRunner:
    return ScriptedRunner(
        ux_lead_final=[ux], ui_lead_final=[ui], pm_final=[final_verdict("approved")] if pm else []
    )


async def test_matches_everywhere_records_a_clean_record_and_runs_pm(tmp_path):
    script = runner(lead([]), lead([check("matches")]))
    patch, ports = await run(state(), script, tmp_path, design())
    assert patch["drawing_checks"] == {
        "head": FULL,
        "rows": [{"subject": "screen", "ref": IMAGE, "verdict": "matches"}],
        "open": 0,
    }
    assert "pm_final" in script.slugs
    assert ports.workspace.touched_reads == [(ports.workspace.touched_reads[0][0], FULL)]
    assert (
        IMAGE in script.prompts("ui_lead_final")[0]
        and OTHER not in script.prompts("ui_lead_final")[0]
    )
    assert "uv run compare" in script.prompts("ux_lead_final")[0]


async def test_a_deleted_path_and_a_renamed_one_keep_their_screens_due(tmp_path):
    ws = Workspace()
    ws.touched = ("web/home_old.tsx", "web/list.tsx")  # a deletion and the new end of a rename
    script = runner(lead([]), lead([check("matches"), check("matches", OTHER)]))
    patch, _ = await run(state(), script, tmp_path, design(), workspace=ws)
    assert [r["ref"] for r in patch["drawing_checks"]["rows"]] == [IMAGE, OTHER]


async def test_a_lead_that_skips_a_due_screen_is_refused_in_the_turn(tmp_path):
    script = ScriptedRunner(
        ux_lead_final=[lead([])],
        ui_lead_final=[lead([]), lead([check("matches")])],
        pm_final=[final_verdict("approved")],
    )
    patch, _ = await run(state(), script, tmp_path, design())
    assert script.slugs.count("ui_lead_final") == 2
    assert patch["drawing_checks"]["open"] == 0


async def test_no_compare_writes_unverified_never_matches_and_the_merge_refuses(tmp_path):
    script = runner(lead([]), lead([check("matches")]))
    patch, _ = await run(state(), script, tmp_path, design(compare=None))
    rows = patch["drawing_checks"]["rows"]
    assert [r["verdict"] for r in rows] == ["unverified"] and patch["drawing_checks"]["open"] == 1
    assert node.NO_COMPARE_NOTE in patch["sections"]["UI Lead: final comparisons"]
    checked, head, open_ = rules.drawing_merge_facts(apply(state(), patch))
    assert (checked, head, open_) == (True, FULL, 1)


async def test_a_sample_app_that_did_not_start_is_unverified_with_its_note(tmp_path):
    script = runner(lead([]), lead([check("matches")]))
    patch, _ = await run(state(), script, tmp_path, design(), app="boom")
    assert patch["drawing_checks"]["rows"][0]["verdict"] == "unverified"
    assert node.NO_APP_NOTE in patch["sections"]["UI Lead: final comparisons"]


async def test_a_short_reviewed_head_is_recorded_full(tmp_path):
    script = runner(lead([]), lead([check("matches")]))
    patch, _ = await run(state(reviewed=FULL[:10]), script, tmp_path, design())
    assert patch["drawing_checks"]["head"] == FULL
    assert rules.full_commit_id(patch["drawing_checks"]["head"])


async def test_an_unresolvable_head_writes_no_record(tmp_path):
    class Other(Workspace):
        def full_head(self, path: Path) -> str:
            return "b" * 40

    script = runner(lead([]), lead([check("matches")]))
    patch, _ = await run(state(reviewed=FULL[:10]), script, tmp_path, design(), workspace=Other())
    assert "drawing_checks" not in patch
    assert node.HEAD_UNRESOLVED_NOTE in patch["sections"]["UI Lead: final comparisons"]


async def test_a_git_error_runs_no_lead_and_writes_no_record(tmp_path):
    ws = Workspace()
    ws.touched_error = "boom"
    script = runner(lead([]), lead([check("matches")]))
    with pytest.raises(GitError):
        await run(state(), script, tmp_path, design(), workspace=ws)
    assert script.slugs == []


async def test_an_unanswered_departure_returns_without_a_number_and_runs_no_pm(tmp_path):
    script = runner(lead([]), lead([check("departs")], [departure()]), pm=False)
    patch, _ = await run(state(), script, tmp_path, design())
    claims = [f["claim"] for f in patch["findings"]]
    assert len(claims) == 1 and IMAGE in claims[0] and "Save" in claims[0]
    assert "owner must answer" in claims[0] and not any(
        ch.isdigit() for ch in claims[0].replace(IMAGE, "").replace("GH", "")
    )
    assert "pm_final" not in script.slugs and "drawing_raised" not in patch
    assert patch["subtasks"]["s0"]["status"] == "returned"
    assert patch["drawing_checks"]["open"] == 1


def _row(answer: str | None, fix_passes: int, element: str = "Save") -> dict[str, Any]:
    key = drawing.departure_key(IMAGE, element)
    return {
        "number": 3,
        "key": key,
        "drawing": IMAGE,
        "element": element,
        "shows": "s",
        "would_ship": "w",
        "recommendation": "r",
        "reason": "r",
        "raised_by": "ui_lead_final",
        "raised_at": "T",
        "fix_passes": fix_passes,
        "answer": None if answer is None else {"verdict": answer, "by": "comment", "at": "T"},
    }


async def test_a_rejected_departure_returns_citing_its_number_and_counts_the_fix_pass(tmp_path):
    held = state(drawing_departures=[_row("reject", 0)])
    # a different capitalisation still resolves to the same row
    script = runner(
        lead([]), lead([check("departs", element="SAVE")], [departure("SAVE")]), pm=False
    )
    patch, _ = await run(held, script, tmp_path, design())
    assert (
        "Drawing 3" in patch["findings"][0]["claim"] and "reject" in patch["findings"][0]["claim"]
    )
    assert [r["fix_passes"] for r in patch["drawing_departures"]] == [1]
    assert patch["changes_requested"]
    again = apply(held, patch)
    script = runner(lead([]), lead([check("departs")], [departure()]), pm=False)
    second, _ = await run(again, script, tmp_path, design())
    assert [r["fix_passes"] for r in second.get("drawing_departures", [])] in ([], [1])
    assert "Drawing 3" in second["findings"][0]["claim"]


async def test_an_accepted_departure_is_settled_and_pm_runs(tmp_path):
    held = state(drawing_departures=[_row("accept", 0)])
    script = runner(lead([]), lead([check("departs")], []))
    patch, _ = await run(held, script, tmp_path, design())
    assert patch["drawing_checks"]["rows"][0]["settled"] is True
    assert patch["drawing_checks"]["open"] == 0 and "pm_final" in script.slugs


async def test_without_a_design_no_new_key_and_the_same_rendered_prompts(tmp_path):
    script = runner(lead([]), lead([]))
    patch, _ = await run(state(), script, tmp_path, None)
    assert "drawing_checks" not in patch and "drawing_departures" not in patch
    assert not any("drawing" in p.lower() for p in script.prompts("ux_lead_final"))
