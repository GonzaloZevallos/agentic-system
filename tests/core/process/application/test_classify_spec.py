"""The spec intake in `classify` (GH-594, part 4b-1b): a `Spec:` line in the issue body is read
at the upstream commit, validated and recorded, or refused before PjM runs. Every case goes
through `classify` over the memory ports; what changes between a refusal and its fix is an input
`classify` reads: the spec file, the upstream sha or `owner_login`."""

import pytest

from agentic.core.process.application.classify import classify, spec_intake
from agentic.core.process.application.ports import Upstream
from agentic.core.process.domain import rules, task, task_path
from agentic.shared.configuration import Configuration
from tests.fakes import (
    FakeSpecReader,
    FakeSpecValidator,
    MemoryWorkspace,
    ScriptedRunner,
    classified,
    memory_ports,
)

pytestmark = pytest.mark.anyio

PATH = "docs/specs/export.md"
SHA = "a" * 40
NEW_SHA = "b" * 40
BODY = f"Users want CSV.\n\nSpec: {PATH}#csv\n"
STATE = {"id": "T-1", "title": "Export report", "description": BODY}


def contract(commit: str = SHA) -> dict:
    return {
        "path": PATH,
        "slice": "csv",
        "commit": commit,
        "sha256": "c" * 64,
        "criteria": ["a CSV file downloads"],
        "screens": [{"name": "Export", "copy": ["Download CSV"]}],
        "risks": [],
        "touches_ui": True,
        "touches_copy": True,
    }


def build(*, result="spec_invalid", texts=None, pjm=None, upstream=None):
    runner = ScriptedRunner(pjm=pjm if pjm is not None else [classified()])
    reader = FakeSpecReader({(SHA, PATH): "text"} if texts is None else texts)
    validator = FakeSpecValidator(result)
    workspace = MemoryWorkspace()
    workspace.upstream_answer = upstream or Upstream("origin/main", SHA, None)
    ports = memory_ports(runner, workspace=workspace, spec_reader=reader, spec_validator=validator)
    return ports, runner, reader, validator


def cfg_of(tmp_path, owner="owner"):
    return Configuration(product_dir=tmp_path, owner_login=owner)


def pjm_calls(runner):
    return runner.slugs.count("pjm")


# (id, body, owner_login, upstream, reader has the file, validator result, cause, detail)
REFUSALS = [
    (
        "malformed",
        "Spec: nope.txt\n",
        "owner",
        None,
        True,
        "spec_invalid",
        "spec_line_malformed",
        [],
    ),
    ("unreadable", BODY, "owner", None, False, "spec_invalid", "spec_unreadable", ["missing"]),
    (
        "noted",
        BODY,
        "owner",
        Upstream("HEAD", SHA, "fetch failed: secret-host"),
        True,
        "spec_invalid",
        "spec_unreadable",
        ["unreachable"],
    ),
    (
        "no_sha",
        BODY,
        "owner",
        Upstream("HEAD", None, None),
        True,
        "spec_invalid",
        "spec_unreadable",
        ["unreachable"],
    ),
    ("invalid", BODY, "owner", None, True, "spec_invalid", "spec_invalid", [PATH]),
    ("unapproved", BODY, "owner", None, True, "spec_unapproved", "spec_unapproved", [PATH]),
    ("no_owner", BODY, "", None, True, "spec_invalid", "spec_unapproved", ["owner_login"]),
    ("slice_unknown", BODY, "owner", None, True, "slice_unknown", "slice_unknown", [PATH]),
    ("slice_missing", BODY, "owner", None, True, "slice_missing", "slice_missing", [PATH]),
]


@pytest.mark.parametrize(
    ("body", "owner", "upstream", "has_file", "result", "cause", "detail"),
    [r[1:] for r in REFUSALS],
    ids=[r[0] for r in REFUSALS],
)
async def test_a_refused_spec_escalates_as_classification_without_running_pjm(
    tmp_path, body, owner, upstream, has_file, result, cause, detail
):
    ports, runner, _, _ = build(result=result, texts=None if has_file else {}, upstream=upstream)

    patch = await classify({**STATE, "description": body}, cfg_of(tmp_path, owner), ports)

    assert patch["escalation_class"] == "classification"
    assert patch["spec_refusal"] == {"cause": cause, "detail": detail}
    assert pjm_calls(runner) == 0
    assert "spec" not in patch and "type" not in patch
    # A fixed sentence: neither the note's text nor the body's reaches the reason.
    assert "secret-host" not in patch["escalated"] and "nope.txt" not in patch["escalated"]
    assert ports.documents.load("T-1")["spec_refusal"]["cause"] == cause


async def test_a_refusal_pause_is_a_humans(tmp_path):
    ports, _, _, _ = build(result="spec_unapproved")

    patch = await classify(STATE, cfg_of(tmp_path), ports)

    assert rules.pause_kind(patch["pause_log"]) == "human"


