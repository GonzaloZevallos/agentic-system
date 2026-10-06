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

from ...application.ports import IssueComment, IssueState, ListedPullRequest
from ...domain.task import PullRequest, PullRequestCommit

_ISSUE = re.compile(r"^GH-(\d+)$")
_PULL_REQUEST_URL = re.compile(r"/pull/(\d+)$")

# The order a merge method is picked in: the first the repository allows.
_MERGE_METHODS = (
    ("merge", "mergeCommitAllowed"),
    ("squash", "squashMergeAllowed"),
    ("rebase", "rebaseMergeAllowed"),
)

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
        # Not a draft: the human merges it as it is (README.md, 9), or does not.
        return self._gh("pr", "create", "--head", branch, "--title", title, "--body", body)

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

    def merge(self, number: int, head_sha: str) -> str:
        allowed = json.loads(
            self._gh(
                "repo",
                "view",
                "--json",
                "mergeCommitAllowed,squashMergeAllowed,rebaseMergeAllowed",
            )
        )
        method = next((name for name, key in _MERGE_METHODS if allowed.get(key)), None)
        if method is None:
            raise ValueError("no_merge_method")
        # The head sha pins the merge to what the decision saw (GH-178); no `--auto` and no
        # `--delete-branch`: the poller's own `close` owns what happens after.
        self._gh("pr", "merge", str(number), f"--{method}", "--match-head-commit", head_sha)
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
