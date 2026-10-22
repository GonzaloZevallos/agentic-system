"""The closed task documents, read by whoever needs the product's history: the reflection
(`reflect_run`), the plan's cost history (`plan`) and the estimate cap (`run_role`). Its own
module so that `run_role`, which every role passes through, can read it without a cycle."""

from __future__ import annotations

from typing import Any

from .ports import Ports


def closed_documents(ports: Ports) -> list[dict[str, Any]]:
    """Every task document with a recorded closure, as the state-shaped dicts `load` gives."""
    docs = (ports.documents.load(task_id) for task_id in ports.documents.ids())
    return [d for d in docs if d and d.get("closed_at")]
