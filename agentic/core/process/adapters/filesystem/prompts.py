"""`Prompts` with the product overlay (ADR-0005): `.agentic/prompts/<slug>.md` in the
product repo wins over the system's default in `agentic/core/process/domain/prompts/`.
The role's playbook (ADR-0043) comes from the same overlay, `.agentic/playbooks/`.

The prompt texts are read once, when the adapter is built (GH-409): a run renders every
role from the texts present when its ports were constructed, so a stage0 update under a
live run changes nothing it renders. The playbook is the exception and is read per role.

The constant Spec blocks (ADR-0096, GH-718) are a snapshot the same way: each distinct name
of the one table, `SPEC_BLOCKS`, is read when the adapter is built, from
`.agentic/prompts/spec/<name>.md` of the product or else `prompts/spec/<name>.md` of the
system, and a read error is kept and raised by `spec_block` for that name. A name that fails
`block_name_ok` is never joined into a path. With the table empty no file under `spec/` is
opened. `blocks` exists for the tests of the adapter; no production code passes it."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from ...domain import playbook
from ...domain.playbook import Playbook
from ...domain.roles import PROMPTS_DIR, ROLES, Role
from ...domain.spec_brief import SPEC_BLOCKS, block_name_ok

OVERLAY_SUBDIR = Path(".agentic/prompts")
PLAYBOOKS_SUBDIR = Path(".agentic/playbooks")
SPEC_SUBDIR = "spec"


class FilesystemPrompts:
    def __init__(
        self,
        product_dir: Path,
        system_dir: Path = PROMPTS_DIR,
        blocks: Mapping[str, str] = SPEC_BLOCKS,
    ):
        self.product_dir = product_dir
        self._read: dict[str, tuple[str, Path] | OSError | UnicodeDecodeError] = {
            slug: self._snapshot(slug, system_dir) for slug in {role.slug for role in ROLES}
        }
        self._blocks: dict[str, str | OSError | UnicodeDecodeError | ValueError] = {
            name: self._snapshot_block(name, system_dir) for name in set(blocks.values())
        }

    def _snapshot_block(
        self, name: str, system_dir: Path
    ) -> str | OSError | UnicodeDecodeError | ValueError:
        """The block `name` as it is on disk now, or the error that keeps it from being served.
        A name off the pattern gets no path at all: it is the only guard between a table entry
        and the filesystem."""
        if not block_name_ok(name):
            return ValueError(f"{name!r} is not a valid spec block name")
        source = self.product_dir / OVERLAY_SUBDIR / SPEC_SUBDIR / f"{name}.md"
        try:
            if not source.exists():
                source = system_dir / SPEC_SUBDIR / f"{name}.md"
            return source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            return error

    def spec_block(self, name: str) -> str:
        found = self._blocks.get(name)
        if found is None:
            raise ValueError(f"no spec block {name!r} in the snapshot")
        if isinstance(found, (OSError, ValueError)):  # UnicodeDecodeError is a ValueError
            raise found
        return found

    def spec_block_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._blocks))

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
