"""The per-role usage breakdown: `agentic.core.measurement.domain.role_usage`."""

from agentic.core.measurement.domain import role_usage, scorecard


def run(slug, turns=3, cost=1.0, **tokens):
    return {
        "role": slug.title(),
        "slug": slug,
        "section": "s",
        "version": 1,
        "cost_usd": cost,
        "turns": turns,
        "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, **tokens},
    }


def test_two_runs_of_the_same_slug_are_summed():
    usage = role_usage.by_role(
        [
            run("developer", turns=3, cost=1.5, input=10, output=5, cache_read=100, cache_write=7),
            run("developer", turns=4, cost=0.5, input=1, output=2, cache_read=3, cache_write=4),
            run("pm", turns=1, cost=0.25),
        ]
    )
    assert usage["developer"] == role_usage.RoleUsage(
        runs=2,
        turns=7,
        input_tokens=11,
        cache_read_tokens=103,
        cache_write_tokens=11,
        output_tokens=7,
        cost_usd=2.0,
    )
    assert usage["pm"].runs == 1


def test_a_run_recorded_before_token_fields_existed_reads_zero():
    old = {"role": "PM", "section": "s", "cost_usd": 0.4, "turns": 2}
    usage = role_usage.by_role([old])
    assert usage == {
        "unknown": role_usage.RoleUsage(
            runs=1,
            turns=2,
            input_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            output_tokens=0,
            cost_usd=0.4,
        )
    }


def test_no_runs_gives_an_empty_breakdown():
    assert role_usage.by_role([]) == {}


def test_a_document_with_no_runs_key_contributes_nothing_after_bucketing():
    document = {"id": "GH-1", "title": "t", "description": "d", "system_baseline": "bl-x"}
    buckets, _ = scorecard.bucket_by_baseline([("GH-1", document)], resolve=lambda sha: "unknown")
    ((_, figures),) = buckets["bl-x"]
    assert role_usage.by_role(figures.runs) == {}


def test_the_runs_of_a_bucket_come_through_bucket_by_baseline():
    document = {
        "id": "GH-1",
        "title": "t",
        "description": "d",
        "system_baseline": "bl-x",
        "runs": [run("tl", cost=2.0)],
    }
    buckets, _ = scorecard.bucket_by_baseline([("GH-1", document)], resolve=lambda sha: "unknown")
    figures = [f for _, f in buckets["bl-x"]]
    usage = role_usage.by_role(r for f in figures for r in f.runs)
    assert usage["tl"].cost_usd == 2.0
