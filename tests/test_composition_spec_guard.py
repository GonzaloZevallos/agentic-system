"""The spec route is on in production (GH-594, part 4b-4): the real composition root builds a
`GitSpecReader` for the product root and a `DomainSpecValidator`, so a `Spec:` line is looked at.
The tests run `spec_intake` over those real ports, on a real remote and clone."""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from agentic.composition import default_ports
from agentic.core.knowledge.adapters.git.spec_reader import GitSpecReader
from agentic.core.knowledge.adapters.spec_validator import DomainSpecValidator
from agentic.core.process.application.classify import spec_intake
from agentic.core.process.domain import spec_approval, spec_delivery
from agentic.core.process.domain.spec_delivery import (
    body_sha256,
    render_approved_front_matter,
    render_draft_front_matter,
)
from agentic.shared.configuration import Configuration

OWNER = "owner"
PATH = "docs/specs/x.md"
BODY = (
    "# Title\n\n## Goal\n\nDo the thing.\n\n## Acceptance criteria\n\n- It works.\n\n"
    "## Out of scope\n\n- Other things.\n\n## Decisions\n\n- Use X\n  - Reason: it is simple.\n\n"
    "## Screens\n\nnone\n\n## Risks\n\nSome risk.\n\n## Slices\n\n### one\n"
    "#### Acceptance criteria\n- One works.\n#### Risks\n- none\n\n"
)


def git(repo: Path, *args: str) -> str:
    """Git for the fixture, deaf to the machine's own configuration."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    argv = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    result = subprocess.run(
        [*argv, "-c", "init.defaultBranch=master", *args],
        cwd=repo,
        capture_output=True,
        env=env,
        check=True,
    )
    return result.stdout.decode().strip()


def approved_text() -> str:
    sha = body_sha256(render_draft_front_matter() + BODY)
    assert sha is not None
    return render_approved_front_matter(OWNER, "2026-11-12T10:00:00Z", sha) + BODY


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    """A clone of a bare remote, with an approved spec committed and pushed."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "-q", "--bare")
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q")
    git(work, "remote", "add", "origin", str(remote))
    (work / PATH).parent.mkdir(parents=True)
    (work / PATH).write_text(approved_text(), encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "spec")
    git(work, "push", "-q", "-u", "origin", "master")
    return work


def real_ports(product_dir: Path):
    return default_ports(Configuration(product_dir=product_dir, owner_login=OWNER))


def test_the_real_composition_root_builds_the_spec_ports(tmp_path):
    ports = real_ports(tmp_path)

    assert isinstance(ports.spec_reader, GitSpecReader)
    assert ports.spec_reader.product_dir == tmp_path
    assert isinstance(ports.spec_validator, DomainSpecValidator)
    assert spec_delivery.honoured(ports.spec_reader, ports.spec_validator) is True


def test_the_route_is_off_when_either_port_is_missing(tmp_path):
    ports = real_ports(tmp_path)

    assert spec_delivery.honoured(None, ports.spec_validator) is False
    assert spec_delivery.honoured(ports.spec_reader, None) is False


def test_spec_intake_over_the_real_ports_records_the_spec_at_the_upstream_commit(clone):
    ports = real_ports(clone)
    state = {"id": "T-1", "title": "t", "description": f"Goal.\n\nSpec: {PATH}\n"}

    patch = spec_intake(state, Configuration(product_dir=clone, owner_login=OWNER), ports)

    assert patch is not None and "escalated" not in patch
    assert patch["spec"]["path"] == PATH
    assert (
        patch["spec"]["commit"] == ports.workspace.upstream().sha == git(clone, "rev-parse", "HEAD")
    )
    assert patch["spec"]["sha256"] == body_sha256(approved_text())


def test_spec_intake_over_the_real_ports_refuses_a_spec_the_owner_did_not_approve(clone):
    ports = real_ports(clone)
    state = {"id": "T-1", "title": "t", "description": f"Spec: {PATH}\n"}

    patch = spec_intake(state, Configuration(product_dir=clone, owner_login="someone-else"), ports)

    assert patch is not None
    assert patch["spec_refusal"]["cause"] == "spec_unapproved"


def test_a_body_without_a_spec_line_reads_nothing(clone):
    ports = real_ports(clone)
    reads: list[tuple[str, str]] = []
    real = ports.spec_reader
    assert real is not None

    class Spy:
        def read(self, commit: str, path: str) -> str:
            reads.append((commit, path))
            return real.read(commit, path)

    spied = replace(ports, spec_reader=Spy())
    state = {"id": "T-1", "title": "t", "description": "A plain issue.\n\nNo spec here.\n"}

    patch = spec_intake(state, Configuration(product_dir=clone, owner_login=OWNER), spied)

    assert patch is None
    assert reads == []


def test_the_real_validator_calls_a_well_formed_draft_valid_and_a_malformed_one_not(tmp_path):
    """GH-594 part 5a: `draft_reading` over `DomainSpecValidator` on `draft_text`, the text the
    system writes: one slice and a Screens section of prose only pass, a body without the
    sections does not."""
    validator = default_ports(Configuration(product_dir=tmp_path, owner_login=OWNER)).spec_validator
    assert validator is not None
    commit = "a" * 40

    good = validator.validate(spec_delivery.draft_text(BODY), PATH, commit, None, OWNER)
    bad = validator.validate(
        spec_delivery.draft_text("# Title\n\nNo sections.\n"), PATH, commit, None, OWNER
    )

    assert spec_delivery.draft_reading(good, None).ok is True
    assert spec_delivery.draft_reading(bad, None).ok is False


def test_the_real_validator_gives_one_verdict_for_a_text_whatever_the_path(tmp_path):
    """GH-594 part 5c-1, invariant 5: a verdict on a draft never depends on the path; only
    `SpecContract.path` carries it. The draft and its approved preview are read under the preview
    path and under another valid path: same cause, or the same contract apart from `.path`."""
    validator = default_ports(Configuration(product_dir=tmp_path, owner_login=OWNER)).spec_validator
    assert validator is not None
    commit = "a" * 40
    other = "docs/specs/2026-11-17-another-name.md"
    assert spec_delivery.spec_path_ok(other)
    draft = spec_delivery.draft_text(BODY)
    texts = {
        "draft": draft,
        "approved": spec_approval.approve(draft, OWNER, "2026-11-17T10:00:00.000Z"),
        "malformed": spec_delivery.draft_text("# Title\n\nNo sections.\n"),
    }
    for name, text in texts.items():
        first = validator.validate(text, spec_delivery.DRAFT_PREVIEW_PATH, commit, None, OWNER)
        second = validator.validate(text, other, commit, None, OWNER)
        if isinstance(first, str):
            assert first == second, name
            continue
        assert isinstance(second, dict), name
        assert first["path"] == spec_delivery.DRAFT_PREVIEW_PATH and second["path"] == other
        assert {**first, "path": other} == second, name
    assert validator.validate(draft, other, commit, None, OWNER) == "spec_unapproved"
    assert validator.validate(texts["malformed"], other, commit, None, OWNER) == "spec_invalid"
    approved = validator.validate(texts["approved"], other, commit, None, OWNER)
    assert isinstance(approved, dict) and approved["path"] == other
