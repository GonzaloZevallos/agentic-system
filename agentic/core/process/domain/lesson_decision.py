"""The lesson decider's domain (GH-881, GH-918): what a decider is asked, what it may answer,
and how an answer is settled into something a caller can trust.

Pure. The model's answer is untrusted: `settle` is the only function that builds `Settled`,
`Decision`, `ClusterVerdict` and `Report`, and nothing here raises on a request or an answer.
A `Report` carries a kind, a key the request offered and a closed cause, never the model's
text. This module does not import `schemas`: `LessonDecisions` there is the only definition of
the output shape, and a test holds the two to each other.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, assert_never

from . import playbook
from .playbook import Archived, Item, Pending

Shelf = Literal["active", "candidate", "archived"]
Verb = Literal["ADD", "MERGE", "NOOP", "CONTRADICTS"]
VerdictKind = Literal["confirm", "split"]
ReportCause = Literal[
    "unknown_id",
    "missing",
    "duplicate",
    "unknown_cluster",
    "wording_not_a_source",
    "wording_refused",
    "wording_on_active",
    "canonical_not_a_member",
]

# What one request may hold: the cost of a decision is linear in these.
DECIDE_CANDIDATES_MAX = 20
SIMILAR_MAX = 5
CLUSTERS_MAX = 5
CLUSTER_MEMBERS_MAX = 6

_PENDING_PREFIX = "candidate:"
_SEPARATOR = "\x1f"


# ---- the request ----------------------------------------------------------------------


@dataclass(frozen=True)
class Similar:
    id: str
    text: str
    shelf: Shelf


@dataclass(frozen=True)
class NewCandidate:
    key: str
    text: str
    similar: tuple[Similar, ...]


@dataclass(frozen=True)
class Cluster:
    key: str
    members: tuple[Similar, ...]


@dataclass(frozen=True)
class DecisionRequest:
    slug: str
    candidates: tuple[NewCandidate, ...]
    clusters: tuple[Cluster, ...]


@dataclass(frozen=True)
class OverCap:
    cap: Literal["candidates", "similar", "clusters", "members"]
    limit: int
    got: int


@dataclass(frozen=True)
class DuplicateKey:
    kind: Literal["candidate", "cluster", "similar"]
    key: str


def decision_request(
    slug: str, candidates: list[NewCandidate], clusters: list[Cluster]
) -> DecisionRequest | OverCap | DuplicateKey:
    """The request for these candidates and clusters, or the first cap it exceeds or key it
    repeats. A repeated key would make an answer ambiguous. Never raises."""
    if len(candidates) > DECIDE_CANDIDATES_MAX:
        return OverCap("candidates", DECIDE_CANDIDATES_MAX, len(candidates))
    if len(clusters) > CLUSTERS_MAX:
        return OverCap("clusters", CLUSTERS_MAX, len(clusters))
    for candidate in candidates:
        if len(candidate.similar) > SIMILAR_MAX:
            return OverCap("similar", SIMILAR_MAX, len(candidate.similar))
    for cluster in clusters:
        if len(cluster.members) > CLUSTER_MEMBERS_MAX:
            return OverCap("members", CLUSTER_MEMBERS_MAX, len(cluster.members))
    keys = [c.key for c in candidates]
    if len(set(keys)) != len(keys):
        return DuplicateKey("candidate", _first_repeat(keys))
    cluster_keys = [c.key for c in clusters]
    if len(set(cluster_keys)) != len(cluster_keys):
        return DuplicateKey("cluster", _first_repeat(cluster_keys))
    for candidate in candidates:
        ids = [s.id for s in candidate.similar]
        if len(set(ids)) != len(ids):
            return DuplicateKey("similar", _first_repeat(ids))
    return DecisionRequest(slug, tuple(candidates), tuple(clusters))


def _first_repeat(keys: list[str]) -> str:
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            return key
        seen.add(key)
    return ""


def pending_id(pending: Pending) -> str:
    """The id a candidate lesson is offered under: the same (slug, category, normalised text)
    identity `playbook` deduplicates candidates by, so it changes if the lesson is edited."""
    identity = _SEPARATOR.join((pending.slug, pending.category, playbook.normalise(pending.lesson)))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return "".join((_PENDING_PREFIX, digest))


def similar_of(value: Item | Pending | Archived) -> Similar:
    """The only place a playbook value gets a shelf."""
    match value:
        case Item():
            return Similar(value.id, value.lesson, "active")
        case Pending():
            return Similar(pending_id(value), value.lesson, "candidate")
        case Archived():
            return Similar(value.item.id, value.item.lesson, "archived")
        case _:
            assert_never(value)


def _similar_data(similar: Similar) -> dict[str, str]:
    return {"id": similar.id, "text": similar.text, "shelf": similar.shelf}


def request_text(request: DecisionRequest) -> str:
    """The only path from lesson text into the decider's prompt: one JSON document, so every
    text is an escaped string and none can read as an instruction or a placeholder."""
    return json.dumps(
        {
            "slug": request.slug,
            "candidates": [
                {
                    "key": c.key,
                    "text": c.text,
                    "similar": [_similar_data(s) for s in c.similar],
                }
                for c in request.candidates
            ],
            "clusters": [
                {"key": c.key, "members": [_similar_data(m) for m in c.members]}
                for c in request.clusters
            ],
        },
        ensure_ascii=False,
        indent=1,
    )


# ---- the answer, as the model gives it -------------------------------------------------


@dataclass(frozen=True)
class RawDecision:
    candidate_key: str
    verb: Verb
    target_id: str | None
    wording: str | None


@dataclass(frozen=True)
class RawVerdict:
    cluster_key: str
    verdict: VerdictKind
    canonical: str | None


@dataclass(frozen=True)
class RawDecisions:
    decisions: tuple[RawDecision, ...]
    verdicts: tuple[RawVerdict, ...]


# ---- the answer, settled ---------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    candidate_key: str
    verb: Verb
    target_id: str | None
    wording: str | None
    # The shelf the input gave the target; `settle` does not decide grace.
    target_shelf: Shelf | None


@dataclass(frozen=True)
class ClusterVerdict:
    cluster_key: str
    verdict: VerdictKind
    canonical: str | None


@dataclass(frozen=True)
class Report:
    """What `settle` changed or refused. A foreign key came from the model, so it is not
    echoed: its subject is empty."""

    kind: Literal["candidate", "cluster"]
    subject: str
    cause: ReportCause


@dataclass(frozen=True)
class Settled:
    decisions: tuple[Decision, ...]
    verdicts: tuple[ClusterVerdict, ...]
    # Candidate keys with no decision, in request order: they stay where they are.
    undecided: tuple[str, ...]
    reports: tuple[Report, ...]


def settle(request: DecisionRequest, raw: RawDecisions) -> Settled:
    """The only constructor of `Settled`: `raw` held to `request`, never trusted."""
    reports: list[Report] = []
    decided = _settle_decisions(request, raw.decisions, reports)
    verdicts = _settle_verdicts(request, raw.verdicts, reports)
    decisions = tuple(decided[c.key] for c in request.candidates if c.key in decided)
    undecided = tuple(c.key for c in request.candidates if c.key not in decided)
    reports.extend(Report("candidate", key, "missing") for key in undecided)
    reports.extend(
        Report("cluster", c.key, "missing")
        for c in request.clusters
        if c.key not in {v.cluster_key for v in verdicts}
    )
    return Settled(decisions, verdicts, undecided, tuple(reports))


def _settle_decisions(
    request: DecisionRequest, raw: tuple[RawDecision, ...], reports: list[Report]
) -> dict[str, Decision]:
    candidates = {c.key: c for c in request.candidates}
    decided: dict[str, Decision] = {}
    for entry in raw:
        candidate = candidates.get(entry.candidate_key)
        if candidate is None:
            reports.append(Report("candidate", "", "unknown_id"))
        elif candidate.key in decided:
            reports.append(Report("candidate", candidate.key, "duplicate"))
        else:
            decided[candidate.key] = _settle_one(candidate, entry, reports)
    return decided


def _settle_one(candidate: NewCandidate, entry: RawDecision, reports: list[Report]) -> Decision:
    key = candidate.key
    wording = entry.wording if entry.wording is not None and entry.wording.strip() else None
    target = next((s for s in candidate.similar if s.id == entry.target_id), None)
    verb = entry.verb
    if verb == "ADD":
        target = None
    elif target is None:
        # An id that is not this candidate's own, or none: it is a new lesson. Whatever it
        # wrote is not trusted, so the wording is checked as an ADD's and then dropped.
        reports.append(Report("candidate", key, "unknown_id"))
        if wording is not None and not _is_source(wording, (candidate.text,)):
            reports.append(Report("candidate", key, "wording_not_a_source"))
        return Decision(key, "ADD", None, None, None)
    sources = _sources(verb, candidate, target)
    if wording is not None and not _is_source(wording, sources):
        reports.append(Report("candidate", key, "wording_not_a_source"))
        wording = None
    if wording is not None and playbook.lesson_refusal(wording) is not None:
        reports.append(Report("candidate", key, "wording_refused"))
        wording = None
    if wording is not None and verb == "MERGE" and target is not None and target.shelf == "active":
        reports.append(Report("candidate", key, "wording_on_active"))
        wording = None
    return Decision(
        key,
        verb,
        None if target is None else target.id,
        wording,
        None if target is None else target.shelf,
    )


def _sources(verb: Verb, candidate: NewCandidate, target: Similar | None) -> tuple[str, ...]:
    """The texts a wording may be, by verb: the table of what each verb may carry."""
    if verb == "ADD":
        return (candidate.text,)
    if verb == "MERGE" and target is not None:
        return (candidate.text, target.text)
    return ()


def _is_source(wording: str, sources: tuple[str, ...]) -> bool:
    return any(wording.strip() == source.strip() for source in sources)


def _settle_verdicts(
    request: DecisionRequest, raw: tuple[RawVerdict, ...], reports: list[Report]
) -> tuple[ClusterVerdict, ...]:
    clusters = {c.key: c for c in request.clusters}
    settled: dict[str, ClusterVerdict] = {}
    for entry in raw:
        cluster = clusters.get(entry.cluster_key)
        if cluster is None:
            reports.append(Report("cluster", "", "unknown_cluster"))
        elif cluster.key in settled:
            reports.append(Report("cluster", cluster.key, "duplicate"))
        else:
            settled[cluster.key] = _settle_verdict(cluster, entry, reports)
    return tuple(settled[c.key] for c in request.clusters if c.key in settled)


def _settle_verdict(cluster: Cluster, entry: RawVerdict, reports: list[Report]) -> ClusterVerdict:
    split = ClusterVerdict(cluster.key, "split", None)
    if entry.verdict == "split":
        return split
    canonical = entry.canonical
    if canonical is None or all(canonical != m.text for m in cluster.members):
        reports.append(Report("cluster", cluster.key, "canonical_not_a_member"))
        return split
    if playbook.lesson_refusal(canonical) is not None:
        reports.append(Report("cluster", cluster.key, "wording_refused"))
        return split
    return ClusterVerdict(cluster.key, "confirm", canonical)
