"""The product repo's history as the store of what held at a commit: the product's
stack (`.agentic/stack.yaml`) at the task's base."""

from .stack import GitStack

__all__ = ["GitStack"]
