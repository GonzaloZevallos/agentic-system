"""`Tracker` over the `gh` CLI in the product checkout (ADR-0011): the pull request
of a task, and the comments and labels of its issue, which a resume also reads.

A task id `GH-<n>` came from issue n; any other id has no issue, and writing to it
is a no-op. `gh` resolves the repo from the checkout, as the inbox does.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ...application.ports import (
    BranchPullRequest,
    GhCallLog,
    HeldListMayBeCut,
    IssueComment,
    IssueState,
    ListedPullRequest,
    MergeCallFailed,
    TrackerCallFailed,
    TrackerRateLimited,
    TrackerRefused,
    TrackerTurnRefused,
    TrackerUnreachable,
    tracker_failure_class,
)
from ...domain.ci import CiCheck, CiReport, CiUnreadable, DispatchedRun
from ...domain.github_access import GithubAccess, NotAnHttpResponse, split_response
from ...domain.hold import HELD_LIMIT, HOLD_LABEL
from ...domain.presubmit import ApiBreakLine
from ...domain.task import (
    PullRequest,
    PullRequestCommit,
    TrackerFailure,
    TrackerTarget,
)
from ...domain.tracker_refs import issue_number, issue_target, pull_request_number
from .run import GhRetry, RateLimitLatch, bounded_run, print_line, run_gh

# The order a merge method is picked in: the first the repository allows.
_MERGE_METHODS = (
    ("merge", "mergeCommitAllowed"),
    ("squash", "squashMergeAllowed"),
    ("rebase", "rebaseMergeAllowed"),
)


def _bool_or_none(value: object) -> bool | None:
    """A real JSON boolean as it is; anything else (missing, null, a string) is unknown."""
    return value if isinstance(value, bool) else None


# `pull_requests_on` asks for one page of this size; a full page may be a truncated one.
_BRANCH_PAGE = 50
_PR_STATES = ("OPEN", "CLOSED", "MERGED")

# The conclusions of a workflow run `rerun_failed_ci` asks `gh` to re-run.
_FAILED_RUNS = ("failure", "timed_out", "startup_failure")

Run = Callable[..., subprocess.CompletedProcess[str]]

# `gh pr view --json commits` carries no `additions`/`deletions` per commit (the gate
# spike this subtask ran, see its section); this GraphQL query is the declared
# fallback, behind the same `pull_request` verb, so no consumer learns which ran.
# `{owner}`/`{repo}` are `gh`'s own magic fields, resolved from `cwd`, which is why
# `_gh` always runs with `cwd=product_dir` -- never the human's own checkout branch.
# `parents(first: 1) { totalCount }` is what lets `metrics._is_human_commit` tell a
# merge commit apart from an ordinary one by its parent count, never by its headline
# (GH-136): GraphQL exposes it per commit, one page of one is enough since only the
# count is read.
# GitHub's `mergeable` values `PullRequest` carries (GH-326); any other reads as `None`.
_MERGEABLE: dict[object, Literal["MERGEABLE", "CONFLICTING", "UNKNOWN"]] = {
    "MERGEABLE": "MERGEABLE",
    "CONFLICTING": "CONFLICTING",
    "UNKNOWN": "UNKNOWN",
}

_PULL_REQUEST_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      state
      createdAt
      mergedAt
      mergeCommit { oid }
      headRefOid
      baseRefOid
      mergeable
      commits(first: 250) {
        nodes {
          commit {
            messageHeadline
            additions
            deletions
            authors(first: 10) { nodes { email } }
            parents(first: 1) { totalCount }
          }
        }
      }
    }
  }
}
"""

# The CI of a commit, read with its checks in the one query so the report's oid is the
# commit its checks belong to (GH-216, invariant 2). Check runs and commit statuses are
# both read; `__typename` tells them apart, and `pageInfo` says when the first hundred are
# not all of them. `{owner}`/`{repo}` are `gh`'s own magic fields, as in the query above.
_ROLLUP = """
statusCheckRollup {
  contexts(first: 100) {
    pageInfo { hasNextPage }
    nodes {
      __typename
      ... on CheckRun { id name status conclusion detailsUrl startedAt completedAt }
      ... on StatusContext { context state targetUrl }
    }
  }
}
"""

# The workflow runs of the same commit (GH-485): a suite with a `workflowRun` is a GitHub Actions
# one; the other apps' suites carry none. Read with the checks so they describe the same commit.
_SUITES = """
checkSuites(first: 50) {
  pageInfo { hasNextPage }
  nodes { status conclusion workflowRun { databaseId } }
}
"""

# The same suites with how many check runs each has (GH-601): `first: 0` fetches no rows, only the
# count, so a queued suite with none -- a ghost run, once it has stayed so -- can be told from one
# that has not reported yet. Only `_WATCHED_BRANCH_CI_QUERY` uses it.
_WATCHED_SUITES = """
checkSuites(first: 50) {
  pageInfo { hasNextPage }
  nodes { status conclusion workflowRun { databaseId } checkRuns(first: 0) { totalCount } }
}
"""

# The same fragments for the three readers that follow the pages (GH-1151), with the `endCursor` of
# each connection. `COMMIT_CI_QUERY` keeps the plain ones: the stage0 window reads one page, and
# says so when there is more. A first page is the plain query; the pages after it are `_page_after`.
_PAGED_ROLLUP = _ROLLUP.replace("pageInfo { hasNextPage }", "pageInfo { hasNextPage endCursor }")
_PAGED_SUITES = _SUITES.replace("pageInfo { hasNextPage }", "pageInfo { hasNextPage endCursor }")
_PAGED_WATCHED_SUITES = _WATCHED_SUITES.replace(
    "pageInfo { hasNextPage }", "pageInfo { hasNextPage endCursor }"
)


def _page_after(query: str) -> str:
    """`query` asking for the page after a cursor: its `contexts` and `checkSuites` take
    `$contextsAfter` and `$suitesAfter` (a null one is the first page)."""
    return (
        query.replace("query($", "query($contextsAfter: String, $suitesAfter: String, $", 1)
        .replace("contexts(first: 100)", "contexts(first: 100, after: $contextsAfter)")
        .replace("checkSuites(first: 50)", "checkSuites(first: 50, after: $suitesAfter)")
    )


_PULL_REQUEST_CI_QUERY = (
    """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      createdAt
      baseRefName
      headRefOid
      commits(last: 1) { nodes { commit { oid """
    + _PAGED_ROLLUP
    + _PAGED_SUITES
    + """ } } }
    }
  }
}
"""
)

_BRANCH_CI_QUERY = (
    """
query($owner: String!, $repo: String!, $ref: String!) {
  repository(owner: $owner, name: $repo) {
    ref(qualifiedName: $ref) { target { ... on Commit { oid """
    + _PAGED_ROLLUP
    + _PAGED_SUITES
    + """ } } }
  }
}
"""
)

# The CI of one commit by its oid, for the stage0 window's reader (GH-704): the same rollup and
# suites as a branch read, without `_WATCHED_SUITES`: the window does not look for ghost runs.
COMMIT_CI_QUERY = (
    """
query($owner: String!, $repo: String!, $oid: GitObjectID!) {
  repository(owner: $owner, name: $repo) {
    object(oid: $oid) { ... on Commit { oid """
    + _ROLLUP
    + _SUITES
    + """ } }
  }
}
"""
)

