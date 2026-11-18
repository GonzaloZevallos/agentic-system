"""`FilesystemLandRecord`: the last landing attempt of a product's playbook PR, one JSON file
per product at `<root>/playbook-land/<product>.json` (owner's rule of 2026-11-16).

The root is the machine state directory, never a checkout, so the record survives a stage0
rotation. A save writes the whole file atomically: a temp file in the same directory, `fsync`,
`os.replace`, so a crash never leaves half of it. A file that is missing, unreadable or of
another version loads as `None`."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from ..domain.playbook_land import LandAttempt

_PRODUCT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
DIRECTORY = "playbook-land"


class FilesystemLandRecord:
    def __init__(self, root: Path):
        self.directory = root / DIRECTORY

    def _path(self, product: str) -> Path:
        if _PRODUCT.fullmatch(product) is None or ".." in product:
            raise ValueError(f"not a product name: {product!r}")
        return self.directory / f"{product}.json"

    def load(self, product: str) -> LandAttempt | None:
        try:
            raw = json.loads(self._path(product).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return LandAttempt.from_data(raw)

    def save(self, product: str, attempt: LandAttempt) -> None:
        path = self._path(product)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                out.write(json.dumps(attempt.to_data(), sort_keys=True))
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
