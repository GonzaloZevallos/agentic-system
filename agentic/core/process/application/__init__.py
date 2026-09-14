"""Use cases: one per node of the path, plus the generic `run_role` they share.

They take a `Configuration` and a `Ports` container and know nothing about the graph
engine, the model harness or the disk (ADR-0007). Escalation is data in the patch
they return; pausing on it is the graph adapter's business (ADR-0003).
"""
