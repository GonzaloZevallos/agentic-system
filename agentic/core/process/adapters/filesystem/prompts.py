"""`Prompts` with the product overlay (ADR-0005): `.agentic/prompts/<slug>.md` in the
product repo wins over the system's default in `agentic/core/process/domain/prompts/`.
The role's playbook (ADR-0043) comes from the same overlay, `.agentic/playbooks/`.

The prompt texts are read once, when the adapter is built (GH-409): a run renders every
role from the texts present when its ports were constructed, so a stage0 update under a
live run changes nothing it renders. The playbook is the exception and is read per role."""

from __future__ import annotations

from pathlib import Path

from ...domain import playbook
from ...domain.playbook import Playbook
from ...domain.roles import PROMPTS_DIR, ROLES, Role

OVERLAY_SUBDIR = Path(".agentic/prompts")
PLAYBOOKS_SUBDIR = Path(".agentic/playbooks")


class FilesystemPrompts:
    def __init__(self, product_dir: Path, system_dir: Path = PROMPTS_DIR):
        self.product_dir = product_dir
        self._read: dict[str, tuple[str, Path] | OSError | UnicodeDecodeError] = {
            slug: self._snapshot(slug, system_dir) for slug in {role.slug for role in ROLES}
        }

    def _snapshot(
        self, slug: str, system_dir: Path
    ) -> tuple[str, Path] | OSError | UnicodeDecodeError:
        """The prompt of `slug` as it is on disk now, or the error reading it raised:
        construction never fails for a file, the call for that role does."""
        source = self.product_dir / OVERLAY_SUBDIR / f"{slug}.md"
        try:
            if not source.exists():
                source = system_dir / f"{slug}.md"
            return source.read_text(encoding="utf-8"), source
        except (OSError, UnicodeDecodeError) as error:
            return error

    def raw(self, role: Role) -> tuple[str, Path]:
        found = self._read[role.slug]
        if isinstance(found, (OSError, UnicodeDecodeError)):
            raise found
        return found

    def playbook(self, role: Role) -> Playbook:
        """The role's playbook; empty when the file is absent or the role has none. A
        file that is not a playbook, or a top-level file that is neither a playbook of
        a role in the set nor a record, raises `ValueError` (preflight refuses it)."""
        directory = self.product_dir / PLAYBOOKS_SUBDIR
        self._refuse_strays(directory)
        if role.slug not in playbook.PLAYBOOK_ROLES:
            return Playbook(slug=role.slug)
        source = directory / f"{role.slug}.yaml"
        if not source.exists():
            return Playbook(slug=role.slug)
        return playbook.parse(source.read_text(encoding="utf-8"), role.slug)

    @staticmethod
    def _refuse_strays(directory: Path) -> None:
        if not directory.is_dir():
            return
        allowed = playbook.PLAYBOOK_ROLES | set(playbook.RECORD_FILES)
        for path in sorted(directory.glob("*.yaml")):
            if path.stem not in allowed:
                raise ValueError(
                    f"{path}: {path.stem} is not a role with a playbook "
                    f"({', '.join(sorted(playbook.PLAYBOOK_ROLES))}) nor a record file"
                )
