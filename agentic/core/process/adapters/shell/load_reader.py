"""`LoadReader` over the operating system: the one place the system reads `os.getloadavg`
(GH-451). `PerfShellRunner` stamps its runs with it and `run_check` classifies a `not_ready`
by it, so the two cannot disagree on what the load was."""

from __future__ import annotations

import os


class ShellLoadReader:
    def one_minute(self) -> float | None:
        try:
            return os.getloadavg()[0]
        except OSError:
            return None