_WATCHED_BRANCH_CI_QUERY = (
    """
query($owner: String!, $repo: String!, $ref: String!) {
  repository(owner: $owner, name: $repo) {
    ref(qualifiedName: $ref) { target { ... on Commit { oid """
    + _PAGED_ROLLUP
    + _PAGED_WATCHED_SUITES
    + """ } } }
  }
}
"""
)

# A `StatusContext` has no `status`: a state that has not decided is `PENDING` and the rest
# are `COMPLETED`, so a consumer reads both kinds of check the same way.
_UNDECIDED_STATES = ("PENDING", "EXPECTED")


# The steps of one check run (GH-610), read only for the product's `api_break_line` check when it
# is red: the one place a step's name tells that the pull request body, not the code, failed it.
_STEPS_QUERY = """
query($id: ID!) {
  node(id: $id) {
    ... on CheckRun {
      steps(first: 100) { nodes { name conclusion } pageInfo { hasNextPage } }
    }
  }
}
"""

_RED = frozenset({"FAILURE", "TIMED_OUT", "STARTUP_FAILURE"})


def _held_numbers(rows: object) -> list[int] | None:
    """The issue numbers of a `gh issue list --json number` page, or `None` when the page is
    not a list of objects each with an integer `number` (a `bool` is not one)."""
    if not isinstance(rows, list):
        return None
    numbers: list[int] = []
    for row in rows:
        number = row.get("number") if isinstance(row, dict) else None
        if not isinstance(number, int) or isinstance(number, bool):
            return None
        numbers.append(number)
    return numbers


def _failed_step_in(answer: dict) -> str | None:
    """The name of the first failing step of a `_STEPS_QUERY` answer. `None` when there is none, the
    list is cut at its first page without one, or the answer cannot be read: never a guess."""
    try:
        steps = answer["data"]["node"]["steps"]
        names = [
            node["name"]
            for node in steps["nodes"]
            if str(node.get("conclusion", "")).upper() == "FAILURE"
        ]
        if not names and steps["pageInfo"]["hasNextPage"]:
            return None
    except (KeyError, TypeError, AttributeError):
        return None
    return names[0] if names and isinstance(names[0], str) else None


def _check_run_ids(commit: object) -> dict[str, str]:
    """Check name -> the GraphQL id of its check run, for the runs of `commit` that give one."""
    if not isinstance(commit, dict):
        return {}
    try:
        nodes = commit["statusCheckRollup"]["contexts"]["nodes"]
        return {
            node["name"]: node["id"]
            for node in nodes
            if node.get("__typename") == "CheckRun" and isinstance(node.get("id"), str)
        }
    except (KeyError, TypeError):
        return {}


class _Unreadable(Exception):
    """Inside this module only: a CI answer that is not whole. Never leaves it."""


def _why(exc: Exception) -> str:
    """Our own account of why an answer could not be read, for `CiUnreadable.of`: the message of
    an `_Unreadable`, else the kind of error and what it names (a missing key, a null)."""
    return str(exc) if isinstance(exc, _Unreadable) else f"{type(exc).__name__}: {exc}"


# How many pages of each connection of a commit's CI one read follows (GH-1151). Past the bound the
# read is unreadable and names it: never a partial list used as whole. Each call asks for the next
# page of every connection still open, so a poll is at most `max(5, 3)` calls more than the first.
_CONTEXT_PAGES = 5
_SUITE_PAGES = 3


def _connection(commit: object, name: str) -> dict | None:
    """The `contexts` (under `statusCheckRollup`) or `checkSuites` connection of a GraphQL
    `Commit`, `None` when the commit does not carry one."""
    holder = (
        commit.get("statusCheckRollup")
        if name == "contexts" and isinstance(commit, dict)
        else commit
    )
    connection = holder.get(name) if isinstance(holder, dict) else None
    return connection if isinstance(connection, dict) else None


def _next_cursor(connection: dict | None) -> str | None:
    """The `endCursor` of the page after this one, `None` when the connection is whole or absent, or
    says there is more and gives no cursor: it cannot be followed, and `_commit_ci` (a cut list of
    checks) or `_runs_completed` (a cut list of suites) already treat it as not whole."""
    page = connection.get("pageInfo") if connection is not None else None
    if not isinstance(page, dict) or page.get("hasNextPage") is not True:
        return None
    cursor = page.get("endCursor")
    return cursor if isinstance(cursor, str) and cursor else None


def _says_more(connection: dict | None) -> bool:
    """Whether the connection says a page follows (`hasNextPage` true), cursor or not."""
    page = connection.get("pageInfo") if connection is not None else None
    return isinstance(page, dict) and page.get("hasNextPage") is True


def _page_nodes(connection: dict | None) -> list:
    nodes = connection.get("nodes") if connection is not None else None
    if not isinstance(nodes, list):
        raise _Unreadable("a page without a node list")
    return nodes


def _whole(commit: dict, nodes: dict[str, list]) -> dict:
    """`commit` with the connections that were followed replaced by their pages merged, each
    marked whole (`hasNextPage` false): what `_commit_ci` and the suite readers expect."""
    merged = dict(commit)
    done = {"hasNextPage": False}
    rollup = commit.get("statusCheckRollup")
    if "contexts" in nodes and isinstance(rollup, dict):
        contexts = {**rollup["contexts"], "nodes": nodes["contexts"], "pageInfo": done}
        merged["statusCheckRollup"] = {**rollup, "contexts": contexts}
    if "checkSuites" in nodes:
        merged["checkSuites"] = {
            **commit["checkSuites"],
            "nodes": nodes["checkSuites"],
            "pageInfo": done,
        }
    return merged


def _commit_ci(commit: object) -> tuple[str, tuple[CiCheck, ...]]:
    """The oid of a GraphQL `Commit` and its checks; `_Unreadable` for anything missing,
    mistyped or cut short (a commit that carries no rollup is a readable, empty one)."""
    if not isinstance(commit, dict):
        raise _Unreadable("no commit")
    oid = commit.get("oid")
    if not isinstance(oid, str) or not oid:
        raise _Unreadable("no commit oid")
    rollup = commit.get("statusCheckRollup")
    if rollup is None:
        return oid, ()
    contexts = rollup["contexts"]
    if contexts["pageInfo"]["hasNextPage"]:
        raise _Unreadable("more checks than one page")
    return oid, tuple(check for node in contexts["nodes"] if (check := _ci_check(node)) is not None)


def _runs_completed(commit: object) -> bool | None:
    """Whether every GitHub Actions run of the commit is completed (GH-485): True only when
    there is at least one suite with a `workflowRun`, every such suite is `COMPLETED` and the
    list is whole (`hasNextPage` false). Anything else, a missing or mistyped field included, is
    None: not known, so a wait and never an unreadable CI."""
    if not isinstance(commit, dict):
        return None
    suites = commit.get("checkSuites")
    if not isinstance(suites, dict):
        return None
    page, nodes = suites.get("pageInfo"), suites.get("nodes")
    if not isinstance(page, dict) or page.get("hasNextPage") is not False:
        return None
    if not isinstance(nodes, list) or not all(isinstance(node, dict) for node in nodes):
        return None
    actions = [node for node in nodes if node.get("workflowRun")]
    if not actions:
        return None
    return True if all(node.get("status") == "COMPLETED" for node in actions) else None


