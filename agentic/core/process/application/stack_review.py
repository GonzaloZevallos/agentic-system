"""The stack review's own use case (ADR-0034): interview, then the author's proposal,
over `Ports`, with no task behind either -- no issue it came from, no branch, no
document under `.agentic/tasks/`. It runs through `interview` and `run_role`, so the
per-attempt cap, the single retry with the error as input, `run_with_retry`'s session
continuation, the schema guard and the denial cage all apply exactly as they do to any
other role.

Whether a review is worth running at all -- the signals, and whether one this path
already opened is still open -- is decided before this function is ever called
(`domain/stack_signals.py`, and the gate `cli.py` composes): this module always runs
the interview and, when it reaches one, the author.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentic.shared.configuration import Configuration

from ..domain import stack_review as texts
from ..domain.roles import INTERVIEW_SOLUTION_STACK
from ..domain.task import TaskState, apply
from .interview import interview
from .ports import Ports
from .run_role import run_role

_SPEC = INTERVIEW_SOLUTION_STACK


@dataclass(frozen=True)
class StackReview:
    """What one call of `stack_review` produced: the issue it opened (`None` only when
    the author itself failed as a role after its retry -- the needs-info exit always
    opens something), the document written to disk, and whether the issue is the
    needs-info exit rather than a proposal."""

    issue_url: str | None
    document: Path
    needs_info: bool


async def stack_review(
    review_id: str, description: str, anchor: str, cfg: Configuration, ports: Ports
) -> StackReview:
    """Run the interview then the author, in the checkout `cfg.product_dir` already is
    -- no worktree, no branch (ADR-0034). `description` is both the synthetic state's
    `ORIGINAL` section and the issue's own `## Signals` text: the signals that
    justified this review, computed once by the caller and never re-derived here.
    `anchor` is the commit `domain.stack_review.marker` records.

    No Brief, or an interview that ends with questions still open for the human, take
    the same exit: a `[needs-info]` issue with the questions, no proposal (the ADR's
    own "one exit, reused"). A body over `texts.ISSUE_BODY_MAX_CHARS` is never
    truncated and the interview is never re-run: the review document keeps every piece
    verbatim, and only the issue's own `standards` piece is replaced by a note pointing
    at the document."""
    state: TaskState = {"id": review_id, "title": "Stack review", "description": description}
    transcript, patch = await interview(state, _SPEC, cfg, ports)
    if transcript is None:
        return _needs_info(state, patch, anchor, description, ports)

    after = apply(state, patch)
    output, written = await run_role(
        after, _SPEC.author, cfg, ports, resume=transcript.sessions.get(_SPEC.author_side.slug)
    )
    final = apply(patch, written)
    if output is not None:
        section = _document_section(output.text, output.data)
        final = apply(final, {"sections": {_SPEC.author.name: section}})
    document = ports.documents.save(apply(state, final))
    if output is None:
        return StackReview(issue_url=None, document=document, needs_info=False)

    issue_body = _capped_proposal_body(anchor, description, output.data, document)
    url = ports.tracker.open_issue(f"Stack review {review_id}: proposal", issue_body)
    return StackReview(issue_url=url, document=document, needs_info=False)


def _capped_proposal_body(
    anchor: str, description: str, data: dict[str, str], document: Path
) -> str:
    """The issue body for a proposal, measured against `texts.ISSUE_BODY_MAX_CHARS`
    before it is ever handed to `open_issue` -- never `gh`'s own non-zero exit (the
    plan's own invariant, checked again here after the first fallback, not only on the
    role's raw output): the full three pieces when they fit; standards replaced by a
    note pointing at the document when only that piece tips it over; and, when even
    that still does not fit -- the diff or the ADR draft alone the size of the
    overflow -- every piece dropped for a refusal that names the count, so nothing but
    the verdict and that note ever reaches `gh`. The document (`_document_section`)
    keeps every piece verbatim regardless: nothing paid is ever lost, only the issue's
    own body shrinks."""
    full = texts.body(
        anchor,
        description,
        data["verdict"],
        data["stack_diff"],
        data["standards"],
        data["adr_draft"],
    )
    if (refusal := texts.body_refusal(full)) is None:
        return full
    note = (
        f"The standards exceeded the issue cap ({refusal}); the full text is in "
        f"the review document at `{document}`."
    )
    without_standards = texts.body(
        anchor, description, data["verdict"], data["stack_diff"], note, data["adr_draft"]
    )
    if (refusal := texts.body_refusal(without_standards)) is None:
        return without_standards
    verdict = (
        f"{data['verdict']}\n\nThe proposal exceeded the issue cap ({refusal}); the "
        f"full stack diff, standards and ADR draft are in the review document at "
        f"`{document}`."
    )
    return texts.body(anchor, description, verdict, "", "", "")


def _document_section(text: str, data: dict[str, str]) -> str:
    """What the author's own section holds on disk: its `markdown` summary (`text`,
    already the role's delivered text) plus every piece verbatim, never capped -- the
    cap only ever bites the issue body, not the document (the ADR's own "nothing paid
    is lost")."""
    return (
        f"{text}\n\n"
        f"### Stack diff\n\n{data['stack_diff']}\n\n"
        f"### Standards\n\n{data['standards']}\n\n"
        f"### ADR draft\n\n{data['adr_draft']}"
    )


def _needs_info(
    state: TaskState, patch: TaskState, anchor: str, description: str, ports: Ports
) -> StackReview:
    after = apply(state, patch)
    document = ports.documents.save(after)
    questions = after.get("questions_for_human") or [
        str(after.get("escalated") or "the interview could not reach a decision")
    ]
    verdict = "\n".join(f"- {q}" for q in questions)
    issue_body = texts.body(anchor, description, verdict, "", "", "")
    if (refusal := texts.body_refusal(issue_body)) is not None:
        issue_body = texts.body(anchor, description, refusal, "", "", "")
    url = ports.tracker.open_issue(f"[needs-info] Stack review {state['id']}", issue_body)
    return StackReview(issue_url=url, document=document, needs_info=True)
