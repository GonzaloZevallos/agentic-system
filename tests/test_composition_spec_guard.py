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
from agentic.core.process.domain import spec_delivery
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