def _suite_run_ids(commit: object) -> tuple[frozenset[str], frozenset[str]]:
    """`(cancelled_runs, running_runs)` of a commit's check suites (GH-539): the Actions run ids
    whose suite concluded `CANCELLED`, and those whose suite is not `COMPLETED`. Best effort: a
    missing or mistyped field, a suite with no readable run id included, adds nothing and is never
    an unreadable CI, so the verdict stays what it was without the information."""
    suites = commit.get("checkSuites") if isinstance(commit, dict) else None
    nodes = suites.get("nodes") if isinstance(suites, dict) else None
    if not isinstance(nodes, list):
        return frozenset(), frozenset()
    cancelled: set[str] = set()
    running: set[str] = set()
    for node in nodes:
        workflow_run = node.get("workflowRun") if isinstance(node, dict) else None
        run_id = workflow_run.get("databaseId") if isinstance(workflow_run, dict) else None
        if not isinstance(run_id, int) or isinstance(run_id, bool):
            continue
        if node.get("conclusion") == "CANCELLED":
            cancelled.add(str(run_id))
        status = node.get("status")
        if isinstance(status, str) and status != "COMPLETED":
            running.add(str(run_id))
    return frozenset(cancelled), frozenset(running)


def commit_report(target: object) -> CiReport:
    """The `CiReport` of a GraphQL `Commit` object, with no pull request: `base_ref`, `base_sha`
    and `opened_at` are None. The one place a branch read and a by-sha read build it. Raises
    `_Unreadable`, `KeyError` or `TypeError` for an answer that is not whole: the caller maps it."""
    head_sha, checks = _commit_ci(target)
    cancelled_runs, running_runs = _suite_run_ids(target)
    return CiReport(
        head_sha=head_sha,
        base_ref=None,
        base_sha=None,
        opened_at=None,
        checks=checks,
        runs_completed=_runs_completed(target),
        cancelled_runs=cancelled_runs,
        running_runs=running_runs,
    )


def _queued_empty_runs(commit: object) -> frozenset[str]:
    """The Actions run ids of a commit whose check suite is `QUEUED` with zero check runs
    (GH-601). Best effort, like `_suite_run_ids`: a missing or mistyped field adds nothing and is
    never an unreadable CI. A suites page that is cut (`hasNextPage` true) lists none: a suite
    outside the page could be the live one, so no read of a cut page makes or extends a ghost."""
    suites = commit.get("checkSuites") if isinstance(commit, dict) else None
    nodes = suites.get("nodes") if isinstance(suites, dict) else None
    if not isinstance(nodes, list):
        return frozenset()
    page = suites.get("pageInfo") if isinstance(suites, dict) else None
    if isinstance(page, dict) and page.get("hasNextPage") is True:
        return frozenset()
    found: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict) or node.get("status") != "QUEUED":
            continue
        workflow_run = node.get("workflowRun")
        run_id = workflow_run.get("databaseId") if isinstance(workflow_run, dict) else None
        check_runs = node.get("checkRuns")
        total = check_runs.get("totalCount") if isinstance(check_runs, dict) else None
        if isinstance(run_id, int) and not isinstance(run_id, bool):
            if isinstance(total, int) and not isinstance(total, bool) and total == 0:
                found.add(str(run_id))
    return frozenset(found)


def _ci_check(node: dict) -> CiCheck | None:
    kind = node["__typename"]
    if kind == "CheckRun":
        return CiCheck(
            name=_text(node["name"]),
            status=_text(node["status"]),
            conclusion=node.get("conclusion"),
            link=node.get("detailsUrl") or "",
            started_at=_instant(node.get("startedAt")),
            completed_at=_instant(node.get("completedAt")),
        )
    if kind == "StatusContext":
        state = _text(node["state"])
        return CiCheck(
            name=_text(node["context"]),
            status="PENDING" if state in _UNDECIDED_STATES else "COMPLETED",
            conclusion=state,
            link=node.get("targetUrl") or "",
        )
    return None


def _instant(value: object) -> str | None:
    """A time GitHub gave (GH-467), as it gave it; `None` when it gave none: absent, null, not a
    string, or `gh run view`'s zero value (`0001-01-01...`) for a job that has not finished.
    Never an error: a missing time only leaves the CI wait unknown."""
    if not isinstance(value, str) or not value or value.startswith("0001-"):
        return None
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise _Unreadable("a check field is not a non-empty string")
    return value


def _dispatched_job(job: object) -> CiCheck:
    """A job of `gh run view --json jobs` as a check: its enums upper-cased as GraphQL gives
    them, an empty conclusion (a job still running) as `None`."""
    if not isinstance(job, dict):
        raise _Unreadable("a job is not an object")
    conclusion = job.get("conclusion")
    return CiCheck(
        name=_text(job["name"]),
        status=_text(job["status"]).upper(),
        conclusion=conclusion.upper() if isinstance(conclusion, str) and conclusion else None,
        link=job.get("url") or "",
        failed_step=_failed_step_of(job.get("steps")),
        started_at=_instant(job.get("startedAt")),
        completed_at=_instant(job.get("completedAt")),
    )


def _failed_step_of(steps: object) -> str | None:
    """The name of the first step of a job whose conclusion is failure (GH-504; `CiCheck`
    derives its stage with `ci.failed_stage`). `None` when there is no failing step or `steps`
    cannot be read: the job then reads red as before, and is never made unreadable by its steps."""
    if not isinstance(steps, list):
        return None
    for step in steps:
        if not isinstance(step, dict) or str(step.get("conclusion", "")).lower() != "failure":
            continue
        name = step.get("name")
        return name if isinstance(name, str) else None
    return None


def _comment_author(comment: dict) -> str:
    """The login of a comment's author: `author.login` in `gh issue view`, `user.login` in
    the REST shape; empty when neither names one (a deleted account is `null`)."""
    for key in ("author", "user"):
        who = comment.get(key)
        if isinstance(who, dict) and isinstance(who.get("login"), str):
            return who["login"]
    return ""


def _concatenated_pages(raw: str) -> list[dict]:
    """The items of what `gh api --paginate` prints without `--slurp`: one JSON array per page,
    one after the other. All or nothing: a value that is cut, trailing text, a value that is not
    a list of objects, or no value at all raises `ValueError`; a shorter thread never returns.
    An explicit `[]` is the only empty thread."""
    decoder = json.JSONDecoder()
    items: list[dict] = []
    end, seen = 0, False
    while True:
        while end < len(raw) and raw[end].isspace():
            end += 1
        if end == len(raw):
            break
        try:
            page, end = decoder.raw_decode(raw, end)
        except ValueError as exc:
            raise ValueError(f"gh api answered unreadable comment pages: {raw[:200]!r}") from exc
        if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
            raise ValueError(f"gh api answered a page that is not a list of objects: {raw[:200]!r}")
        items.extend(page)
        seen = True
    if not seen:
        raise ValueError("gh api answered no comment page")
    return items


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _label_names(raw: object) -> tuple[str, ...] | None:
    """The label names of `gh issue view --json labels`; `None` when the field is absent or
    any entry is not a named label, so an unreadable set is never taken for an empty one."""
    if not isinstance(raw, list):
        return None
    names: list[str] = []
    for item in raw:
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str):
            return None
        names.append(name)
    return tuple(names)


