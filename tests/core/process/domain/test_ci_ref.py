"""`rules.ci_ref` and `rules.stale_ci_refs` (GH-376): the name of the ref the full tier
publishes, and which of those refs the poller may sweep. Pure."""

from agentic.core.process.domain import rules


def test_the_ci_ref_is_apart_from_the_task_branch() -> None:
    assert rules.ci_ref("GH-376") == "agentic-ci/GH-376"
    assert rules.ci_ref("GH-376") != rules.task_branch("GH-376")


def test_only_the_ref_of_an_ended_task_with_no_live_run_is_stale() -> None:
    refs = ["agentic-ci/GH-1", "agentic-ci/GH-2", "agentic-ci/GH-3", "agentic-ci/GH-4"]
    stale = rules.stale_ci_refs(refs, live_ids={"GH-1"}, ended_ids={"GH-1", "GH-2"})
    # GH-1 is live (even though ended), GH-2 is ended and idle, GH-3 is escalated (neither
    # live nor ended), GH-4 is an orphan nobody knows.
    assert stale == ("agentic-ci/GH-2",)


def test_a_ref_outside_the_prefix_is_never_stale() -> None:
    refs = ["agentic/GH-2", "agentic-ci-x/GH-2", "main", "agentic-ci/GH-2"]
    assert rules.stale_ci_refs(refs, live_ids=set(), ended_ids={"GH-2"}) == ("agentic-ci/GH-2",)


def test_stale_refs_keep_the_input_order() -> None:
    refs = ["agentic-ci/GH-9", "agentic-ci/GH-3"]
    assert rules.stale_ci_refs(refs, set(), {"GH-3", "GH-9"}) == tuple(refs)
