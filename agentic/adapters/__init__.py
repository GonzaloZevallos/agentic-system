"""Adapters: the graph engine, the model harness, the disk, GitHub and tracing.

Each one implements a port from `agentic.application.ports` or drives the use
cases (the graph, the CLI). They import the domain and the application; only
`agentic.composition`, which wires them, and `agentic.cli` import them (ADR-0007).
"""
