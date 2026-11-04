"""`EvalRoots` over the product's git repository (GH-470): a root is a detached worktree
of the product at the entry's `base`, under `<product>/.agentic/eval/<id>/`."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from agentic.core.process.domain import rules

EVAL_DIR = Path(".agentic") / "eval"
_PREFIX = "EVAL-"


class GitEvalRoots:
    def __init__(self, product_dir: Path):
        self.product_dir = product_dir

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=self.product_dir, capture_output=True, text=True, check=False
        )

    def resolves(self, base: str) -> bool:
        return self._git("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").returncode == 0

    def has_stack(self, base: str) -> bool:
        return self._git("cat-file", "-e", f"{base}:.agentic/stack.yaml").returncode == 0

    def path(self, eval_id: str) -> Path:
        return self.product_dir / EVAL_DIR / eval_id

    def create(self, eval_id: str, base: str) -> Path:
        root = self.path(eval_id)
        root.parent.mkdir(parents=True, exist_ok=True)
        result = self._git("worktree", "add", "--detach", str(root), base)
        if result.returncode != 0:
            raise OSError(f"git worktree add {root}: {result.stderr.strip()}")
        return root

    def ids(self) -> list[str]:
        parent = self.product_dir / EVAL_DIR
        if not parent.is_dir():
            return []
        return sorted(p.name for p in parent.iterdir() if p.is_dir() and p.name.startswith(_PREFIX))

    def remove(self, eval_id: str) -> None:
        root = self.path(eval_id)
        self._git("worktree", "remove", "--force", str(root))
        if root.exists():  # not registered any more (or never was): still a directory to drop
            shutil.rmtree(root, ignore_errors=True)
        self._git("worktree", "prune")

    def delete_branches(self, eval_id: str) -> None:
        own = rules.task_branch(eval_id)
        listed = self._git("for-each-ref", "--format=%(refname:short)", "refs/heads/agentic/")
        for name in listed.stdout.split():
            if name == own or name.startswith(f"{own}."):
                self._git("branch", "-D", name)
