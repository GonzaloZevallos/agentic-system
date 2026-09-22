# Brief

## Problem and who has it

Building a product with agentic roles today means one human doing PM, Tech Lead,
Developer and reviewer in series, at the pace a single person types. The human who
owns this system (README.md, 8) wants to hand each of those roles to an agent and
keep only what a human must keep: the calls that trade money or scope, and the
decision to merge. Without a system, nothing stops a role from re-deciding what a
previous role already settled, and nothing measures whether a change actually shipped
without the human touching it.

## Jobs

- When a task arrives as a GitHub issue, I want it classified, planned, built and
  reviewed by agents that hand off through one document, so I can spend my own time on
  the calls only a human can make. Success: a pull request the human reviews once,
  never a transcript he has to reconstruct.
- When a role disagrees with an ADR or a plan another role already settled, I want it
  escalated to me instead of silently re-decided, so the system's own decisions stay
  decided. Success: a question in the task document, not a divergent diff.
- When I change a prompt or a parameter, I want the same five tasks re-run against it,
  so I can tell whether the change helped before it reaches every future task.
  Success: a binary, strict score (README.md, 9) — the human merges the diff
  untouched, or the task does not count.
- When the system develops itself, I want it to run from a checkout it never edits, so
  a Developer changing routing code cannot invalidate the trace of the very run that is
  changing it. Success: every span carries a `system_version` that names a sha nothing
  in the run can move (ADR-0008, ADR-0017).

## Constraints

- Scale: two products only, gym-saas (the evaluation base, README.md 8-9) and this
  system developing itself (ADR-0017); no third product is in scope.
- Cost: each attempt has its own budget and each task has its own cap (README.md, 3);
  a run that would exceed either escalates to the human instead of continuing on its
  own credit.
- Observability: every metric, span and label lives in a closed, versioned set
  (ADR-0008); nothing free-text, nothing a role can invent by writing a new string.
- Regulatory: none — neither product handles data that requires it today.
- Integrations mandatory: GitHub (`gh`) for issues and pull requests, the Claude Agent
  SDK for every role, git worktrees for isolation between subtasks (ADR-0011).

## Out of scope

No PM and no business metrics on this repo's own path: every task on the system
itself is a platform enhancement (README.md, 8), so there is no product-market
question to answer here, only "does the system get better at building the other
product." Frontend and its roles (UX/UI Designer, UX/Design Lead) stay out until
gym-saas grows one.
