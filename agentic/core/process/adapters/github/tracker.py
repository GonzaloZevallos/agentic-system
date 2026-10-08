"""`Tracker` over the `gh` CLI in the product checkout (ADR-0011): the pull request
of a task, and the comments and labels of its issue, which a resume also reads.

A task id `GH-<n>` came from issue n; any other id has no issue, and writing to it
is a no-op. `gh` resolves the repo from the checkout, as the inbox does.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from ...application.ports import (
    BranchPullRequest,
    IssueComment,
    IssueState,
    ListedPullRequest,
    MergeCallFailed,
)
from ...domain.ci import CiCheck, CiReport, CiUnreadable
from ...domain.task import PullRequest, PullRequestCommit

_ISSUE = re.compile(r"^GH-(\d+)$")
_PULL_REQUEST_URL = re.compile(r"/pull/(\d+)$")

# The order a merge method is picked in: the first the repository allows.
_MERGE_METHODS = (
    ("merge", "mergeCommitAllowed"),
    ("squash", "squashMergeAllowed"),
    ("rebase", "rebaseMergeAllowed"),
)

# `pull_requests_on` asks for one page of this size; a full page may be a truncated one.
_BRANCH_PAGE = 50
_PR_STATES = ("OPEN", "CLOSED", "MERGED")

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
_PULL_REQUEST_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      state
      createdAt
      mergedAt
      mergeCommit { oid }
      headRefOid
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
      ... on CheckRun { name status conclusion detailsUrl }
      ... on StatusContext { context state targetUrl }
    }
  }
}
"""

_PULL_REQUEST_CI_QUERY = (
    """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      createdAt
      baseRefName
      headRefOid
      commits(last: 1) { nodes { commit { oid """
    + _ROLLUP
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
    + _ROLLUP
    + """ } } }
  }
}
"""
)

# A `StatusContext` has no `status`: a state that has not decided is `PENDING` and the rest
# are `COMPLETED`, so a consumer reads both kinds of check the same way.
_UNDECIDED_STATES = ("PENDING", "EXPECTED")


class _Unreadable(Exception):
    """Inside this module only: a CI answer that is not whole. Never leaves it."""


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


