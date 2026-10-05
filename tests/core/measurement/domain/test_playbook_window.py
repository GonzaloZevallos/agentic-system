"""The playbook window: `agentic.core.measurement.domain.playbook_window` (ADR-0043).

Every test runs on fixtures of task documents (frontmatter dicts), the module's own input.
"""

from typing import Any

from agentic.core.measurement.domain import playbook_window as window
from agentic.core.process.domain.playbook import Delta, Item, Playbook

SLUG = "developer"
PAIR = "Tech Lead→Developer"
EMPTY = "empty"
A = "hash-a"
B = "hash-b"


def returns(category: str, count: int = 1, pair: str = PAIR) -> list[dict[str, str]]:
    return [{"pair": pair, "category": category, "reason": "r"}] * count


def task(
    n: int,
    key: str | None = A,
    history: list[dict[str, str]] | None = None,
    *,
    baseline: str | None = None,
    experiment: dict[str, str] | None = None,
    changed: str | None = None,
    closed: bool = True,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": f"GH-{n}",
        "type": "feature",
        "return_history": history or [],
    }
    if closed:
        document["closed_at"] = f"2026-10-01T00:{n // 60:02d}:{n % 60:02d}+00:00"
    if key is not None:
        document["playbooks"] = {SLUG: key}
    if changed is not None:
        document["playbooks_changed"] = {SLUG: changed}
    if baseline is not None:
        document["system_baseline"] = baseline
    if experiment is not None:
        document["experiment"] = experiment
    return document


def run(key: str, start: int, count: int, history: list[dict[str, str]] | None = None, **kw: Any):
    """`count` consecutive tasks under `key`, each carrying `history`."""
    return [task(start + i, None if key == EMPTY else key, history, **kw) for i in range(count)]


def item(
    item_id: str, category: str = "spec-behaviour", since: str = A, added_at: str = "2026-10-02"
) -> Item:
    return Item(
        id=item_id,
        lesson=f"Lesson {item_id}.",
        category=category,
        since=since,
        seen_in=("GH-1",),
        added_at=added_at,
    )


def playbook(*items: Item) -> Playbook:
    return Playbook(slug=SLUG, items=tuple(items))


def boundary(before_returns: int, after_returns: int, category: str = "spec-behaviour"):
    """10 tasks under `empty`, then 10 under A: `before_returns` of the first and
    `after_returns` of the second carry one `category` return."""
    docs = [task(i, None, returns(category) if i < before_returns else None) for i in range(10)] + [
        task(10 + i, A, returns(category) if i < after_returns else None) for i in range(10)
    ]
    return docs


# --- segments ---


def test_segments_are_runs_of_the_same_key_in_closed_order():
    docs = run(A, 0, 3) + run(B, 3, 2) + run(A, 5, 1)
    docs.reverse()

    keys = [(s.key, len(s.tasks)) for s in window.segments(docs, SLUG)]

    assert keys == [(A, 3), (B, 2), (A, 1)]


def test_a_task_without_the_slug_is_in_the_empty_segment():
    assert [s.key for s in window.segments(run(EMPTY, 0, 2), SLUG)] == [EMPTY]


def test_segments_leave_out_an_open_task_a_non_default_arm_and_a_changed_playbook():
    docs = [
        task(0),
        task(1, closed=False),
        task(2, experiment={"review_mode": "light"}),
        task(3, changed=B),
        task(4),
    ]

    segments = window.segments(docs, SLUG)

    assert [t["id"] for s in segments for t in s.tasks] == ["GH-0", "GH-4"]


def test_a_changed_playbook_for_another_slug_does_not_exclude_the_task():
    other = task(0)
    other["playbooks_changed"] = {"tech_lead": B}

    assert len(window.segments([other], SLUG)[0].tasks) == 1


# --- compare: the verdicts ---


def test_worse_flags_the_item_for_retirement():
    """The reject case: the item's category was returned more after it than before."""
    docs = boundary(before_returns=2, after_returns=5)

    [comparison] = window.compare(docs, playbook(item("developer-1")))

    assert comparison.conclusive
    assert comparison.retire == ("developer-1",)
    assert comparison.before["spec-behaviour"] == 0.2
    assert comparison.after["spec-behaviour"] == 0.5


