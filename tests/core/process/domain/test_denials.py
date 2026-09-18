"""The sentence a non-executing role reads when the SDK denies a call outside its
`Role.allowed` (glossary: Negación; issue #69, #81): the permission layer already
decided, so this module only supplies the words, derived from `role.tools` alone,
never a second list of what the role may run."""

from dataclasses import replace

from agentic.core.process.domain.denials import denial_for
from agentic.core.process.domain.roles import PJM, PRODUCT_ANALYST_BUG


def test_a_role_with_bash_may_reproduce_by_running_the_declared_checks():
    text = denial_for(PRODUCT_ANALYST_BUG)
    assert "this role writes nothing" in text
    assert "reading the code, the task documents and the existing tests" in text
    assert "running the declared checks" in text


def test_a_role_with_no_bash_is_told_only_to_read():
    # PjM's default has no tools at all: nothing to run, so the sentence never invites
    # it to try (deriving straight off `role.tools`, no second list).
    text = denial_for(PJM)
    assert "reading the code, the task documents and the existing tests" in text
    assert "running the declared checks" not in text


def test_the_sentence_names_the_role_not_a_syntax_problem():
    # Issue #69: the Product Analyst treated eight denials as a syntax problem and
    # retried with heredocs, `python3 -`, and base64 instead of changing strategy.
    assert "not a syntax problem" in denial_for(PJM)


def test_a_role_without_bash_allowed_still_reads_only_from_tools():
    # A role can list `Bash` in `tools` while its `allowed` rules cover none of it yet;
    # `denial_for` only asks whether the role has the tool at all (`role.tools`), since
    # deciding whether a given call is covered is the SDK permission layer's job, not a
    # second parser here.
    reader = replace(PJM, tools=("Read", "Bash"), allowed=("Read",))
    assert "running the declared checks" in denial_for(reader)