def _ci_check(node: dict) -> CiCheck | None:
    kind = node["__typename"]
    if kind == "CheckRun":
        return CiCheck(
            name=_text(node["name"]),
            status=_text(node["status"]),
            conclusion=node.get("conclusion"),
            link=node.get("detailsUrl") or "",
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


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise _Unreadable("a check field is not a non-empty string")
    return value


def issue_number(task_id: str) -> int | None:
    match = _ISSUE.match(task_id)
    return int(match.group(1)) if match else None


def _blocked_by(raw: dict | None) -> tuple[int, ...] | None:
    """`None` when `gh`'s own `blockedBy` field is absent from the response at all --
    the signal `IssueState.blocked_by`'s own docstring names -- never read apart from
    "exposed, with zero blockers", which is `{"nodes": [], "totalCount": 0}`."""
    if raw is None:
        return None
    return tuple(node["number"] for node in raw.get("nodes", []) if "number" in node)


def pull_request_number(url: str) -> int:
    """The number `gh` needs, off the URL `deliver` stored in the document."""
    match = _PULL_REQUEST_URL.search(url)
    if not match:
        raise ValueError(f"not a pull request URL: {url}")
    return int(match.group(1))


class GhTracker:
    def __init__(self, product_dir: Path, run: Run = subprocess.run):
        self.product_dir = product_dir
        self._run = run

    def open_pull_request(self, task_id: str, branch: str, title: str, body: str) -> str:
        if (number := issue_number(task_id)) is not None:
            body = f"Closes #{number}\n\n{body}"
        # `--body-file`, as `open_issue`: a multibyte body near the bound is larger than
        # one argv slot takes, and the limit that can bite is GitHub's own (ADR-0034).
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as h:
            h.write(body)
            path = h.name
        try:
            # Not a draft: the human merges it as it is (README.md, 9), or does not.
            return self._gh("pr", "create", "--head", branch, "--title", title, "--body-file", path)
        finally:
            Path(path).unlink(missing_ok=True)

    def open_issue(self, title: str, body: str) -> str:
        # `--body-file`, never `--body`: the one limit that can bite is GitHub's own,
        # never argv's (ADR-0034). The temp file is this call's alone; nothing reads it
        # back.
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
            handle.write(body)
            path = handle.name
        try:
            return self._gh("issue", "create", "--title", title, "--body-file", path)
        finally:
            Path(path).unlink(missing_ok=True)

    def comment(self, task_id: str, text: str) -> None:
        if (number := issue_number(task_id)) is not None:
            self._gh("issue", "comment", str(number), "--body", text)

    def label(self, task_id: str, label: str) -> None:
        if (number := issue_number(task_id)) is not None:
            self._gh("issue", "edit", str(number), "--add-label", label)

    def unlabel(self, task_id: str, label: str) -> None:
        if (number := issue_number(task_id)) is not None:
            self._gh("issue", "edit", str(number), "--remove-label", label)

    def list_pull_requests(self) -> list[ListedPullRequest]:
        raw = self._gh(
            "pr",
            "list",
            "--state",
            "all",
            "--json",
            "number,url,state,body,mergeCommit,headRefName",
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
            )
            for item in json.loads(raw)
        ]

    def pull_requests_on(self, branch: str) -> list[BranchPullRequest]:
        raw = self._gh(
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
            self._gh("pr", "edit", str(number), "--body-file", path)
        finally:
            Path(path).unlink(missing_ok=True)

    def issue_comments(self, task_id: str) -> list[str]:
        if (number := issue_number(task_id)) is None:
            return []
        comments = json.loads(self._gh("issue", "view", str(number), "--json", "comments"))
        return [c.get("body") or "" for c in comments.get("comments", [])]

    def timestamped_comments(self, task_id: str) -> list[IssueComment]:
        if (number := issue_number(task_id)) is None:
            return []
        raw = json.loads(self._gh("issue", "view", str(number), "--json", "comments"))
        return [
            IssueComment(body=c.get("body") or "", created_at=c.get("createdAt") or "")
            for c in raw.get("comments", [])
        ]

    def queue(self) -> list[int]:
        raw = self._gh(
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

    def issue_state(self, number: int) -> IssueState:
        fields = "state,stateReason,blockedBy,closedByPullRequestsReferences"
        try:
            raw = self._gh("issue", "view", str(number), "--json", fields)
        except ValueError:
            # An older `gh` that does not expose the native dependency at all: fall
            # back to every other field, `blocked_by=None` tells the caller to read
            # the issue body's own `Blocked by:` line instead.
            raw = self._gh(
                "issue",
                "view",
                str(number),
                "--json",
                "state,stateReason,closedByPullRequestsReferences",
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
        )

    def pull_request(self, reference: str) -> PullRequest | None:
        number = pull_request_number(reference)
        raw = self._gh(
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
            commits=commits,
        )

    def ci_report(self, reference: str) -> CiReport | CiUnreadable:
        """See `Tracker.ci_report`. `gh` resolves the repository from `product_dir`. Never
        raises and never reads stderr: the class of an unreadable answer is the step that
        failed. `base_sha` is always None: the gate that would have filled it from the
        Actions runs failed (ADR-0045, GH-216 amendment), `pull_requests` being empty on
        every run of this product's CI."""
        try:
            number = pull_request_number(reference)
        except ValueError:
            return CiUnreadable("gh_unreadable")
        answer = self._graphql(
            _PULL_REQUEST_CI_QUERY,
            ("-F", "owner={owner}", "-F", "repo={repo}", "-F", f"number={number}"),
        )
        if isinstance(answer, CiUnreadable):
            return answer
        try:
            pull_request = answer["data"]["repository"]["pullRequest"]
            commit = pull_request["commits"]["nodes"][0]["commit"]
            head_sha, checks = _commit_ci(commit)
            return CiReport(
                head_sha=head_sha,
                base_ref=_text(pull_request["baseRefName"]),
                base_sha=None,
                opened_at=_text(pull_request["createdAt"]),
                checks=checks,
            )
        except (_Unreadable, KeyError, IndexError, TypeError):
            return CiUnreadable("gh_unreadable")

    def branch_ci(self, branch: str) -> CiReport | CiUnreadable:
        """See `Tracker.ci_report`: the latest commit of `branch`, and no pull request."""
        answer = self._graphql(
            _BRANCH_CI_QUERY,
            ("-F", "owner={owner}", "-F", "repo={repo}", "-f", f"ref=refs/heads/{branch}"),
        )
        if isinstance(answer, CiUnreadable):
            return answer
        try:
            head_sha, checks = _commit_ci(answer["data"]["repository"]["ref"]["target"])
        except (_Unreadable, KeyError, TypeError):
            return CiUnreadable("gh_unreadable")
        return CiReport(
            head_sha=head_sha, base_ref=None, base_sha=None, opened_at=None, checks=checks
        )

    def _graphql(self, query: str, fields: tuple[str, ...]) -> dict | CiUnreadable:
        """One `gh api graphql` call, its JSON object or the class of its failure."""
        try:
            raw = self._gh("api", "graphql", "-f", f"query={query}", *fields)
        except ValueError as exc:  # `_gh`'s own failure
            missing = isinstance(exc.__cause__, FileNotFoundError)
            return CiUnreadable("gh_unavailable" if missing else "gh_unreadable")
        try:
            answer = json.loads(raw)
        except json.JSONDecodeError:
            return CiUnreadable("gh_unreadable")
        return answer if isinstance(answer, dict) else CiUnreadable("gh_unreadable")

    def merge(self, reference: str, head_sha: str) -> str:
        """Raises only `MergeCallFailed`, its class chosen at the step that failed: never
        by reading `gh`'s stderr (ADR-0029). The reference is parsed first, so an
        unreadable one reaches no `gh` call at all; `gh pr merge` gets the bare number and
        resolves the repository from `product_dir`, as `pull_request` does."""
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
        except ValueError as exc:  # `_gh`'s own failure, or a `JSONDecodeError`
            missing = isinstance(exc.__cause__, FileNotFoundError)
            raise MergeCallFailed(
                "gh_unavailable" if missing else "gh_unreadable", str(exc)
            ) from exc
        if not isinstance(allowed, dict):
            raise MergeCallFailed(
                "gh_unreadable", "gh repo view answered something else than an object"
            )
        method = next((name for name, key in _MERGE_METHODS if allowed.get(key)), None)
        if method is None:
            raise MergeCallFailed("no_merge_method", "no_merge_method")
        # The head sha pins the merge to what the decision saw (GH-178); no `--auto` and no
        # `--delete-branch`: the poller's own `close` owns what happens after.
        try:
            self._gh("pr", "merge", str(number), f"--{method}", "--match-head-commit", head_sha)
        except ValueError as exc:
            missing = isinstance(exc.__cause__, FileNotFoundError)
            raise MergeCallFailed("gh_unavailable" if missing else "gh_rejected", str(exc)) from exc
        return method

    def _gh(self, *args: str) -> str:
        try:
            result = self._run(
                ["gh", *args], cwd=self.product_dir, capture_output=True, text=True, check=True
            )
        except FileNotFoundError as exc:
            raise ValueError("gh is not installed; delivering to GitHub needs the CLI") from exc
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"gh {' '.join(args[:2])} failed: {exc.stderr.strip()}") from exc
        return result.stdout.strip()