def test_equal_keeps_the_item():
    [comparison] = window.compare(boundary(3, 3), playbook(item("developer-1")))

    assert comparison.conclusive
    assert comparison.retire == ()


def test_better_keeps_the_item():
    [comparison] = window.compare(boundary(5, 1), playbook(item("developer-1")))

    assert comparison.conclusive
    assert comparison.retire == ()


def test_an_item_from_another_segment_is_not_judged_by_this_boundary():
    docs = boundary(before_returns=0, after_returns=5)

    [comparison] = window.compare(docs, playbook(item("developer-1", since=B)))

    assert comparison.retire == ()


def test_a_category_without_an_item_is_not_flagged_even_when_worse():
    docs = boundary(before_returns=0, after_returns=5, category="spec-missing-test")

    [comparison] = window.compare(docs, playbook(item("developer-1", category="spec-behaviour")))

    assert comparison.retire == ()
    assert comparison.after["spec-missing-test"] == 0.5


def test_only_the_newest_of_two_items_in_the_same_segment_is_flagged():
    older = item("developer-1", added_at="2026-10-02")
    newer = item("developer-2", added_at="2026-10-03")

    [comparison] = window.compare(boundary(0, 4), playbook(older, newer))

    assert comparison.retire == ("developer-2",)


def test_items_added_at_the_same_time_flag_the_highest_id_numerically():
    nine = item("developer-9")
    ten = item("developer-10")

    [comparison] = window.compare(boundary(0, 4), playbook(nine, ten))

    assert comparison.retire == ("developer-10",)


def test_the_empty_to_first_hash_boundary_is_compared_like_any_other():
    [comparison] = window.compare(boundary(1, 3), playbook(item("developer-1")))

    assert (comparison.before_key, comparison.after_key) == (EMPTY, A)
    assert comparison.retire == ("developer-1",)


def test_the_window_is_the_last_ten_before_and_the_first_ten_after():
    """A long run before: only its last 10 count. Returns in its early tasks are out."""
    docs = (
        run(EMPTY, 0, 5, returns("spec-behaviour"))
        + run(EMPTY, 5, 10)
        + run(A, 15, 10)
        + run(A, 25, 5, returns("spec-behaviour"))
    )

    [comparison] = window.compare(docs, playbook(item("developer-1")))

    assert comparison.before == {}
    assert comparison.after == {}
    assert comparison.retire == ()


def test_only_returns_attributable_to_the_slug_count():
    """A return of the PRD (another pair) and one for the Tech Lead (plan-calibration)
    are not the Developer's."""
    history = returns("spec-behaviour", pair="Product Analyst→PM") + returns("plan-calibration")
    docs = run(EMPTY, 0, 10) + run(A, 10, 10, history)

    [comparison] = window.compare(docs, playbook(item("developer-1")))

    assert comparison.after == {}


# --- compare: inconclusive ---


def test_a_side_with_fewer_than_ten_tasks_is_inconclusive():
    docs = run(EMPTY, 0, 10) + run(A, 10, 9, returns("spec-behaviour"))

    [comparison] = window.compare(docs, playbook(item("developer-1")))

    assert not comparison.conclusive
    assert "9" in comparison.reason
    assert comparison.retire == ()


def test_a_crossed_baseline_is_inconclusive():
    docs = run(EMPTY, 0, 10, baseline="bl-2026-09-24") + run(
        A, 10, 10, returns("spec-behaviour"), baseline="bl-2026-10-01"
    )

    [comparison] = window.compare(docs, playbook(item("developer-1")))

    assert not comparison.conclusive
    assert "bl-2026-09-24" in comparison.reason and "bl-2026-10-01" in comparison.reason
    assert comparison.retire == ()


def test_one_baseline_on_every_task_is_conclusive():
    docs = run(EMPTY, 0, 10, baseline="bl-2026-09-24") + run(A, 10, 10, baseline="bl-2026-09-24")

    assert window.compare(docs, playbook())[0].conclusive


