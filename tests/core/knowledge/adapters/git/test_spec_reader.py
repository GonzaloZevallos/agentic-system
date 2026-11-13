"""`GitSpecReader` on a real repository, and the `SpecReader` contract it shares with its fake."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple, get_args

import pytest

from agentic.core.knowledge.adapters.git import spec_reader
from agentic.core.knowledge.adapters.git.spec_reader import (
    SPEC_GIT_TIMEOUT_S,
    GitSpecReader,
    _entries,
    _Entry,
    _git_env,
    _run,
)
from agentic.core.process.application.ports import SpecReader, SpecUnreadable
from agentic.core.process.domain.spec_delivery import SpecReadRefusal
from tests.fakes import FakeSpecReader

PATH = "docs/specs/a.md"


def git(repo: Path, *args: str, stdin: bytes | None = None) -> str:
    """Git for the fixture, deaf to the machine's own configuration."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return (
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "tag.gpgsign=false",
                "-c",
                "init.defaultBranch=master",
                *args,
            ],
            cwd=repo,
            input=stdin,
            capture_output=True,
            env=env,
            check=True,
        )
        .stdout.decode()
        .strip()
    )


def commit_file(repo: Path, content: bytes, message: str = "c", path: str = PATH) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    return tmp_path


def refusal(reader: SpecReader, commit: str, path: str = PATH) -> SpecUnreadable:
    with pytest.raises(SpecUnreadable) as caught:
        reader.read(commit, path)
    return caught.value


def test_an_older_commit_returns_its_own_bytes(repo: Path) -> None:
    old = commit_file(repo, b"old\n")
    commit_file(repo, b"new\n")
    (repo / PATH).write_bytes(b"dirty\n")
    assert GitSpecReader(repo).read(old, PATH) == "old\n"


def test_a_deleted_working_file_is_still_read(repo: Path) -> None:
    head = commit_file(repo, b"kept\n")
    (repo / PATH).unlink()
    assert GitSpecReader(repo).read(head, PATH) == "kept\n"


def test_an_untracked_file_is_missing(repo: Path) -> None:
    head = commit_file(repo, b"x\n", path="README.md")
    (repo / PATH).parent.mkdir(parents=True)
    (repo / PATH).write_bytes(b"untracked\n")
    assert refusal(GitSpecReader(repo), head).reason == "missing"


def test_a_symlink_is_not_a_blob(repo: Path) -> None:
    (repo / "docs/specs").mkdir(parents=True)
    (repo / PATH).symlink_to("/etc/passwd")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "link")
    head = git(repo, "rev-parse", "HEAD")
    assert refusal(GitSpecReader(repo), head).reason == "not_blob"


def test_a_gitlink_is_not_a_blob(repo: Path) -> None:
    head = commit_file(repo, b"x\n", path="README.md")
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},{PATH}")
    git(repo, "commit", "-q", "-m", "gitlink")
    assert refusal(GitSpecReader(repo), git(repo, "rev-parse", "HEAD")).reason == "not_blob"


def test_a_directory_is_not_a_blob(repo: Path) -> None:
    head = commit_file(repo, b"x\n", path=f"{PATH}/inner.txt")
    assert refusal(GitSpecReader(repo), head).reason == "not_blob"


def test_an_executable_file_is_readable(repo: Path) -> None:
    (repo / "docs/specs").mkdir(parents=True)
    (repo / PATH).write_bytes(b"run\n")
    (repo / PATH).chmod(0o755)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "x")
    assert git(repo, "ls-tree", "HEAD", "--", PATH).startswith("100755")
    assert GitSpecReader(repo).read(git(repo, "rev-parse", "HEAD"), PATH) == "run\n"


def test_the_size_limit_is_exact(repo: Path) -> None:
    exact = commit_file(repo, b"a" * 65536)
    over = commit_file(repo, b"a" * 65537)
    reader = GitSpecReader(repo)
    assert len(reader.read(exact, PATH)) == 65536
    assert refusal(reader, over).reason == "oversize"


