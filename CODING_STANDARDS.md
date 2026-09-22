# Coding standards

This repo is a product since ADR-0017. The system's default states the practices
(ADR-0012); this file says how each one is met here, and replaces the default whole
for every role that reads standards over this repo. It names the tool and points at
the registry that carries the checked scope; it does not restate the registry's
contents, because that is the very drift ADR-0021 refused.

## 1. Hexagonal architecture

The architecture test enforces the import rules on every delivery.

## 2. Tactical DDD

The glossary is `CONTEXT-MAP.md` and the `CONTEXT.md` each context it links (issue
#45); a term belongs to the context it names, never to the file it happens to live
in.

## 3. SOLID

No mechanics beyond the practice itself.

## 4. Patterns name problems

Named in the plan, per pattern, the same as any product (ADR-0012).

## 5. Tests at the seams the plan agrees

The unit seam is each module's domain, run with `pytest`. The behaviour seam is
whichever one the plan names for the subtask at hand, and it varies task by task.

## 6. Strict typing, without silencing

The type checker is mypy. The checked scope is the registry, `[tool.mypy] files`; the
dial that widens or narrows it, and the sanctioned form for a narrowing the checker
cannot express, are both ADR-0021. The linter and the formatter are both ruff.

## 7. Observability

Met through this repo's own measurement module (ADR-0008): a closed set of labels, no
free text, and a change that adds, removes or moves a signal says so in the plan.

## 8. Security and performance

A change that widens what an input may reach or do says in the plan what it now
allows and to whom; a change whose cost scales with data size or traffic says so in
the plan, with the shape of the cost.