@pytest.mark.parametrize("typed", [("bug", "high"), ("platform_enhancement", "high")])
async def test_a_spec_on_a_task_typed_otherwise_escalates_and_keeps_the_pjm_run(tmp_path, typed):
    ports, runner, _, _ = build(result=contract(), pjm=[classified(*typed)])

    patch = await classify(STATE, cfg_of(tmp_path), ports)

    assert patch["escalation_class"] == "classification"
    assert patch["spec_refusal"] == {"cause": "spec_type", "detail": [PATH]}
    assert pjm_calls(runner) == 1
    assert patch["runs"] and "PjM" in patch["sections"]
    decision = task_path.decide("feature", "small", None, None, None)
    dropped = {"type", "size", "spec", "flags", *task_path.classification_patch(decision, None)}
    assert dropped.isdisjoint(patch)
    paused = task.apply(STATE, patch)
    resumed = task.apply(paused, rules.resumed(paused, None, at="2026-11-06T12:30:00+00:00"))
    assert rules.resume_target(resumed) == "classify"


async def test_a_low_confidence_platform_enhancement_is_a_feature_and_keeps_its_spec(tmp_path):
    ports, _, _, _ = build(result=contract(), pjm=[classified("platform_enhancement", "low")])

    patch = await classify(STATE, cfg_of(tmp_path), ports)

    assert patch["type"] == "feature"
    assert patch["spec"]["commit"] == SHA and "escalated" not in patch


async def test_a_feature_with_a_valid_spec_records_it_beside_pjms_keys(tmp_path):
    ports, runner, reader, validator = build(result=contract())

    patch = await classify(STATE, cfg_of(tmp_path, "zeta"), ports)

    assert patch["type"] == "feature"
    assert patch["spec"] == contract()
    assert patch["flags"] == {"touches_ui": True, "touches_copy": True}
    keys = task_path.classification_patch(task_path.decide("feature", None, None, None, None), None)
    assert set(keys) <= set(patch)
    assert pjm_calls(runner) == 1
    assert reader.calls == [(SHA, PATH)]
    assert validator.calls == [("text", PATH, SHA, "csv", "zeta")]


async def test_a_refusal_is_fixed_by_what_classify_reads_on_the_resume(tmp_path):
    ports, runner, reader, validator = build(result="spec_unapproved")
    cfg = cfg_of(tmp_path)

    refused = await classify(STATE, cfg, ports)
    assert refused["spec_refusal"]["cause"] == "spec_unapproved"
    paused = task.apply(STATE, refused)
    resumed = task.apply(paused, rules.resumed(paused, None, at="2026-11-06T12:30:00+00:00"))
    assert resumed["spec_refusal"] is None

    # The spec is approved now and the upstream moved: the file is read at the new commit.
    validator.result = contract(NEW_SHA)
    reader.texts[(NEW_SHA, PATH)] = "approved text"
    ports.workspace.upstream_answer = Upstream("origin/main", NEW_SHA, None)
    fixed = await classify(resumed, cfg, ports)

    assert fixed["spec"]["commit"] == NEW_SHA
    assert reader.calls[-1] == (NEW_SHA, PATH)
    assert validator.calls[-1][2] == NEW_SHA
    assert fixed["type"] == "feature"
    assert task.apply(resumed, fixed)["spec_refusal"] is None


async def test_a_fixed_owner_login_is_read_on_the_resume(tmp_path):
    ports, _, _, validator = build(result=contract())

    refused = await classify(STATE, cfg_of(tmp_path, ""), ports)
    fixed = await classify(STATE, cfg_of(tmp_path, "zeta"), ports)

    assert refused["spec_refusal"]["detail"] == ["owner_login"]
    assert fixed["spec"]["commit"] == SHA
    assert validator.calls[-1][4] == "zeta"


@pytest.mark.parametrize("which", ["no_validator", "no_ports"])
async def test_without_both_ports_a_spec_line_is_not_looked_at(tmp_path, which):
    plain = {"id": "T-1", "title": "Export report", "description": "Users want CSV.\n"}
    reader = FakeSpecReader({(SHA, PATH): "text"})
    ports, _, _, _ = build(result=contract())
    ports_off = memory_ports(
        ScriptedRunner(pjm=[classified()]),
        workspace=ports.workspace,
        spec_reader=reader if which == "no_validator" else None,
        spec_validator=None,
    )
    baseline = memory_ports(ScriptedRunner(pjm=[classified()]), workspace=ports.workspace)

    with_line = await classify(STATE, cfg_of(tmp_path), ports_off)
    without = await classify(plain, cfg_of(tmp_path), baseline)

    # The run record holds the prompt, which carries the body: everything else is equal.
    assert {k: v for k, v in with_line.items() if k != "runs"} == {
        k: v for k, v in without.items() if k != "runs"
    }
    assert ports.workspace.upstream_calls == 0 and reader.calls == []
    assert spec_intake(STATE, cfg_of(tmp_path), ports_off) is None
