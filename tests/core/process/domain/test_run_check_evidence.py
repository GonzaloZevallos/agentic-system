"""The system's line for one call of `run_check` on a verification (ADR-0065 addendum,
GH-451): what the reviewer reads in the Developer's section instead of the Developer's word."""

from agentic.core.process.domain.run_check import (
    Outcome,
    VerificationCall,
    evidence_line,
    evidence_markdown,
)


def call(**over) -> VerificationCall:
    fields = {
        "name": "compare",
        "outcome": Outcome.PASSED,
        "duration_s": 712.46,
        "load_before": 3.5,
        "load_after": 9.25,
        "cores": 16,
        "tail": "offenders: 0",
    }
    return VerificationCall(**(fields | over))


def test_the_line_carries_name_outcome_duration_load_with_cores_and_the_log_tail():
    text = evidence_line(call())
    assert "`compare`" in text and "passed" in text and "712.5 s" in text
    assert "3.50" in text and "9.25" in text and "16 cores" in text
    assert text.endswith("```text\noffenders: 0\n```")


def test_every_outcome_is_named_as_the_system_spells_it():
    for outcome in Outcome:
        assert f"`{outcome.value}`" in evidence_line(call(outcome=outcome))


def test_a_load_that_was_not_read_says_so_instead_of_inventing_a_figure():
    text = evidence_line(call(load_before=None, load_after=None, cores=None))
    assert "load not read" in text and "cores" not in text


def test_a_call_that_ran_nothing_has_no_empty_log_block():
    text = evidence_line(call(outcome=Outcome.NOT_DECLARED, duration_s=0.0, tail=""))
    assert "`not_declared`" in text and "```" not in text


def test_no_calls_render_nothing_so_the_section_is_todays():
    assert evidence_markdown([]) is None


def test_the_block_lists_every_call_in_order_under_one_heading():
    text = evidence_markdown([call(name="a"), call(name="b", outcome=Outcome.FAILED)])
    assert text is not None
    assert text.startswith("### Verifications")
    assert text.index("`a`") < text.index("`b`")


def test_the_review_prompt_reads_the_systems_line_and_never_blocks_on_unverified():
    from agentic.core.process.domain.roles import TECH_LEAD_REVIEWER
    from tests.fakes import memory_ports

    raw, _ = memory_ports().prompts.raw(TECH_LEAD_REVIEWER)
    assert "### Verifications" in raw
    assert "never the Developer's\n`summary`" in raw
    assert "`unverified` or `not_declared` outcome is an `observation`" in raw
