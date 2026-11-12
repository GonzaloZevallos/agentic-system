"""The plan revision (GH-591 part 2): a re-plan after a promotion revises the existing
subtasks, it does not redo them. Pure decisions at `revise`."""

from agentic.core.process.domain import plan_revision


def _item(sid, brief="do it", seams=("s",), depends_on=(), screens=()):
    return {
        "id": sid,
        "title": sid,
        "brief": brief,
        "seams": list(seams),
        "depends_on": list(depends_on),
        "screens": list(screens),
    }


def _old(sid, status="approved", **kw):
    item = _item(sid, **kw)
    item.pop("screens")
    return {
        **item,
        "status": status,
        "branch": f"agentic/T.{sid}",
        "base": "b0",
        "reviewed_head": f"h-{sid}",
    }


def test_an_unchanged_subtask_is_not_in_the_patch():
    old = {"a": _old("a"), "b": _old("b")}
    patch = plan_revision.revise(old, [_item("a"), _item("b", brief="other")], approved_work=False)
    assert "a" not in patch


def test_a_changed_brief_goes_back_to_pending_on_its_branch_keeping_its_head():
    old = {"a": {**_old("a"), "prd_recheck": True, "reason": "r"}, "b": _old("b")}
    patch = plan_revision.revise(old, [_item("a", brief="new"), _item("b")], approved_work=False)
    assert patch["a"]["status"] == "pending"
    assert patch["a"]["brief"] == "new"
    assert patch["a"]["branch"] == "agentic/T.a"
    assert patch["a"]["base"] == "b0"
    assert patch["a"]["reviewed_head"] == "h-a"
    assert "prd_recheck" not in patch["a"]


def test_only_depends_on_changed_counts_as_changed():
    old = {"a": _old("a"), "b": _old("b")}
    patch = plan_revision.revise(
        old, [_item("a"), _item("b", depends_on=["a"])], approved_work=False
    )
    assert patch["b"]["status"] == "pending"
    assert patch["b"]["depends_on"] == ["a"]
    assert "a" not in patch


def test_changed_seams_and_screens_count_as_changed_and_drop_stale_screens():
    old = {"a": {**_old("a"), "screens": ["x"]}, "b": _old("b")}
    patch = plan_revision.revise(old, [_item("a"), _item("b", seams=["t"])], approved_work=False)
    assert patch["a"]["status"] == "pending"
    assert "screens" not in patch["a"]
    assert patch["b"]["seams"] == ["t"]


def test_a_new_id_starts_pending_built_like_a_first_plan():
    old = {"a": _old("a")}
    patch = plan_revision.revise(
        old, [_item("a"), _item("c", screens=["scr"])], approved_work=False
    )
    assert patch["c"] == {
        "id": "c",
        "title": "c",
        "brief": "do it",
        "seams": ["s"],
        "depends_on": [],
        "status": "pending",
        "screens": ["scr"],
    }
    assert "screens" not in plan_revision.pending(_item("d"))


def test_an_unchanged_single_subtask_with_approved_work_is_rechecked_against_the_prd():
    old = {"a": _old("a")}
    patch = plan_revision.revise(old, [_item("a")], approved_work=True)
    assert patch["a"] == {**old["a"], "status": "delivered", "prd_recheck": True}


def test_an_unchanged_single_subtask_without_approved_work_is_left_alone():
    assert plan_revision.revise({"a": _old("a")}, [_item("a")], approved_work=False) == {}


def test_a_changed_single_subtask_is_pending_and_not_rechecked():
    patch = plan_revision.revise({"a": _old("a")}, [_item("a", brief="new")], approved_work=True)
    assert patch["a"]["status"] == "pending"
    assert "prd_recheck" not in patch["a"]


def test_a_single_subtask_that_never_ran_is_not_marked_delivered():
    old = {"a": _old("a", status="pending")}
    assert plan_revision.revise(old, [_item("a")], approved_work=True) == {}


def test_the_integration_is_rechecked_when_every_dependency_stays_approved():
    old = {
        "a": _old("a"),
        "b": _old("b"),
        "integration": _old("integration", depends_on=["a", "b"]),
    }
    patch = plan_revision.revise(old, [_item("a"), _item("b")], approved_work=True)
    assert patch["integration"]["status"] == "delivered"
    assert patch["integration"]["prd_recheck"] is True
    assert "a" not in patch
    assert "b" not in patch


def test_the_integration_waits_when_one_dependency_changed():
    old = {
        "a": _old("a"),
        "b": _old("b"),
        "integration": _old("integration", depends_on=["a", "b"]),
    }
    patch = plan_revision.revise(old, [_item("a"), _item("b", brief="new")], approved_work=True)
    assert patch["b"]["status"] == "pending"
    assert patch["integration"]["status"] == "pending"
    assert "prd_recheck" not in patch["integration"]
    assert patch["integration"]["reviewed_head"] == "h-integration"


def test_a_pending_integration_stays_out_of_the_patch_when_a_dependency_changed():
    old = {
        "a": _old("a"),
        "integration": _old("integration", status="pending", depends_on=["a"]),
    }
    assert "integration" not in plan_revision.revise(
        old, [_item("a", brief="new")], approved_work=True
    )


def test_several_subtasks_without_an_integration_have_no_final_unit():
    old = {"a": _old("a"), "b": _old("b")}
    assert plan_revision.revise(old, [_item("a"), _item("b")], approved_work=True) == {}
