"""Which hand runs of a declared check the Developer is refused (ADR-0065, addendum 2026-11-25,
GH-1070): the one pure predicate `suite_refusal`, over the table of its inputs."""

import itertools

import pytest

from agentic.core.process.domain.run_check import HAND_WORKERS, suite_refusal

DELIVERY = ("tests", "lint")
AFFECTED = {"tests": "uv run pytest -n {workers} $(git diff --name-only {base}) --dist load"}
BASE = "abc123"


def refusal(
    name: str,
    *,
    stores: bool | None = True,
    delivery: tuple[str, ...] = DELIVERY,
    affected: dict[str, str] | None = None,
    verifications: tuple[str, ...] = (),
    red: frozenset[str] = frozenset(),
) -> str | None:
    return suite_refusal(
        name,
        delivery=delivery,
        affected=AFFECTED if affected is None else affected,
        verifications=verifications,
        red=red,
        base=BASE,
        stores=stores,
    )


@pytest.mark.parametrize("stores", [True, False, None])
def test_a_check_with_an_affected_form_is_refused_whatever_the_tree(stores):
    text = refusal("tests", stores=stores)
    assert text is not None
    assert f"uv run pytest -n {HAND_WORKERS} $(git diff --name-only {BASE}) --dist load" in text
    assert "Bash" in text and "deliver" in text
    assert "run_check" in text and "in full" in text


@pytest.mark.parametrize("stores", [True, None])
def test_a_check_without_an_affected_form_runs_when_its_pass_is_stored_or_never_is(stores):
    assert refusal("lint", stores=stores) is None


def test_a_check_without_an_affected_form_is_refused_when_its_pass_would_not_be_stored():
    text = refusal("lint", stores=False)
    assert text is not None
    assert "commit" in text and "run_check" in text and "reuses" in text
    assert "Bash" in text and "deliver" in text


@pytest.mark.parametrize("stores", [True, False, None])
def test_a_check_in_the_red_set_is_never_refused(stores):
    assert refusal("tests", stores=stores, red=frozenset({"tests"})) is None
    assert refusal("lint", stores=stores, red=frozenset({"lint"})) is None


@pytest.mark.parametrize("stores", [True, False, None])
def test_a_red_check_does_not_open_another(stores):
    assert refusal("tests", stores=stores, red=frozenset({"lint"})) is not None


@pytest.mark.parametrize("stores", [True, False, None])
def test_a_verification_is_never_refused_even_when_it_shares_a_delivery_name(stores):
    assert refusal("tests", stores=stores, verifications=("tests",)) is None
    assert refusal("lint", stores=stores, verifications=("lint",)) is None


@pytest.mark.parametrize("stores", [True, False, None])
def test_a_check_outside_the_delivery_tier_is_never_refused(stores):
    assert refusal("e2e", stores=stores) is None
    assert refusal("e2e", stores=stores, affected={"e2e": "run {base}"}) is None
    assert refusal("tests", stores=stores, delivery=()) is None


def test_no_token_survives_in_any_text():
    affected = {"tests": "pytest -n {workers} --base {base} {base}", "lint": "ruff {base}"}
    for name, stores, with_affected in itertools.product(
        DELIVERY, (True, False, None), (True, False)
    ):
        text = refusal(name, stores=stores, affected=affected if with_affected else {})
        if text is not None:
            assert "{base}" not in text and "{workers}" not in text
    assert "pytest -n 1 --base abc123 abc123" in (refusal("tests", affected=affected) or "")


def test_a_command_with_braces_of_its_own_is_rendered_not_formatted():
    affected = {"tests": "sh -c 'echo ${HOME} {base} {workers}'"}
    text = refusal("tests", affected=affected)
    assert text is not None and "echo ${HOME} abc123 1" in text