def test_a_non_default_arm_is_excluded_from_both_sides():
    """Ten default tasks each side plus an `light` one on each: the light ones neither
    count nor make a side short."""
    light = {"review_mode": "light", "skip_ephemeral": "False"}
    docs = boundary(0, 0) + [
        task(30, None, returns("spec-behaviour"), experiment=light),
        task(31, A, returns("spec-behaviour"), experiment=light),
    ]

    [comparison] = window.compare(docs, playbook(item("developer-1")))

    assert comparison.conclusive
    assert comparison.after == {}


def test_a_task_with_a_changed_playbook_is_excluded_from_both_sides():
    docs = run(EMPTY, 0, 9) + run(A, 10, 9)
    docs += [task(9, None, changed=A), task(19, A, returns("spec-behaviour"), changed=B)]

    [comparison] = window.compare(docs, playbook(item("developer-1")))

    assert not comparison.conclusive
    assert comparison.before_n == 9 and comparison.after_n == 9


def test_no_boundary_no_comparison():
    assert window.compare(run(A, 0, 25), playbook()) == []


# --- check proposals ---


def test_three_returns_in_the_window_despite_an_item_propose_a_check():
    comparisons = window.compare(boundary(0, 3), playbook(item("developer-1")))

    [proposal] = window.check_proposals(comparisons, playbook(item("developer-1")))

    assert (proposal.slug, proposal.category, proposal.count) == (SLUG, "spec-behaviour", 3)
    assert "presubmit" in proposal.text and "guard" in proposal.text and "hook" in proposal.text


def test_two_returns_propose_no_check():
    comparisons = window.compare(boundary(0, 2), playbook(item("developer-1")))

    assert window.check_proposals(comparisons, playbook(item("developer-1"))) == []


def test_three_returns_with_no_item_for_the_category_propose_no_check():
    comparisons = window.compare(
        boundary(0, 3), playbook(item("developer-1", category="spec-scope"))
    )

    assert (
        window.check_proposals(comparisons, playbook(item("developer-1", category="spec-scope")))
        == []
    )


def test_three_returns_in_an_inconclusive_window_propose_no_check():
    docs = run(EMPTY, 0, 10) + run(A, 10, 9, returns("spec-behaviour"))
    comparisons = window.compare(docs, playbook(item("developer-1")))

    assert window.check_proposals(comparisons, playbook(item("developer-1"))) == []


# --- the summary ---


def summary(comparisons, proposals=(), applied=(), gained=0):
    return window.summary_markdown(comparisons, proposals, applied, items_gained=gained)


def test_the_summary_counts_inconclusive_comparisons_and_says_why():
    docs = run(EMPTY, 0, 10) + run(A, 10, 4)

    text = summary(window.compare(docs, playbook(item("developer-1"))))

    assert "Inconclusive comparisons: 1" in text
    assert "4 tasks" in text


def test_the_summary_lists_added_merged_and_retired_items():
    applied = [
        Delta(op="add", slug=SLUG, category="spec-behaviour", lesson="Add one.", tasks=("GH-1",)),
        Delta(op="merge", slug=SLUG, item_id="developer-2", lesson="Merged.", tasks=("GH-1",)),
        Delta(op="retire", slug=SLUG, item_id="developer-3"),
    ]

    text = summary([], applied=applied)

    assert "Added: 1" in text and "Merged: 1" in text and "Retired: 1" in text
    assert "Add one." in text and "developer-2" in text and "developer-3" in text


def test_the_summary_shows_rates_retirements_and_check_proposals():
    pb = playbook(item("developer-1"))
    comparisons = window.compare(boundary(1, 4), pb)

    text = summary(comparisons, window.check_proposals(comparisons, pb))

    assert "spec-behaviour" in text and "0.10" in text and "0.40" in text
    assert "Retire developer-1" in text
    assert "mechanical check" in text.lower()


def test_eval_compare_is_recommended_after_a_conclusive_worse_comparison():
    comparisons = window.compare(boundary(0, 4), playbook(item("developer-1")))

    assert "agentic eval compare" in summary(comparisons)


def test_eval_compare_is_recommended_after_three_items_gained():
    assert "agentic eval compare" in summary([], gained=3)


def test_eval_compare_is_not_recommended_otherwise():
    comparisons = window.compare(boundary(3, 3), playbook(item("developer-1")))

    assert "agentic eval compare" not in summary(comparisons, gained=2)