def test_invalid_utf8_is_an_encoding_refusal(repo: Path) -> None:
    head = commit_file(repo, b"caf\xe9\n")
    assert refusal(GitSpecReader(repo), head).reason == "encoding"


def test_an_id_that_is_not_a_commit_of_the_repo_is_refused(repo: Path) -> None:
    head = commit_file(repo, b"x\n")
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    git(repo, "tag", "-a", "-m", "t", "v1")
    tag = git(repo, "rev-parse", "v1")
    assert tag != head
    reader = GitSpecReader(repo)
    for bad in ("f" * 40, head[:12], head.upper(), "HEAD", "-x", tree, tag):
        assert refusal(reader, bad).reason == "commit", bad


def test_a_replaced_blob_still_reads_the_original(repo: Path) -> None:
    head = commit_file(repo, b"original\n")
    blob = git(repo, "rev-parse", f"{head}:{PATH}")
    other = git(repo, "hash-object", "-w", "--stdin", stdin=b"forged\n")
    git(repo, "replace", blob, other)
    assert git(repo, "cat-file", "blob", blob) == "forged"
    assert GitSpecReader(repo).read(head, PATH) == "original\n"


def test_an_inherited_git_dir_is_ignored(
    repo: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = commit_file(repo, b"mine\n")
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    git(elsewhere, "init", "-q")
    commit_file(elsewhere, b"theirs\n")
    monkeypatch.setenv("GIT_DIR", str(elsewhere / ".git"))
    assert GitSpecReader(repo).read(head, PATH) == "mine\n"


def test_a_sha256_repository_reads_a_64_hex_id(tmp_path: Path) -> None:
    try:
        git(tmp_path, "init", "-q", "--object-format=sha256")
    except subprocess.CalledProcessError:
        pytest.skip("this git cannot make a sha256 repository (before 2.29)")
    head = commit_file(tmp_path, b"wide\n")
    assert len(head) == 64
    assert GitSpecReader(tmp_path).read(head, PATH) == "wide\n"


def test_a_64_hex_id_on_a_sha1_repository_is_a_commit_refusal(repo: Path) -> None:
    commit_file(repo, b"x\n")
    assert refusal(GitSpecReader(repo), "a" * 64).reason == "commit"


def test_a_directory_that_is_no_repository_is_a_git_refusal(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    exc = refusal(GitSpecReader(empty), "a" * 40)
    assert exc.reason == "git"


class _Done(NamedTuple):
    returncode: int
    stdout: bytes
    stderr: bytes = b""


def test_a_blob_that_outgrows_what_ls_tree_said_is_oversize(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = "b" * 40

    def run(argv: list[str], cwd: Path) -> Any:
        if argv[1] == "rev-parse":
            return _Done(0, b"a" * 40 + b"\n")
        if argv[1] == "ls-tree":
            return _Done(0, f"100644 blob {sha} 65536\t{PATH}\0".encode())
        return _Done(0, b"a" * 65537)

    monkeypatch.setattr(spec_reader, "_run", run)
    assert refusal(GitSpecReader(repo), "a" * 40).reason == "oversize"


@pytest.mark.parametrize(
    "path",
    [
        "..",
        "docs/specs/../x.md",
        "/docs/specs/a.md",
        "docs\\specs\\a.md",
        "docs/specs/a\x00.md",
        "docs/specs/a.md\n",
        "docs/specs/a\x1f.md",
        "docs/specs/s/a.md",
        "docs/specs/a.txt",
        "docs/other/a.md",
        "docs/specs/.md",
        "docs/specs/.hidden.md",
        "docs/specs/" + "a" * 253 + ".md",
    ],
)
def test_a_refused_path_starts_no_subprocess(
    repo: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("a subprocess started")

    monkeypatch.setattr(spec_reader, "_run", fail)
    monkeypatch.setattr(subprocess, "run", fail)
    assert refusal(GitSpecReader(repo), "a" * 40, path).reason == "path"
    assert refusal(GitSpecReader(repo), "HEAD").reason == "commit"


def test_run_passes_the_hardening_to_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def fake(argv: list[str], **kwargs: Any) -> _Done:
        seen.update(kwargs, argv=argv)
        return _Done(0, b"")

    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    monkeypatch.setattr(subprocess, "run", fake)
    _run(["git", "status"], tmp_path)
    assert seen["argv"] == ["git", "status"]
    assert seen["shell"] is False
    assert seen["cwd"] == tmp_path
    assert seen["timeout"] == SPEC_GIT_TIMEOUT_S
    assert seen["env"] == _git_env(os.environ)
    assert "GIT_DIR" not in seen["env"]


@pytest.mark.parametrize(
    "error", [subprocess.TimeoutExpired(["git"], 1.0), FileNotFoundError("git")]
)
def test_run_maps_a_timeout_and_an_oserror_to_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def fake(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(SpecUnreadable) as caught:
        _run(["git", "status"], tmp_path)
    assert caught.value.reason == "git"
    assert str(caught.value) == "git"


def test_run_returns_a_non_zero_exit_code_to_its_caller(tmp_path: Path) -> None:
    assert _run(["git", "rev-parse", "--verify", "--quiet", "nope"], tmp_path).returncode != 0


def test_entries_tolerate_a_padded_size() -> None:
    sha = "a" * 40
    assert _entries(f"100644 blob {sha}      12\t{PATH}\0".encode()) == [
        _Entry("100644", "blob", sha, 12)
    ]


def test_entries_read_a_dash_for_a_tree_and_a_gitlink() -> None:
    sha = "a" * 40
    raw = f"040000 tree {sha}       -\tdocs/specs/a.md\x00160000 commit {sha}       -\tx\0".encode()
    assert _entries(raw) == [
        _Entry("040000", "tree", sha, None),
        _Entry("160000", "commit", sha, None),
    ]


def test_entries_of_nothing_is_empty() -> None:
    assert _entries(b"") == []


def test_a_malformed_record_is_a_git_refusal() -> None:
    with pytest.raises(SpecUnreadable) as caught:
        _entries(b"garbage\0")
    assert caught.value.reason == "git"


def test_git_env_scrubs_the_git_variables_and_pins_four() -> None:
    env = _git_env(
        {
            "GIT_DIR": "/x",
            "GIT_INDEX_FILE": "/y",
            "GIT_NO_REPLACE_OBJECTS": "0",
            "PATH": "/bin",
            "TMPDIR": "/t",
        }
    )
    assert env == {
        "PATH": "/bin",
        "TMPDIR": "/t",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }


# The shared contract: a known (commit, path) reads its text; an unknown one raises
# `SpecUnreadable` whose cause, message and reason are the port's. The fake's own reason is not
# asserted: it has none of the adapter's authority.
def _fake(repo: Path) -> tuple[SpecReader, str]:
    return FakeSpecReader({("c" * 40, PATH): "text\n"}), "c" * 40


def _git_reader(repo: Path) -> tuple[SpecReader, str]:
    return GitSpecReader(repo), commit_file(repo, b"text\n")


@pytest.mark.parametrize("build", [_fake, _git_reader])
def test_the_contract_of_a_known_and_an_unknown_spec(
    repo: Path, build: Callable[[Path], tuple[SpecReader, str]]
) -> None:
    reader, commit = build(repo)
    assert reader.read(commit, PATH) == "text\n"
    for other_commit, other_path in ((commit, "docs/specs/b.md"), ("d" * 40, PATH)):
        exc = refusal(reader, other_commit, other_path)
        assert exc.cause == "spec_unreadable"
        assert str(exc) == exc.reason
        assert exc.reason in get_args(SpecReadRefusal)
