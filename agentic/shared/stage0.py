"""stage0: the checkout the system runs from (ADR-0017).

Bootstrapping a compiler. The copy that executes a run is not the copy the run edits,
so a task may rewrite the system while the system attends it. This module only says
*where* stage0 is; whether the product is that same repository is `preflight`'s rule,
and the git question behind it belongs to the git adapter, which is what may run a
command.

An installed wheel answers its `site-packages`, which is in no repository: everyone who
asks treats "not a repository" as "not stage0", never as an error.
"""

from __future__ import annotations

from pathlib import Path

# agentic/shared/stage0.py -> agentic/ -> the checkout the package was installed from.
CHECKOUT = Path(__file__).resolve().parents[2]
