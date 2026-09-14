"""The system, in modules (ADR-0010). Each one is hexagonal on its own: `domain/`,
`application/`, `adapters/`, only the layers it has. `process` is the core domain
and imports none of the others; `knowledge` and `measurement` support it and may
import its domain and application, never its adapters. `tests/test_architecture.py`
checks the rule on the imports; `CONTEXT-MAP.md` at the root maps the three.
"""
