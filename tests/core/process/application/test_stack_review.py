"""The stack review's own use case (ADR-0032), against in-memory ports: the three
exits — needs-info, a proposal, and a proposal whose standards piece overflows the
issue cap — each over `ScriptedRunner`, with no task, no branch, no issue behind it."""

import pytest

from agentic.core.process.application.stack_review import stack_review
from agentic.core.process.domain import stack_review as texts
from agentic.shared.configuration import Configuration
from tests.fakes import ScriptedRunner, answers, memory_ports, questions, stack_proposal

pytestmark = pytest.mark.anyio

ASK, ANSWER, AUTHOR = "tech_lead_ephemeral_stack", "tech_lead_interviewee_stack", "tech_lead_stack"
ANCHOR = "deadbeef"


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


async def test_no_brief_or_open_questions_end_in_a_needs_info_issue_with_no_proposal(cfg):
    runner = ScriptedRunner(
        **{ASK: [questions(1, session="e1"), questions(done=True, session="e1")]},
        **{ANSWER: [answers(1, for_human=["the product has no BRIEF.md"], session="a1")]},
    )
    ports = memory_ports(runner)
    result = await stack_review("stack-2026-09-24", "no signals text yet", ANCHOR, cfg, ports)
    assert result.needs_info is True
    assert AUTHOR not in runner.slugs  # no proposal is written on this exit
    [(title, body)] = ports.tracker.opened_issues
    assert title.startswith("[needs-info]")
    assert texts.marker(ANCHOR) in body
    assert "the product has no BRIEF.md" in body
    assert "Stack diff" not in body and "ADR draft" not in body


async def test_a_signal_produces_a_proposal_issue_with_the_three_pieces_and_no_label(cfg):
    runner = ScriptedRunner(
        **{ASK: [questions(done=True, session="e1")]},
        **{AUTHOR: [stack_proposal(verdict_text="change to X", session="t1")]},
    )
    ports = memory_ports(runner)
    result = await stack_review(
        "stack-2026-09-24", "10 tasks merged since the last review", ANCHOR, cfg, ports
    )
    assert result.needs_info is False
    assert result.issue_url == ports.tracker.url
    [(title, body)] = ports.tracker.opened_issues
    assert "needs-info" not in title
    assert texts.marker(ANCHOR) in body
    assert "change to X" in body
    assert "a diff" in body  # stack_diff piece
    assert "Hex." in body  # standards piece
    assert "an ADR draft" in body
    # The port has no way to carry a label at all (ADR-0032).
    assert not hasattr(ports.tracker, "labels") or ports.tracker.labels == []


async def test_an_over_cap_proposal_keeps_the_full_text_in_the_document_and_caps_the_issue(cfg):
    huge = "s" * (texts.ISSUE_BODY_MAX_CHARS + 1)
    runner = ScriptedRunner(
        **{ASK: [questions(done=True, session="e1")]},
        **{AUTHOR: [stack_proposal(standards=huge, session="t1")]},
    )
    ports = memory_ports(runner)
    result = await stack_review("stack-2026-09-24", "signals", ANCHOR, cfg, ports)
    [(_, body)] = ports.tracker.opened_issues
    assert huge not in body
    assert str(texts.ISSUE_BODY_MAX_CHARS) in body
    assert str(result.document) in body
    # Nothing paid is lost: the document keeps every piece verbatim.
    saved = ports.documents.saved[-1]
    assert huge in saved["sections"]["Tech Lead"]


async def test_an_over_cap_adr_draft_still_refuses_by_name_after_dropping_standards(cfg):
    # Dropping only `standards` (the first fallback) is not enough when the overflow is
    # in `adr_draft` instead: the reassembled body must be measured again, and refuse
    # by name rather than reach `open_issue` over the cap (blocking finding, review 1).
    huge = "a" * (texts.ISSUE_BODY_MAX_CHARS + 1)
    runner = ScriptedRunner(
        **{ASK: [questions(done=True, session="e1")]},
        **{AUTHOR: [stack_proposal(adr_draft=huge, session="t1")]},
    )
    ports = memory_ports(runner)
    result = await stack_review("stack-2026-09-24", "signals", ANCHOR, cfg, ports)
    [(_, body)] = ports.tracker.opened_issues
    assert len(body) <= texts.ISSUE_BODY_MAX_CHARS
    assert huge not in body
    assert str(texts.ISSUE_BODY_MAX_CHARS) in body
    assert str(result.document) in body
    # Nothing paid is lost: the document still keeps the full ADR draft.
    saved = ports.documents.saved[-1]
    assert huge in saved["sections"]["Tech Lead"]
