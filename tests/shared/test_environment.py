"""The environment the system hands down to what it spawns."""

from pathlib import Path

from agentic.shared.configuration import Configuration
from agentic.shared.environment import (
    RESOURCE_ROLE_SLUG_KEY,
    RESOURCE_TRACE_ID_KEY,
    own_configuration_unset,
    trace_id_of,
    with_resource_marker,
    without_own_configuration,
    without_own_toolchain,
)

UV_RUN = {
    "HOME": "/home/z",
    "PATH": "/repo/.venv/bin:/home/z/.local/bin:/usr/bin",
    "VIRTUAL_ENV": "/repo/.venv",
    "UV": "/home/z/.local/bin/uv",
    "UV_RUN_RECURSION_DEPTH": "1",
    "UVICORN_PORT": "8000",  # not uv's: a product's variable that happens to start alike
}


def test_the_systems_own_interpreter_is_stripped_from_what_it_spawns():
    env = without_own_toolchain(UV_RUN)
    assert env == {
        "HOME": "/home/z",
        "PATH": "/home/z/.local/bin:/usr/bin",
        "UVICORN_PORT": "8000",
    }
    assert UV_RUN["VIRTUAL_ENV"] == "/repo/.venv"  # the input is not touched


def test_the_venv_bin_is_matched_as_a_path_however_it_is_spelled():
    env = without_own_toolchain(
        {"PATH": "/repo/.venv/bin/:/usr/bin:/repo/.venv/bin", "VIRTUAL_ENV": "/repo/.venv/"}
    )
    assert env == {"PATH": "/usr/bin"}


def test_nothing_to_strip_is_a_copy():
    plain = {"PATH": "/usr/bin", "HOME": "/home/z"}
    assert without_own_toolchain(plain) == plain


OWN_RUN = {
    "PATH": "/usr/bin",
    "AGENTIC_PRODUCT_DIR": "/repos/agentic-system",
    "AGENTIC_VERBOSE": "1",
    "AGENTIC_SYSTEM_VERSION": "deadbee",
    "ANTHROPIC_API_KEY": "sk-x",  # not ours: a prefix that happens to start alike
}


