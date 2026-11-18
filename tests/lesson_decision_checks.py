"""What a `Settled` must hold for the request it answers (GH-918): the checker the decider
tests share. One line per violation, so a test says what broke and an empty list says nothing did.
It reads `settle`'s output; it never builds one."""

from __future__ import annotations

from collections import Counter

from agentic.core.process.domain.lesson_decision import DecisionRequest, Settled


def settled_is_consistent(request: DecisionRequest, settled: Settled) -> list[str]:
    return [*_decision_lines(request, settled), *_verdict_lines(request, settled)]


def _decision_lines(request: DecisionRequest, settled: Settled) -> list[str]:
    candidates = {c.key: c for c in request.candidates}
    lines: list[str] = []
    answered = Counter(d.candidate_key for d in settled.decisions)
    answered.update(settled.undecided)
    for key in answered:
        if key not in candidates:
            lines.append(f"{key!r}: not a candidate of the request")
    for key in candidates:
        if answered[key] != 1:
            lines.append(f"{key!r}: appears {answered[key]} times across decisions and undecided")
    for decision in settled.decisions:
        candidate = candidates.get(decision.candidate_key)
        if candidate is None:
            continue
        name = repr(decision.candidate_key)
        target = next((s for s in candidate.similar if s.id == decision.target_id), None)
        sources = [candidate.text.strip()]
        if decision.verb == "ADD":
            if decision.target_id is not None or decision.target_shelf is not None:
                lines.append(f"{name}: an ADD carries a target or a shelf")
        else:
            if target is None:
                lines.append(f"{name}: {decision.verb} target is not in the candidate's similar")
            elif decision.target_shelf != target.shelf:
                lines.append(f"{name}: target_shelf is not the shelf of the target")
            else:
                sources.append(target.text.strip())
        if decision.wording is None:
            continue
        if decision.verb in ("NOOP", "CONTRADICTS"):
            lines.append(f"{name}: {decision.verb} carries a wording")
        elif decision.wording.strip() not in sources:
            lines.append(f"{name}: the wording is neither the candidate's text nor the target's")
        if decision.verb == "MERGE" and decision.target_shelf == "active":
            lines.append(f"{name}: a MERGE onto an active target carries a wording")
    return lines


def _verdict_lines(request: DecisionRequest, settled: Settled) -> list[str]:
    clusters = {c.key: c for c in request.clusters}
    lines: list[str] = []
    for key, times in Counter(v.cluster_key for v in settled.verdicts).items():
        if key not in clusters:
            lines.append(f"{key!r}: not a cluster of the request")
        elif times > 1:
            lines.append(f"{key!r}: has {times} verdicts")
    for verdict in settled.verdicts:
        cluster = clusters.get(verdict.cluster_key)
        if cluster is None:
            continue
        name = repr(verdict.cluster_key)
        if verdict.verdict == "split" and verdict.canonical is not None:
            lines.append(f"{name}: a split carries a canonical")
        if verdict.verdict == "confirm" and all(
            verdict.canonical != member.text for member in cluster.members
        ):
            lines.append(f"{name}: a confirm's canonical is not exactly a member's text")
    return lines
