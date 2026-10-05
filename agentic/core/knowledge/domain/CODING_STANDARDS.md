# Coding standards

How code is written in this product. They apply to the code a task adds or touches,
not to what already existed: old shapes are migrated by a platform enhancement, never
in passing. Where they meet, the task's ADR says how they coexist.

These are practices, not tools: how each one is met in this product's own stack lives
in this product's own `CODING_STANDARDS.md`, which replaces this default whole
(ADR-0012).

## 1. Hexagonal architecture

- The domain imports nothing from a framework or from anything outside the process:
  entities, value objects, domain services and the ports the domain needs live here.
- The application layer holds the use cases: one function or class per action,
  orchestrating the domain through ports. It imports the domain, never an adapter.
- Adapters implement the ports — the boundary to anything outside the process — and
  are the only layer that imports a framework or a client for the outside world.
- An architecture test checks these import rules on every run. If the product has
  none, the first subtask of the task creates it.

## 2. Tactical DDD

- The ubiquitous language is the glossary in `CONTEXT.md`. Names in code and tests use
  its terms exactly; a new concept goes into the glossary before it goes into code.
- Model with entities (identity), value objects (equality by value, immutable),
  aggregates (one root, one transaction), repositories as ports, and domain services
  for rules that belong to no single entity.
- Invariants are enforced in the domain, never at whichever boundary happens to catch
  the bad input first.

## 3. SOLID

- Single responsibility: a module changes for one reason.
- Dependency inversion is what makes the hexagon real: the domain declares ports, the
  adapters depend on the domain, never the reverse.
- Open for extension through new adapters and strategies, closed to editing working
  code to add a variant.

## 4. Patterns name problems

- Use a pattern (Repository, Strategy, Specification, Factory, Adapter) when it names
  a problem the code actually has, and say which problem in the plan. Never for its
  own sake, never speculatively.
- CQRS, event sourcing and message buses are out until a task needs them.

## 5. Tests at the seams the plan agrees

- Unit tests of the domain, with no infrastructure: pure inputs and outputs.
- Behaviour tests at the seam a product actually exposes — a public API, a command
  line, a message it consumes, a library's own entry point — with the real adapters
  over a disposable environment built for the test.
- Tests are written test-first at seams agreed in the plan, never against internals.
  A test that breaks on a refactor without a behaviour change is wrong.

## 6. Strict typing, without silencing

- Static types on every signature that crosses a module boundary. The type checker
  and the linter this product declares pass with no warnings before a delivery.
- Do not silence a violation the checker or the linter raises: fix the code, or say in
  the plan why a unit is left out of the checked scope, named and revisited later.

## 7. Observability

- Code that changes an observable surface leaves it observable: a signal a role or an
  operator already reads keeps reading true after the change.
- A new capability says, in the plan, what it will emit and where — or says "nothing
  yet" on purpose, so the gap is a decision and not an omission discovered later.

## 8. Security and performance

- A change that widens what an input may reach or do (a new field, a new route, a new
  permission) says in the plan what it now allows and to whom.
- A change whose cost scales with data size or traffic says so in the plan, with the
  shape of the cost, not only that it was measured once.

## 9. Naming of persisted schema

Covers tables, columns, keys, indexes and constraints of the persisted schema.

- Lowercase `snake_case` for every table, column, index and constraint.
- Singular table names (`customer`, not `customers`); a junction table is named
  `<a>_<b>`, after the two tables it joins.
- Primary key `id`; a foreign key is named `<referenced_table>_id`.
- A boolean starts with `is_` (`is_vip`, `is_shown`).
- `_on` suffixes a calendar date, `_at` a moment in time.
- An index is named `ix_<table>_<columns>`, a unique constraint `uq_<table>_<columns>`.

These rules apply to new tables and columns and to the ones a task touches; renaming
existing schema is a task of its own, never a side effect.
