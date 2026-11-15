"""`SpecValidator` over `knowledge.domain.spec.validate` (GH-594): a straight pass-through.

No I/O and no translation: the port is typed with Process's own names and `validate` already
answers in them. Wiring it is the composition root's business, not this module's.
"""

from __future__ import annotations

from ...process.domain.spec_delivery import SpecCause, SpecContract
from ..domain import spec


class DomainSpecValidator:
    def validate(
        self, text: str, path: str, commit: str, slice_id: str | None, owner_login: str | None
    ) -> SpecContract | SpecCause:
        return spec.validate(text, path, commit, slice_id, owner_login)
