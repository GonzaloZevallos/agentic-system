"""The collector config left in the tree references no exporter, extension or
processor a diff deleted (Tech Lead plan, invariant): every name under
`service.extensions` and every name in each pipeline's
`receivers`/`processors`/`exporters` has to be defined in the matching top-level
section (`receivers:`, `processors:`, `exporters:`, `extensions:`) of the same file --
the declarations block that, by convention, sits above `service:`. This is the
replacement enforcement artefact for the Grafana-era guard this diff removes
(ADR-0023: built here, over a config constructed in the test, never by breaking the
real file), on the seam the plan declares: the *parsed*
`observability/collector/otel-collector.yaml`.
"""

from pathlib import Path

import yaml

COLLECTOR_CONFIG = (
    Path(__file__).resolve().parents[1] / "observability" / "collector" / "otel-collector.yaml"
)

# The four top-level sections a pipeline or `service.extensions` may point into, and
# the key under `service` that lists which extensions are active.
DECLARATION_SECTIONS = ("receivers", "processors", "exporters", "extensions")


def undefined_references(config: dict) -> list[str]:
    """Every name a pipeline or `service.extensions` points at that the matching
    top-level section does not define, as `"<section>.<name>"`. Empty means the
    config is closed: nothing under `service` names a component this file never
    declared."""
    declared = {section: set(config.get(section) or {}) for section in DECLARATION_SECTIONS}
    service = config.get("service") or {}
    missing: list[str] = []

    for name in service.get("extensions") or []:
        if name not in declared["extensions"]:
            missing.append(f"extensions.{name}")

    for pipeline_name, pipeline in (service.get("pipelines") or {}).items():
        for section in ("receivers", "processors", "exporters"):
            for name in pipeline.get(section) or []:
                if name not in declared[section]:
                    missing.append(f"{pipeline_name}.{section}.{name}")

    return missing


def test_the_real_collector_config_has_no_undefined_reference():
    config = yaml.safe_load(COLLECTOR_CONFIG.read_text(encoding="utf-8"))
    assert undefined_references(config) == []


def test_traces_langfuse_pipeline_is_unchanged_by_the_grafana_removal():
    """Regression evidence the subtask brief asks for by name: the pipeline that
    reaches Langfuse, and the processor that translates Claude Code's usage counters
    for it, carry no trace of the Grafana deletion around them."""
    config = yaml.safe_load(COLLECTOR_CONFIG.read_text(encoding="utf-8"))
    assert config["service"]["pipelines"]["traces/langfuse"] == {
        "receivers": ["otlp"],
        "processors": ["transform/claude_code_usage", "batch"],
        "exporters": ["otlphttp/langfuse"],
    }
    assert "claude_code.llm_request" in yaml.safe_dump(
        config["processors"]["transform/claude_code_usage"]
    )


def test_no_grafana_pipeline_or_component_survives():
    """`grafana` still names, in prose comments, the destination this diff removes
    (ADR-0036 tells that story); no component, pipeline or environment variable named
    after it survives in the config a collector actually loads."""
    config = yaml.safe_load(COLLECTOR_CONFIG.read_text(encoding="utf-8"))
    text = COLLECTOR_CONFIG.read_text(encoding="utf-8")
    assert "GRAFANA_" not in text
    assert set(config["service"]["pipelines"]) == {"traces/langfuse"}
    assert "extensions" not in config
    assert "extensions" not in config["service"]
    for section in ("receivers", "processors", "exporters"):
        assert not any("grafana" in name for name in config[section])


def test_a_pipeline_that_references_an_undefined_exporter_is_rejected():
    """The rejecting input the guard exists for (ADR-0023): a config built in this
    test, never the real file broken on purpose. A pipeline naming an exporter no
    `exporters:` section declares is exactly the shape a careless deletion in
    `otel-collector.yaml` would leave behind."""
    broken = {
        "receivers": {"otlp": {}},
        "processors": {"batch": {}},
        "exporters": {"otlphttp/langfuse": {}},
        "service": {
            "pipelines": {
                "traces/langfuse": {
                    "receivers": ["otlp"],
                    "processors": ["batch"],
                    "exporters": ["otlphttp/langfuse", "otlphttp/grafana"],
                }
            }
        },
    }
    assert undefined_references(broken) == ["traces/langfuse.exporters.otlphttp/grafana"]


def test_a_service_extension_never_declared_is_rejected():
    broken = {
        "receivers": {"otlp": {}},
        "processors": {},
        "exporters": {"otlphttp/langfuse": {}},
        "extensions": {},
        "service": {
            "extensions": ["basicauth/grafana"],
            "pipelines": {
                "traces/langfuse": {
                    "receivers": ["otlp"],
                    "processors": [],
                    "exporters": ["otlphttp/langfuse"],
                }
            },
        },
    }
    assert undefined_references(broken) == ["extensions.basicauth/grafana"]


def test_a_fully_declared_config_passes():
    clean = {
        "receivers": {"otlp": {}},
        "processors": {"batch": {}},
        "exporters": {"otlphttp/langfuse": {}},
        "service": {
            "pipelines": {
                "traces/langfuse": {
                    "receivers": ["otlp"],
                    "processors": ["batch"],
                    "exporters": ["otlphttp/langfuse"],
                }
            }
        },
    }
    assert undefined_references(clean) == []