def _blocked_by(raw: dict | None) -> tuple[int, ...] | None:
    """`None` when `gh`'s own `blockedBy` field is absent from the response at all --
    the signal `IssueState.blocked_by`'s own docstring names -- never read apart from
    "exposed, with zero blockers", which is `{"nodes": [], "totalCount": 0}`."""
    if raw is None:
        return None
    return tuple(node["number"] for node in raw.get("nodes", []) if "number" in node)


def rest_argv(
    method: str,
    path: str,
    raw_fields: Sequence[tuple[str, str]] = (),
    body_file: str | None = None,
) -> tuple[str, ...]:
    """The `gh` arguments of one REST write (GH-1089): `api -i -X <method> <path>`, each text
    field as `-f key=value` (always raw: `@file`, `true` and `123` stay the text they are) and
    the body only as `-F body=@<file>`, which reads the file and sends it as it is. No other
    value is ever a `-F`."""
    args = ["api", "-i", "-X", method, path]
    for key, value in raw_fields:
        args += ["-f", f"{key}={value}"]
    if body_file is not None:
        args += ["-F", f"body=@{body_file}"]
    return tuple(args)


@contextlib.contextmanager
def _body_file(text: str, *, newline: str | None = None) -> Iterator[str]:
    """`text` in a temp file for one call, removed when it ends: a body is never in argv,
    whose slots a multibyte body near GitHub's bound would outgrow (ADR-0034)."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8", newline=newline
    ) as handle:
        handle.write(text)
        path = handle.name
    try:
        yield path
    finally:
        Path(path).unlink(missing_ok=True)


_PR_CREATE_FAILED = "gh pr create failed:"


def _as_pr_create_failure(exc: ValueError, head: str) -> ValueError:
    """`exc` as the same class with a message that starts `gh pr create failed:` and ends
    with the head: what `run_gh` says (`gh pr create timed out after 60 s`) goes after the
    prefix, with its own `gh pr create ` dropped. Status and reset are kept."""
    text = str(exc).removeprefix("gh pr create ")
    message = f"{_PR_CREATE_FAILED} {text}{head}"
    if isinstance(exc, TrackerRateLimited):
        return TrackerRateLimited(message, status=exc.status, resets_at=exc.resets_at)
    if isinstance(exc, TrackerCallFailed | TrackerRefused):
        return type(exc)(message, status=exc.status)
    return ValueError(message)


def _unreadable(call: str, detail: str) -> ValueError:
    return ValueError(f"gh {call} failed: unreadable response; the write may have applied{detail}")


class GhTracker:
    def __init__(
        self,
        product_dir: Path,
        run: Run = bounded_run,
        log: Callable[[str], None] = print_line,
        retry: GhRetry | None = None,
        now: Callable[[], str] = _utc_now,
        api_break_line: Callable[[], ApiBreakLine | None] = lambda: None,
        latch: RateLimitLatch | None = None,
        access: GithubAccess | None = None,
        calls: GhCallLog | None = None,
    ):
        self._calls = calls
        self.product_dir = product_dir
        self._latch = latch if latch is not None else RateLimitLatch()
        # The product's `api_break_line` (GH-610), asked only when a check is red.
        self._api_break_line = api_break_line
        self._now = now
        self._run = run
        self._log = log
        self._retry = retry
        self._access = access

    def open_pull_request(
        self, task_id: str, branch: str, title: str, body: str, *, closes_issue: bool = True
    ) -> str:
        if closes_issue and (number := issue_number(task_id)) is not None:
            body = f"Closes #{number}\n\n{body}"
        # Every failure starts `gh pr create failed:` and names the head (GH-1089): the ones
        # built here already do; `run_gh`'s own (timeout, unreachable, retries spent, turn
        # refused, no `gh`) are re-raised as the same class with that wording.
        head = f" (head {branch})"
        try:
            return self._create_pull_request(branch, title, body, head)
        except (TrackerCallFailed, TrackerRefused, ValueError) as exc:
            if str(exc).startswith(_PR_CREATE_FAILED):
                raise
            raise _as_pr_create_failure(exc, head) from (exc.__cause__ or exc)

    def _create_pull_request(self, branch: str, title: str, body: str, head: str) -> str:
        # The base is the default branch, as `gh pr create` took it. A failed read ends the
        # call here: the POST is never attempted without a base.
        base = self._gh_read(
            "api",
            "repos/{owner}/{repo}",
            "--jq",
            ".default_branch",
            call="pr create",
            failed=lambda stderr: f"gh pr create failed: could not read the default branch{head}",
        )
        if not base:
            raise ValueError(f"gh pr create failed: could not read the default branch{head}")
        # Not a draft: the human merges it as it is (README.md, 9), or does not. Not retried:
        # a second POST after a lost answer could open a second pull request.
        with _body_file(body) as path:
            response = self._rest(
                "POST",
                "repos/{owner}/{repo}/pulls",
                (("title", title), ("head", branch), ("base", base)),
                path,
                call="pr create",
                retrying=False,
                expect=201,
                detail=head,
            )
        try:
            url = json.loads(response)["html_url"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise _unreadable("pr create", head) from exc
        if not isinstance(url, str):
            raise _unreadable("pr create", head)
        return url

    def open_issue(
        self,
        title: str,
        body: str,
        *,
        label: str | None = None,
        labels: Sequence[str] = (),
    ) -> str:
        # `--body-file`, never `--body`: the one limit that can bite is GitHub's own,
        # never argv's (ADR-0034). The temp file is this call's alone; nothing reads it
        # back.
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
            handle.write(body)
            path = handle.name
        merged = dict.fromkeys(name for name in (label, *labels) if name)
        extra = tuple(part for name in merged for part in ("--label", name))
        try:
            return self._gh("issue", "create", "--title", title, "--body-file", path, *extra)
        finally:
            Path(path).unlink(missing_ok=True)

    def comment_on_issue(self, reference: str, text: str) -> None:
        # The body goes as a file, as `open_issue`: the text carries ids from product output,
        # and the one limit that can bite is GitHub's own, never argv's (ADR-0034).
        target = issue_target(reference)
        with _body_file(text) as path:
            if target is None:
                # Not a reference this module can build a path from: the subcommand, as before.
                self._gh_retrying("issue", "comment", reference, "--body-file", path)
                return
            self._rest(
                "POST",
                f"{target}/comments",
                (),
                path,
                call="issue comment",
                retrying=True,
                expect=201,
            )

    def update_issue_body(self, number: int, body: str) -> None:
        # An empty body is refused before any call: GitHub would take it as the new body.
        if not body.strip():
            raise ValueError(f"refusing to write an empty body to issue #{number}")
        # `newline=""`: the body goes out byte for byte, CRLF lines included.
        with _body_file(body, newline="") as path:
            self._rest(
                "PATCH",
                f"repos/{{owner}}/{{repo}}/issues/{number}",
                (),
                path,
                call="issue edit",
                retrying=True,
                expect=200,
            )

    def rerun_failed_ci(self, reference: str, head: str) -> list[str]:
        pull_request_number(reference)  # rejected before any `gh` call, as `merge` does
        runs = self._json_list(
            self._gh("run", "list", "--commit", head, "--json", "url,conclusion", "--limit", "100"),
            "gh run list",
        )
        failed = [
            str(run["url"])
            for run in runs
            if isinstance(run, dict) and run.get("conclusion") in _FAILED_RUNS and run.get("url")
        ]
        for url in failed:
            self._gh("run", "rerun", url.rsplit("/", 1)[-1], "--failed")
        return failed

    def close_pull_request(self, reference: str) -> None:
        self._gh("pr", "close", str(pull_request_number(reference)))

    def reopen_pull_request(self, number: int) -> None:
        # `gh pr reopen` on an open pull request prints "already open" and exits 0, which
        # is the no-op; no retry, so a refusal (merged, base gone) reaches the caller once.
        self._gh("pr", "reopen", str(number))

    def changed_files(self, pull_request: str) -> int:
        raw = self._gh_read(
            "pr", "view", str(pull_request_number(pull_request)), "--json", "changedFiles"
        )
        try:
            count = json.loads(raw)["changedFiles"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError(f"gh pr view answered no readable changedFiles: {raw[:80]!r}") from exc
        if not isinstance(count, int) or isinstance(count, bool):
            raise ValueError(f"gh pr view answered a non-integer changedFiles: {count!r}")
        return count

    @staticmethod
    def _json_list(raw: str, what: str) -> list[object]:
        try:
            items = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{what} answered unreadable JSON") from exc
        if not isinstance(items, list):
            raise ValueError(f"{what} did not answer a list")
        return items

    def comment(self, task_id: str, text: str) -> None:
        if (number := issue_number(task_id)) is not None:
            with _body_file(text) as path:
                self._rest(
                    "POST",
                    f"repos/{{owner}}/{{repo}}/issues/{number}/comments",
                    (),
                    path,
                    call="issue comment",
                    retrying=True,
                    expect=201,
                )

    def note(self, task_id: str, text: str) -> TrackerFailure | None:
        return self._best_effort("comment", "note", lambda: self.comment(task_id, text))

    def drop_label(self, task_id: str, label: str) -> TrackerFailure | None:
        return self._best_effort("label", "drop_label", lambda: self.unlabel(task_id, label))

    def put_label(self, task_id: str, label: str) -> TrackerFailure | None:
        return self._best_effort("label", "put_label", lambda: self.label(task_id, label))

    def _best_effort(
        self, target: TrackerTarget, name: str, write: Callable[[], None]
    ) -> TrackerFailure | None:
        """Runs `write`, the same `gh` call and retry path as the strict verb, and returns
        the failure of a GitHub error as a closed class. Anything else, a missing `gh`
        included, raises as the strict verb does."""
        try:
            write()
        except ValueError as exc:
            cls = tracker_failure_class(exc)
            if cls is None:
                raise
            self._log(f"agentic: {name} could not write the {target} ({cls}); the run goes on")
            return TrackerFailure(target=target, cls=cls, at=self._now())
        return None

    def close_issue(self, task_id: str, *, comment: str | None = None) -> None:
        if (number := issue_number(task_id)) is not None:
            extra = ["--comment", comment] if comment is not None else []
            self._gh("issue", "close", str(number), "--reason", "completed", *extra)

    def label(self, task_id: str, label: str) -> None:
        if (number := issue_number(task_id)) is not None:
            self._gh_retrying("issue", "edit", str(number), "--add-label", label)

    def unlabel(self, task_id: str, label: str) -> None:
        if (number := issue_number(task_id)) is not None:
            self._gh_retrying("issue", "edit", str(number), "--remove-label", label)

    def ensure_label(self, name: str) -> None:
        # Never `--search`: its index is eventually consistent (ADR-0034), so the whole
        # list is asked and the name is compared here. No `--force`: an existing label is
        # not ours to edit, and nothing here reads `stderr` to decide.
        raw = self._gh_read("label", "list", "--limit", "1000", "--json", "name")
        existing: set[str] = set()
        for item in self._json_list(raw, "gh label list"):
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise ValueError("gh label list answered an entry without a name")
            existing.add(item["name"])
        if name not in existing:
            self._gh_retrying("label", "create", name)

    def list_pull_requests(self) -> list[ListedPullRequest]:
        raw = self._gh_read(
            "pr",
            "list",
            "--state",
            "all",
            "--json",
            "number,url,state,body,mergeCommit,headRefName,isCrossRepository",
            "--limit",
            "500",
        )
        return [
            ListedPullRequest(
                number=item["number"],
                url=item["url"],
                state=item["state"],
                body=item.get("body") or "",
                merge_commit=(item.get("mergeCommit") or {}).get("oid") or "",
                branch=item.get("headRefName") or "",
                cross_repository=_bool_or_none(item.get("isCrossRepository")),
            )
            for item in json.loads(raw)
        ]

    def pull_requests_on(self, branch: str) -> list[BranchPullRequest]:
        raw = self._gh_read(
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "all",
            "--json",
            "number,state,url",
            "--limit",
            str(_BRANCH_PAGE),
        )
        try:
            items = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"gh pr list on {branch} answered unreadable JSON") from exc
        if not isinstance(items, list):
            raise ValueError(f"gh pr list on {branch} did not answer a list")
        if len(items) >= _BRANCH_PAGE:
            raise ValueError(f"gh pr list on {branch} filled its page of {_BRANCH_PAGE}: truncated")
        found: list[BranchPullRequest] = []
        for item in items:
            row = item if isinstance(item, dict) else {}
            number, state, url = row.get("number"), row.get("state"), row.get("url")
            if (
                not isinstance(number, int)
                or isinstance(number, bool)
                or not isinstance(url, str)
                or state not in _PR_STATES
            ):
                raise ValueError(f"gh pr list on {branch} answered an unusable row: {item!r}")
            found.append(BranchPullRequest(number=number, state=state, url=url))
        return found

    def update_pull_request_body(self, number: int, body: str) -> None:
        # `--body-file`, as `open_issue`: the limit that can bite is GitHub's own.
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
            handle.write(body)
            path = handle.name
        try:
            self._gh_retrying("pr", "edit", str(number), "--body-file", path)
        finally:
            Path(path).unlink(missing_ok=True)

    def pull_request_body(self, reference: str) -> str:
        raw = self._gh_read("pr", "view", str(pull_request_number(reference)), "--json", "body")
        try:
            body = json.loads(raw)["body"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"gh pr view answered something unreadable: {raw[:200]!r}") from exc
        if not isinstance(body, str):
            raise ValueError(f"gh pr view answered a body that is not text: {raw[:200]!r}")
        return body

    def issue_comments(self, task_id: str) -> list[str]:
        return [c.body for c in self.timestamped_comments(task_id)]

    def timestamped_comments(self, task_id: str) -> list[IssueComment]:
        """The thread of the task's issue, oldest first, read over REST: one `gh api
        --paginate`, whose pages are read to the end or the read raises (`_concatenated_pages`)."""
        if (number := issue_number(task_id)) is None:
            return []
        raw = self._gh_read(
            "api",
            "--paginate",
            f"repos/{{owner}}/{{repo}}/issues/{number}/comments?per_page=100",
            call="issue comments",
        )
        return [
            IssueComment(
                body=c.get("body") or "",
                created_at=c.get("created_at") or c.get("createdAt") or "",
                author=_comment_author(c),
                url=c.get("html_url") or c.get("url") or "",
            )
            for c in _concatenated_pages(raw)
        ]

    def issue_created_at(self, task_id: str) -> str | None:
        if (number := issue_number(task_id)) is None:
            return None
        raw = json.loads(self._gh_read("issue", "view", str(number), "--json", "createdAt"))
        created = raw.get("createdAt") if isinstance(raw, dict) else None
        return created if isinstance(created, str) and created else None

    def queue(self) -> list[int]:
        raw = self._gh_read(
            "issue",
            "list",
            "--label",
            "agentic",
            "--state",
            "open",
            "--json",
            "number",
            "--limit",
            "500",
        )
        return sorted(item["number"] for item in json.loads(raw))

    def held_issues(self) -> frozenset[int]:
        try:
            raw = self._gh_read(
                "issue",
                "list",
                "--label",
                HOLD_LABEL,
                "--state",
                "open",
                "--json",
                "number",
                "--limit",
                str(HELD_LIMIT),
            )
        except TrackerCallFailed:
            raise  # a stall or an unreachable GitHub keeps its own class
        except ValueError as exc:  # `gh` failed: its stderr stays out of the message
            raise TrackerCallFailed("gh issue list (held) failed") from exc
        try:
            rows = json.loads(raw)
        except ValueError as exc:
            raise TrackerCallFailed("gh issue list (held) answered unparsable JSON") from exc
        numbers = _held_numbers(rows)
        if numbers is None:
            raise TrackerCallFailed("gh issue list (held) answered an unexpected shape")
        if len(rows) == HELD_LIMIT:  # rows counted before deduplication
            raise HeldListMayBeCut(
                "gh issue list (held) returned a full page: the held list may be cut"
            )
        return frozenset(numbers)

    def issue_state(self, number: int) -> IssueState:
        fields = "state,stateReason,blockedBy,closedByPullRequestsReferences,labels"
        try:
            raw = self._gh_read("issue", "view", str(number), "--json", fields)
        except TrackerCallFailed:
            # A stall or an unreachable GitHub is not an old `gh`: never fall back, never
            # `blocked_by=None`.
            raise
        except ValueError:
            # An older `gh` that does not expose the native dependency at all: fall
            # back to every other field, `blocked_by=None` tells the caller to read
            # the issue body's own `Blocked by:` line instead.
            raw = self._gh_read(
                "issue",
                "view",
                str(number),
                "--json",
                "state,stateReason,closedByPullRequestsReferences,labels",
            )
        data = json.loads(raw)
        closing_merge_commit = None
        for reference in data.get("closedByPullRequestsReferences") or []:
            pull_request = self.pull_request(reference["url"])
            closing_merge_commit = pull_request.merge_commit if pull_request else None
            break  # the first one is enough: an issue closes once
        return IssueState(
            state=data["state"],
            state_reason=data.get("stateReason") or "",
            blocked_by=_blocked_by(data.get("blockedBy")),
            closing_merge_commit=closing_merge_commit,
            labels=_label_names(data.get("labels")),
        )

    def pull_request(self, reference: str) -> PullRequest | None:
        number = pull_request_number(reference)
        raw = self._gh_read(
            "api",
            "graphql",
            "-f",
            f"query={_PULL_REQUEST_QUERY}",
            "-F",
            "owner={owner}",
            "-F",
            "repo={repo}",
            "-F",
            f"number={number}",
        )
        pull_request = json.loads(raw)["data"]["repository"]["pullRequest"]
        commits = tuple(
            PullRequestCommit(
                author_emails=tuple(a["email"] for a in node["commit"]["authors"]["nodes"]),
                message_headline=node["commit"]["messageHeadline"],
                additions=node["commit"]["additions"],
                deletions=node["commit"]["deletions"],
                parent_count=node["commit"]["parents"]["totalCount"],
            )
            for node in pull_request["commits"]["nodes"]
        )
        return PullRequest(
            state=pull_request["state"],
            created_at=pull_request["createdAt"],
            merged_at=pull_request["mergedAt"],
            merge_commit=(pull_request.get("mergeCommit") or {}).get("oid"),
            head_oid=pull_request.get("headRefOid") or None,
            mergeable=_MERGEABLE.get(pull_request.get("mergeable")),
            base_oid=pull_request.get("baseRefOid") or None,
            commits=commits,
        )

    def ci_report(self, reference: str) -> CiReport | CiUnreadable:
        """See `Tracker.ci_report`. `gh` resolves the repository from `product_dir`. Never
        raises. It quotes what `gh` said (`CiUnreadable.of`) and never classifies by it: the
        class of an unreadable answer is the step that failed (ADR-0029). `base_sha` is always None:
        the gate that would have filled it from the Actions runs failed (ADR-0045, GH-216
        amendment), `pull_requests` being empty on every run of this product's CI."""
        try:
            number = pull_request_number(reference)
        except ValueError:
            return CiUnreadable("gh_unreadable")
        read = self._paged_commit(
            _PULL_REQUEST_CI_QUERY,
            ("-F", "owner={owner}", "-F", "repo={repo}", "-F", f"number={number}"),
            lambda answer: answer["data"]["repository"]["pullRequest"]["commits"]["nodes"][0][
                "commit"
            ],
        )
        if isinstance(read, CiUnreadable):
            return read
        answer, commit = read
        try:
            pull_request = answer["data"]["repository"]["pullRequest"]
            head_sha, checks = _commit_ci(commit)
            checks = self._with_failed_step(checks, commit)
            cancelled_runs, running_runs = _suite_run_ids(commit)
            return CiReport(
                head_sha=head_sha,
                base_ref=_text(pull_request["baseRefName"]),
                base_sha=None,
                opened_at=_text(pull_request["createdAt"]),
                checks=checks,
                runs_completed=_runs_completed(commit),
                cancelled_runs=cancelled_runs,
                running_runs=running_runs,
            )
        except (_Unreadable, KeyError, IndexError, TypeError) as exc:
            return CiUnreadable.of("gh_unreadable", _why(exc))

    def _with_failed_step(self, checks: tuple[CiCheck, ...], commit: object) -> tuple[CiCheck, ...]:
        """`checks` with `failed_step` filled on the red runs of the product's `api_break_line`
        check (GH-610), read from the check API's steps, never from a log. No other check is asked
        and nothing is asked without the declaration; an unreadable answer leaves `None`."""
        declared = self._api_break_line()
        if declared is None:
            return checks
        ids = _check_run_ids(commit)
        out: list[CiCheck] = []
        for check in checks:
            run_id = ids.get(check.name)
            if check.name != declared.check or check.conclusion not in _RED or run_id is None:
                out.append(check)
                continue
            answer = self.graphql(_STEPS_QUERY, ("-F", f"id={run_id}"))
            step = None if isinstance(answer, CiUnreadable) else _failed_step_in(answer)
            out.append(replace(check, failed_step=step))
        return tuple(out)

    def branch_ci(self, branch: str) -> CiReport | CiUnreadable:
        """See `Tracker.ci_report`: the latest commit of `branch`, and no pull request."""
        read = self._read_branch(_BRANCH_CI_QUERY, branch)
        return read if isinstance(read, CiUnreadable) else read[0]

    def watched_branch_ci(self, branch: str) -> CiReport | CiUnreadable:
        """See `Tracker.watched_branch_ci`: `branch_ci` with the queued suites that have no
        check run (GH-601)."""
        read = self._read_branch(_WATCHED_BRANCH_CI_QUERY, branch)
        if isinstance(read, CiUnreadable):
            return read
        report, target = read
        return replace(report, queued_empty_runs=_queued_empty_runs(target))

    def _read_branch(self, query: str, branch: str) -> tuple[CiReport, object] | CiUnreadable:
        """The latest commit of `branch` read with `query`: its report and the GraphQL commit it
        was built from, so a caller can read more off the same answer."""
        read = self._paged_commit(
            query,
            ("-F", "owner={owner}", "-F", "repo={repo}", "-f", f"ref=refs/heads/{branch}"),
            lambda answer: answer["data"]["repository"]["ref"]["target"],
        )
        if isinstance(read, CiUnreadable):
            return read
        target = read[1]
        try:
            report = commit_report(target)
        except (_Unreadable, KeyError, TypeError) as exc:
            return CiUnreadable.of("gh_unreadable", _why(exc))
        return report, target

    def _paged_commit(
        self, query: str, fields: tuple[str, ...], locate: Callable[[dict], object]
    ) -> tuple[dict, object] | CiUnreadable:
        """One commit's CI read page by page (GH-1151): the first answer, and the commit `locate`
        finds in it with the `contexts` and `checkSuites` pages merged and marked whole. Each
        connection is followed only while its `hasNextPage` is true, up to `_CONTEXT_PAGES` and
        `_SUITE_PAGES` pages; one call per page asks for the next page of every connection that is
        still open. Past a bound, or when a page is of another commit, cannot be read or gives no
        cursor, the read is unreadable: never the pages read so far. A whole first page is the
        one call it always was and its commit is returned untouched. Every poll starts at page one.
        """
        answer = self.graphql(query, fields)
        if isinstance(answer, CiUnreadable):
            return answer
        try:
            commit = locate(answer)
            variables = {"contextsAfter": "contexts", "suitesAfter": "checkSuites"}
            bounds = {"contexts": _CONTEXT_PAGES, "checkSuites": _SUITE_PAGES}
            cursors = {name: _next_cursor(_connection(commit, name)) for name in variables.values()}
            nodes = {
                name: list(_page_nodes(_connection(commit, name)))
                for name, cursor in cursors.items()
                if cursor is not None
            }
            pages = dict.fromkeys(nodes, 1)
            while any(cursor is not None for cursor in cursors.values()):
                for name in nodes:
                    if cursors[name] is not None and pages[name] >= bounds[name]:
                        return CiUnreadable.of(
                            "gh_unreadable", f"more {name} than {bounds[name]} pages"
                        )
                after = tuple(
                    part
                    for variable, name in variables.items()
                    if cursors[name] is not None
                    for part in ("-f", f"{variable}={cursors[name]}")
                )
                page = self.graphql(_page_after(query), fields + after)
                if isinstance(page, CiUnreadable):
                    return page
                fetched = locate(page)
                if not isinstance(fetched, dict) or fetched.get("oid") != commit["oid"]:
                    return CiUnreadable.of(
                        "gh_unreadable", "the commit changed while its pages were read"
                    )
                for name in nodes:
                    if cursors[name] is None:
                        continue
                    connection = _connection(fetched, name)
                    nodes[name].extend(_page_nodes(connection))
                    pages[name] += 1
                    cursors[name] = _next_cursor(connection)
                    if cursors[name] is None and _says_more(connection):
                        return CiUnreadable.of(
                            "gh_unreadable", f"a page of {name} says more and gives no cursor"
                        )
        except (_Unreadable, KeyError, IndexError, TypeError) as exc:
            return CiUnreadable.of("gh_unreadable", _why(exc))
        return answer, (_whole(commit, nodes) if nodes and isinstance(commit, dict) else commit)

    def dispatch_ci(
        self, workflow: str, ref: str, inputs: Mapping[str, str]
    ) -> CiUnreadable | None:
        """See `Tracker.dispatch_ci`. Transient failures go through the write retries first
        (`_gh_retrying`); whatever is left is `dispatch_refused`, a timeout or an unreachable
        GitHub included (the caller escalates `ci` at once either way), except a `gh` that
        cannot be run at all."""
        flags = ("--ref", ref) if ref else ()
        fields = tuple(part for key, value in inputs.items() for part in ("-f", f"{key}={value}"))
        try:
            self._gh_retrying("workflow", "run", workflow, *flags, *fields)
        except ValueError as exc:  # `_gh`'s own failure: a refusal, or no `gh` at all
            missing = isinstance(exc.__cause__, FileNotFoundError)
            return CiUnreadable("gh_unavailable" if missing else "dispatch_refused")
        except OSError:  # a `gh` that cannot be run at all (not executable, no permission)
            return CiUnreadable("gh_unavailable")
        return None

    def rerun_ci_run(self, run_id: str) -> CiUnreadable | None:
        """See `Tracker.rerun_ci_run`: `gh run rerun <id> --failed`, the one run the merge gate
        decided red, never a run listing and never the pull request's. A `run_id` that is not
        all digits is refused before any call: it would be read by `gh` as a flag or a url."""
        if not run_id.isascii() or not run_id.isdigit():
            return CiUnreadable("dispatch_refused")
        try:
            self._gh_retrying("run", "rerun", run_id, "--failed")
        except ValueError as exc:  # `_gh`'s own failure: a refusal, or no `gh` at all
            missing = isinstance(exc.__cause__, FileNotFoundError)
            return CiUnreadable("gh_unavailable" if missing else "dispatch_refused")
        except OSError:  # a `gh` that cannot be run at all (not executable, no permission)
            return CiUnreadable("gh_unavailable")
        return None

    def dispatched_ci(self, workflow: str, since: str) -> tuple[DispatchedRun, ...] | CiUnreadable:
        """See `Tracker.dispatched_ci`: the listing, then one `gh run view` per run for its
        jobs. `gh` writes statuses and conclusions in lower case; the run keeps `status` as
        it came and its jobs carry the upper-case enums every other `CiCheck` does."""
        listed = self._read_json(
            "run",
            "list",
            "--workflow",
            workflow,
            "--event",
            "workflow_dispatch",
            "--created",
            f">={since}",
            "--json",
            "databaseId,event,displayTitle,status,createdAt,url",
            "--limit",
            "50",
        )
        if isinstance(listed, CiUnreadable):
            return listed
        if not isinstance(listed, list):
            return CiUnreadable("gh_unreadable")
        runs: list[DispatchedRun] = []
        for item in listed:
            run = self._dispatched_run(item)
            if isinstance(run, CiUnreadable):
                return run
            runs.append(run)
        return tuple(runs)

    def _dispatched_run(self, item: object) -> DispatchedRun | CiUnreadable:
        if not isinstance(item, dict):
            return CiUnreadable("gh_unreadable")
        try:
            run_id = item["databaseId"]
            if not isinstance(run_id, int) or isinstance(run_id, bool):
                return CiUnreadable("gh_unreadable")
            fields = {
                key: _text(item[key])
                for key in ("event", "displayTitle", "status", "createdAt", "url")
            }
        except (KeyError, _Unreadable) as exc:
            return CiUnreadable.of("gh_unreadable", _why(exc))
        viewed = self._read_json("run", "view", str(run_id), "--json", "jobs")
        if isinstance(viewed, CiUnreadable):
            return viewed
        try:
            jobs = tuple(_dispatched_job(job) for job in viewed["jobs"])
        except (_Unreadable, KeyError, TypeError) as exc:
            return CiUnreadable.of("gh_unreadable", _why(exc))
        return DispatchedRun(
            run_id=run_id,
            event=fields["event"],
            display_title=fields["displayTitle"],
            status=fields["status"],
            created_at=fields["createdAt"],
            link=fields["url"],
            jobs=jobs,
        )

    def _read_json(self, *args: str) -> object | CiUnreadable:
        """One `gh` read, its parsed JSON or the class of its failure."""
        try:
            raw = self._gh_read(*args)
        except ValueError as exc:  # `_gh`'s own failure: the first line of its message is quoted
            missing = isinstance(exc.__cause__, FileNotFoundError)
            first_line = next(iter(str(exc).splitlines()), "")
            return CiUnreadable.of("gh_unavailable" if missing else "gh_unreadable", first_line)
        except OSError as exc:  # a `gh` that cannot be run at all (not executable, no permission)
            return CiUnreadable.of("gh_unavailable", str(exc))
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return CiUnreadable.of("gh_unreadable", raw)  # the class is the step that failed

    def graphql(self, query: str, fields: tuple[str, ...]) -> dict | CiUnreadable:
        """One `gh api graphql` call, its JSON object or the class of its failure."""
        answer = self._read_json("api", "graphql", "-f", f"query={query}", *fields)
        if isinstance(answer, dict | CiUnreadable):
            return answer
        return CiUnreadable.of("gh_unreadable", f"not a JSON object: {type(answer).__name__}")

    def merge(
        self, reference: str, head_sha: str, *, method: Literal["merge"] | None = None
    ) -> str:
        """With `method="merge"` only a merge commit will do: a repository that does not
        allow one raises `MergeCallFailed("no_merge_method")` and nothing is merged.
        Raises `MergeCallFailed`, its class chosen at the step that failed: never by
        reading `gh`'s stderr (ADR-0029), or `TrackerUnreachable` from the `repo view` read,
        before anything could change, or `TrackerTurnRefused` when the turn of `gh pr merge`
        was refused: `gh` never launched, so it is never `gh_unreadable`. The reference is parsed
        first, so an unreadable one reaches no `gh` call at all; `gh pr merge` gets the bare
        number and resolves the repository from `product_dir`, as `pull_request` does."""
        try:
            number = pull_request_number(reference)
        except ValueError as exc:
            raise MergeCallFailed("gh_unreadable", str(exc)) from exc
        try:
            allowed = json.loads(
                self._gh(
                    "repo",
                    "view",
                    "--json",
                    "mergeCommitAllowed,squashMergeAllowed,rebaseMergeAllowed",
                )
            )
        except TrackerUnreachable:
            raise  # a read, before any change: nothing merged, nothing to count
        except ValueError as exc:  # `_gh`'s own failure, or a `JSONDecodeError`
            missing = isinstance(exc.__cause__, FileNotFoundError)
            raise MergeCallFailed(
                "gh_unavailable" if missing else "gh_unreadable", str(exc)
            ) from exc
        if not isinstance(allowed, dict):
            raise MergeCallFailed(
                "gh_unreadable", "gh repo view answered something else than an object"
            )
        wanted = _MERGE_METHODS if method is None else _MERGE_METHODS[:1]
        chosen = next((name for name, key in wanted if allowed.get(key)), None)
        if chosen is None:
            raise MergeCallFailed("no_merge_method", "no_merge_method")
        # The head sha pins the merge to what the decision saw (GH-178); no `--auto` and no
        # `--delete-branch`: the poller's own `close` owns what happens after.
        try:
            self._gh("pr", "merge", str(number), f"--{chosen}", "--match-head-commit", head_sha)
        except TrackerTurnRefused:
            raise  # `gh` never launched: nothing merged, so not `gh_unreadable`
        except ValueError as exc:
            missing = isinstance(exc.__cause__, FileNotFoundError)
            if isinstance(exc, TrackerCallFailed):  # the merge may or may not have happened
                raise MergeCallFailed("gh_unreadable", str(exc)) from exc
            raise MergeCallFailed("gh_unavailable" if missing else "gh_rejected", str(exc)) from exc
        return chosen

    def _gh(self, *args: str) -> str:
        """No retry, whatever the policy: open_issue, merge, close_* and rerun_failed_ci call
        this one, because repeating them can duplicate or merge twice. `open_pull_request` has
        the same rule through `_rest(retrying=False)`."""
        return self._call(args, retry=None, retry_timeouts=False)

    def _call(
        self,
        args: tuple[str, ...],
        *,
        retry: GhRetry | None,
        retry_timeouts: bool,
        call: str | None = None,
        failed: Callable[[str], str] | None = None,
    ) -> str:
        label = call if call is not None else " ".join(args[:2])
        return run_gh(
            self._run,
            args,
            cwd=self.product_dir,
            log=self._log,
            not_installed="gh is not installed; delivering to GitHub needs the CLI",
            failed=failed or (lambda stderr: f"gh {label} failed: {stderr}"),
            latch=self._latch,
            retry=retry,
            retry_timeouts=retry_timeouts,
            access=self._access,
            call=call,
            calls=self._calls,
        )

    def _gh_read(
        self, *args: str, call: str | None = None, failed: Callable[[str], str] | None = None
    ) -> str:
        """A read: under a policy a timeout is retried too, since nothing changed."""
        return self._call(args, retry=self._retry, retry_timeouts=True, call=call, failed=failed)

    def _gh_retrying(self, *args: str) -> str:
        """A write that is safe to repeat (a comment, a label, a body): retried under the
        policy except on a timeout, which may have applied it. The only other door to `gh`
        besides `_gh`, so the verbs that create or merge are the ones that call `_gh`."""
        return self._call(args, retry=self._retry, retry_timeouts=False)

    def _rest(
        self,
        method: str,
        path: str,
        raw_fields: Sequence[tuple[str, str]],
        body_file: str | None,
        *,
        call: str,
        retrying: bool,
        expect: int,
        detail: str = "",
    ) -> str:
        """The one door of the writes moved to `gh api -i` (GH-1089): the body of the response
        when GitHub answered exactly `expect`. The call goes through `run_gh` as `call` (the
        subcommand it replaces, so logs and messages keep their wording) and, `retrying`, under
        the policy except on a timeout, as `_gh_retrying`; a write that creates is never
        retried. A failure is told by `gh`'s stderr alone, through `failed`; stdout is read only
        on success, by `split_response`. Any other status, 2xx included, or a stdout that is no
        response, raises a `ValueError` saying the write may have applied, outside the retry
        loop: the write is never repeated on it. `detail` ends every message."""
        raw = self._call(
            rest_argv(method, path, raw_fields, body_file),
            retry=self._retry if retrying else None,
            retry_timeouts=False,
            call=call,
            failed=lambda stderr: f"gh {call} failed: {stderr}{detail}",
        )
        try:
            response = split_response(raw)
        except NotAnHttpResponse as exc:
            raise _unreadable(call, detail) from exc
        if response.status != expect:
            raise ValueError(
                f"gh {call} failed: unexpected HTTP {response.status}; "
                f"the write may have applied{detail}"
            )
        return response.body
