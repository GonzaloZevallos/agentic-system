"""JSON schema as the SDK wants it: one self-contained tree, no `$ref`."""

from __future__ import annotations

from typing import Any


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Nested Pydantic models arrive as `$ref` into `$defs`; the SDK wants them inline."""
    schema = dict(schema)
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].rsplit("/", 1)[-1]
                return walk({**defs[name], **{k: v for k, v in node.items() if k != "$ref"}})
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(schema)
