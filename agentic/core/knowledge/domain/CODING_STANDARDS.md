# Coding standards

How code is written in this product. They apply to the code a task adds or touches,
not to what already existed: old shapes are migrated by a platform enhancement, never
in passing. Where they meet, the task's ADR says how they coexist.

## 1. Hexagonal architecture

- `domain/` imports nothing from frameworks, databases or HTTP. Entities, value
  objects, domain services and the ports (interfaces) the domain needs live here.
- `application/` holds the use cases: one function or class per action, orchestrating
  the domain through ports. It imports the domain, never an adapter.
- `adapters/` implements the ports (repositories over the database, HTTP routers, external
  clients) and is the only layer that imports frameworks.
- An architecture test checks these import rules on every run. If the product has none,
  the first subtask of the task creates it.

## 2. Tactical DDD

- The ubiquitous language is the glossary in `CONTEXT.md`. Names in code and tests use
  its terms exactly; a new concept goes into the glossary before it goes into code.
- Model with entities (identity), value objects (equality by value, immutable),
  aggregates (one root, one transaction), repositories as ports, and domain services
  for rules that belong to no single entity.
- Invariants are enforced in the domain, not in the router or the database.

## 3. SOLID

- Single responsibility: a module changes for one reason.
- Dependency inversion is what makes the hexagon real: the domain declares ports, the
  adapters depend on the domain, never the reverse.
- Open for extension through new adapters and strategies, closed to editing working
  code to add a variant.

## 4. Patterns name problems

- Use a pattern (Repository, Strategy, Specification, Factory, Adapter) when it names a
  problem the code actually has, and say which problem in the plan. Never for its own
  sake, never speculatively.
- CQRS, event sourcing and message buses are out until a task needs them.

## 5. Tests at two seams

- Unit tests of the domain, with no infrastructure: pure inputs and outputs.
- Behaviour tests at the HTTP seam, through the public API, with the real adapters over a
  throwaway database.
- Tests are written test-first at seams agreed in the plan, never against internals.
  A test that breaks on a refactor without a behaviour change is wrong.

## 6. Strict typing and a clean linter

- Type hints everywhere; the type checker and the linter pass with no warnings before a
  delivery. Do not silence them; fix the code.
