"""Shared exception types that do not pull in a platform backend."""

from __future__ import annotations


class EngineError(RuntimeError):
    """An engine failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
