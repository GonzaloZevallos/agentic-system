"""Errors that cross module boundaries."""

from __future__ import annotations


class CannotStart(Exception):
    """The command refuses before spending anything; the message says why and what to do."""

    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code
