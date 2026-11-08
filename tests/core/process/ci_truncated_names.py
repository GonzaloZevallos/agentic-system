"""Shared names of GH-556: the two matrix job names GitHub truncated (no closing parenthesis),
quoted from apostrophe's escalations (GH-601, GH-399 / PR #615)."""

TRUNCATED_PYTEST: tuple[str, str] = (
    "pytest (always, tests/architecture tests/deploy tests/design tests/docs"
    " tests/migrations tests/op...",
    "pytest (areas-1, tests/customers ... tests...",
)
