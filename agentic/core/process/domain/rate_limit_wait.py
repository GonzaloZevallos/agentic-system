"""What `gh api rate_limit` said, as a value (GH-1157, ADR-0050 addendum)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class RateLimitReading:
    """The resources of one `gh api rate_limit` document: resource name -> (`remaining`,
    `reset` in epoch seconds). Immutable: the mapping is a read-only copy of what it was built
    from, so a reading that crossed threads cannot change under its reader."""

    resources: Mapping[str, tuple[int, float]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "resources", MappingProxyType(dict(self.resources)))
