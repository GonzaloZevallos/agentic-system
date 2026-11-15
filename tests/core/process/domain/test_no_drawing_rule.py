"""The prompt scan: every prompt carries the drawing rule or says, checked, why not."""

import re
from pathlib import Path

import pytest

import agentic.core.process.domain as domain_pkg
from agentic.core.process.domain.drawing import NO_DRAWING_RULE

PLACEHOLDER = "{{drawing_rule}}"
PROMPTS = Path(domain_pkg.__file__).parent / "prompts"
NAMES = sorted(p.relative_to(PROMPTS).with_suffix("").as_posix() for p in PROMPTS.rglob("*.md"))
GRAMMAR = re.compile(
    r"pending GH-766 part [345]"
    r"|covered by (?P<target>[a-z_/]+)"
    r"|plan carries the rule"
    r"|no visible change"
    r"|never decides what a person sees"
)
NEVER_DECIDES = {
    "pjm",
    "plan_sizer",
    "landing_diagnoser",
    "reflector",
    "qa_performance",
    "tech_lead_carve_out",
}


def _carries(name: str) -> int:
    return (PROMPTS / f"{name}.md").read_text().count(PLACEHOLDER)


def _reason(name: str) -> str:
    return NO_DRAWING_RULE[name]


def test_the_scan_sees_the_prompts_and_spec():
    assert "tech_lead" in NAMES and "spec/plan" in NAMES


@pytest.mark.parametrize("name", NAMES)
def test_a_prompt_carries_the_rule_once_xor_is_exempt(name):
    assert (_carries(name) == 1) != (name in NO_DRAWING_RULE), name
    assert _carries(name) <= 1


def test_every_exemption_names_an_existing_prompt():
    assert set(NO_DRAWING_RULE) <= set(NAMES)


def test_every_reason_follows_the_grammar():
    for name, reason in NO_DRAWING_RULE.items():
        assert GRAMMAR.fullmatch(reason), (name, reason)


def test_a_covered_by_target_carries_the_rule_or_is_pending():
    for name, reason in NO_DRAWING_RULE.items():
        match = GRAMMAR.fullmatch(reason)
        assert match
        target = match.group("target")
        if target is None:
            continue
        assert target in NAMES, (name, target)
        assert target not in NO_DRAWING_RULE or _reason(target).startswith("pending"), (
            name,
            target,
        )


def test_no_visible_change_is_exactly_the_platform_and_stack_names():
    suffixed = {n for n in NAMES if n.endswith(("_platform", "_stack"))}
    marked = {n for n, r in NO_DRAWING_RULE.items() if r == "no visible change"}
    assert marked == suffixed


def test_never_decides_is_the_fixed_list():
    never = {n for n, r in NO_DRAWING_RULE.items() if r == "never decides what a person sees"}
    assert never == NEVER_DECIDES


def test_the_scan_rejects_a_bad_registry():
    assert not GRAMMAR.fullmatch("pending GH-766 part 6")
    assert not GRAMMAR.fullmatch("because I said so")
