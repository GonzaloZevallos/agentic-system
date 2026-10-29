"""Where the owner's decisions live in a product repo, declared once for both modules.

`core.process` decides which paths a close-time commit may hold (`rules.head_reviewed`) and
`core.knowledge` reads and indexes the same files, but `core.process` may import only
`shared` (ADR-0007): the paths are named here, and `core.knowledge.domain.knowledge`
re-exports them. Normalised to NFC, because a path with an `ñ` reaches the system from
git and from the tracker in either form and is compared as a string.
"""

from __future__ import annotations

import unicodedata


def _nfc(path: str) -> str:
    return unicodedata.normalize("NFC", path)


# The product's own rules (`docs/product/`) and the rules about how the system works on any
# product (agentic-system's `docs/`). The only paths the owner-rules commit may write.
OWNER_DECISIONS: tuple[str, ...] = (
    _nfc("docs/product/decisiones-del-dueño.md"),
    _nfc("docs/decisiones-del-dueño.md"),
)
