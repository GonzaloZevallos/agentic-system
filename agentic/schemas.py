"""Structured outputs: what PjM processes mechanically (DISENO.md, 14).

Each schema goes to the SDK as `json_schema`. The Markdown section travels as
the `markdown` field, free text inside the same output: the SDK ends the turn
on the structured-output tool call, so a separate text reply never arrives.
A missing or invalid structured output is a role failure, never an empty dict.

`flags` is typed loosely here and tightened per run to the product's flag
vocabulary, so the model can only mark names that exist.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

TaskType = Literal["bug", "feature", "platform_enhancement"]


class RoleOutput(BaseModel):
    markdown: str = Field(description="The role's Markdown section, free text, `###` headings")


class Classification(RoleOutput):
    type: TaskType
    # Low confidence escalates to the human; PjM never guesses (DISENO.md, 4).
    confidence: Literal["high", "low"]
    reason: str = Field(description="One line")


class Definition(RoleOutput):
    flags: dict[str, bool] = Field(description="Every flag of the vocabulary, marked yes/no")


class CapabilityFile(BaseModel):
    capability: str = Field(
        description="File name in docs/product/, a slug named after a glossary concept, "
        "e.g. `check-ins`, `metrics`"
    )
    content: str = Field(
        description="The complete updated file, not a diff: everything current plus what "
        "this task adds or changes, metrics included"
    )


class GlossaryTerm(BaseModel):
    term: str
    definition: str = Field(description="One to three sentences")
    avoid: str = Field(description="Comma-separated words not to use for it; may be empty")


class Verdict(RoleOutput):
    verdict: Literal["approved", "returned"]
    reason: str = Field(description="One line. On `returned`, what blocks building")
    flags: dict[str, bool] = Field(description="Every flag of the vocabulary, validated")
    # The distillation (ADR-0006): PM returns the knowledge, the graph writes it.
    definitions: list[CapabilityFile] = Field(
        description="On `approved` for a feature, one entry per capability the task "
        "touches. Empty on `returned`"
    )
    glossary: list[GlossaryTerm] = Field(
        description="Terms that are new or whose meaning changed. Empty if none"
    )


def json_schema(schema: type[RoleOutput], vocabulary: dict[str, str]) -> dict[str, Any]:
    """The schema for the SDK, with `flags` closed over the vocabulary when present."""
    out = _inline_refs(schema.model_json_schema())
    if "flags" in out.get("properties", {}):
        out["properties"]["flags"] = {
            "type": "object",
            "properties": {
                name: {"type": "boolean", "description": desc} for name, desc in vocabulary.items()
            },
            "required": list(vocabulary),
            "additionalProperties": False,
        }
    return out


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Nested models arrive as `$ref` into `$defs`; the SDK wants one self-contained tree."""
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


def validate(schema: type[RoleOutput], raw: Any, vocabulary: dict[str, str]) -> dict[str, Any]:
    """Pydantic validation plus the vocabulary check. Raises `ValueError` on any gap."""
    data = schema.model_validate(raw).model_dump()
    if "flags" in data:
        got, expected = set(data["flags"]), set(vocabulary)
        if got != expected:
            raise ValueError(
                f"flags must be exactly the vocabulary; unknown: {sorted(got - expected)}, "
                f"missing: {sorted(expected - got)}"
            )
    return data
