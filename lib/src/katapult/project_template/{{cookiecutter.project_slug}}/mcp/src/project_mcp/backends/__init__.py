from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AgentBackend(Protocol):
    """A pluggable 'second brain' that executes a delegated task rooted at /content."""

    name: str

    def delegate(self, task: str, resume_id: str | None = None) -> dict:
        ...


def get_backend(name: str) -> AgentBackend | None:
    """Return the configured backend adapter, or ``None`` for 'none'/unknown.

    Adapters are imported lazily so the neutral server never imports a vendor
    SDK (e.g. ``cursor-sdk``) unless that backend is actually selected. An
    unknown name is treated as disabled, leaving the ``delegate`` tool absent.
    """
    name = (name or "none").lower()
    if name in ("", "none"):
        return None
    if name == "cursor":
        from .cursor_backend import CursorBackend

        return CursorBackend()
    return None
