"""The product repo's history as the store of what held at a commit: the checks
(`.agentic/checks.yaml`) at the task's base."""

from .checks import GitChecks

__all__ = ["GitChecks"]