def test_the_systems_own_configuration_is_stripped_from_what_it_spawns():
    assert without_own_configuration(OWN_RUN) == {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-x"}
    assert OWN_RUN["AGENTIC_VERBOSE"] == "1"  # the input is not touched


def test_what_cannot_be_dropped_is_overwritten_with_nothing():
    """The SDK merges this process's environment into every role's, so the only way to
    take a variable out of there is to hand it back empty."""
    assert own_configuration_unset(OWN_RUN) == {
        "AGENTIC_PRODUCT_DIR": "",
        "AGENTIC_VERBOSE": "",
        "AGENTIC_SYSTEM_VERSION": "",
    }


def test_an_empty_variable_is_unset_for_the_configuration_that_reads_it():
    """What makes the blanking work at all (`Configuration.from_sources`)."""
    cfg = Configuration.from_sources(
        {"product_dir": Path("/p")}, {"AGENTIC_PRODUCT_DIR": "", "AGENTIC_VERBOSE": ""}
    )
    assert cfg.product_dir == Path("/p")
    assert cfg.verbose is False


# --- trace_id_of: the trace id out of a W3C `traceparent` header --------------------

VALID_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
VALID_SPAN_ID = "00f067aa0ba902b7"


def test_trace_id_of_a_well_formed_header():
    header = f"00-{VALID_TRACE_ID}-{VALID_SPAN_ID}-01"
    assert trace_id_of(header) == VALID_TRACE_ID


def test_trace_id_of_none_is_none():
    assert trace_id_of(None) is None


def test_trace_id_of_an_empty_header_is_none():
    assert trace_id_of("") is None


def test_trace_id_of_the_wrong_number_of_fields_is_none():
    assert trace_id_of(f"00-{VALID_TRACE_ID}-{VALID_SPAN_ID}") is None


def test_trace_id_of_non_hex_is_none():
    header = f"00-{'z' * 32}-{VALID_SPAN_ID}-01"
    assert trace_id_of(header) is None


def test_trace_id_of_the_wrong_length_is_none():
    header = f"00-{VALID_TRACE_ID[:-2]}-{VALID_SPAN_ID}-01"
    assert trace_id_of(header) is None


def test_trace_id_of_all_zero_ids_is_none():
    """The W3C spec reserves an all-zero trace id or span id as invalid."""
    assert trace_id_of(f"00-{'0' * 32}-{VALID_SPAN_ID}-01") is None
    assert trace_id_of(f"00-{VALID_TRACE_ID}-{'0' * 16}-01") is None


# --- with_resource_marker: OTEL_RESOURCE_ATTRIBUTES merged, never overwritten -------


def test_inherited_absent_carries_only_our_keys():
    result = with_resource_marker(None, role_slug="tech_lead", trace_id=VALID_TRACE_ID)
    assert result == f"{RESOURCE_ROLE_SLUG_KEY}=tech_lead,{RESOURCE_TRACE_ID_KEY}={VALID_TRACE_ID}"


def test_inherited_present_with_unrelated_keys_is_preserved():
    result = with_resource_marker(
        "service.namespace=gym-saas,deployment.environment=dev",
        role_slug="tech_lead",
        trace_id=VALID_TRACE_ID,
    )
    assert result == (
        "service.namespace=gym-saas,deployment.environment=dev,"
        f"{RESOURCE_ROLE_SLUG_KEY}=tech_lead,{RESOURCE_TRACE_ID_KEY}={VALID_TRACE_ID}"
    )


def test_inherited_already_carrying_one_of_our_keys_is_refreshed_not_duplicated():
    stale = f"{RESOURCE_ROLE_SLUG_KEY}=stale_role,unrelated.key=kept"
    result = with_resource_marker(stale, role_slug="tech_lead", trace_id=VALID_TRACE_ID)
    assert result == (
        f"unrelated.key=kept,{RESOURCE_ROLE_SLUG_KEY}=tech_lead,"
        f"{RESOURCE_TRACE_ID_KEY}={VALID_TRACE_ID}"
    )
    assert "stale_role" not in result


def test_inherited_malformed_is_preserved_as_is_not_repaired():
    """An entry with no `=` is not ours to fix: kept verbatim alongside our marker."""
    malformed = "not-a-key-value-pair,also;broken"
    result = with_resource_marker(malformed, role_slug="tech_lead", trace_id=VALID_TRACE_ID)
    assert result == (
        "not-a-key-value-pair,also;broken,"
        f"{RESOURCE_ROLE_SLUG_KEY}=tech_lead,{RESOURCE_TRACE_ID_KEY}={VALID_TRACE_ID}"
    )


def test_a_value_that_would_truncate_the_list_is_percent_encoded():
    result = with_resource_marker(None, role_slug="tech_lead,review", trace_id=VALID_TRACE_ID)
    assert result == (
        f"{RESOURCE_ROLE_SLUG_KEY}=tech_lead%2Creview,{RESOURCE_TRACE_ID_KEY}={VALID_TRACE_ID}"
    )
    # the encoded comma does not add a third entry: the list still has exactly two
    assert result.count(",") == 1


def test_never_emits_a_service_key_of_its_own():
    result = with_resource_marker(
        "service.name=claude-code", role_slug="tech_lead", trace_id=VALID_TRACE_ID
    )
    assert result.split(",")[0] == "service.name=claude-code"  # kept, not ours to drop
    assert "service.name=" not in result.replace("service.name=claude-code", "", 1)


def test_merge_is_not_a_wholesale_overwrite():
    """The table above fails as a whole if the merge is replaced by a wholesale
    overwrite: an unrelated inherited key must always survive."""
    result = with_resource_marker(
        "deployment.environment=dev", role_slug="tech_lead", trace_id=VALID_TRACE_ID
    )
    assert "deployment.environment=dev" in result.split(",")


LANGFUSE_KEYS = {
    "AGENTIC_LANGFUSE_HOST": "http://lf:3000",
    "AGENTIC_LANGFUSE_PUBLIC_KEY": "pk-lf-x",
    "AGENTIC_LANGFUSE_SECRET_KEY": "sk-lf-x",
}


def test_the_langfuse_key_pair_never_reaches_a_role_a_check_or_the_sample_app():
    """GH-273, ADR-0037 addendum: the three `AGENTIC_LANGFUSE_*` are not `Configuration`
    fields, and the `AGENTIC_` prefix alone is what both scrubs key on. A name that lost
    the prefix would pass through, which this pins from the other side."""
    env = {"PATH": "/usr/bin", "LANGFUSE_SECRET_KEY": "sk-unprefixed", **LANGFUSE_KEYS}

    # The checks and the sample app (`without_own_configuration`) never see them.
    assert without_own_configuration(env) == {
        "PATH": "/usr/bin",
        "LANGFUSE_SECRET_KEY": "sk-unprefixed",
    }
    # The roles (`own_configuration_unset`) see each one empty, never its value.
    blanked = own_configuration_unset(env)
    assert {name: blanked[name] for name in LANGFUSE_KEYS} == dict.fromkeys(LANGFUSE_KEYS, "")
    assert "LANGFUSE_SECRET_KEY" not in blanked
