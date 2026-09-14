"""Adapters of the process: the graph engine, the model harness, the disk and GitHub.

Each one implements a port from `..application.ports` or drives the use cases (the
graph, the CLI). Tracing arrives through `telemetry.Telemetry`, a port of this layer
that `core.measurement` implements and `agentic.composition` wires (ADR-0007,
ADR-0010).
"""
