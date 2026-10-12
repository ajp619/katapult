from __future__ import annotations

from pathlib import Path

from .config import HANDOFF_DIR


def _resolve(rel: str) -> Path:
    """Resolve a handoff-relative path, blocking escape outside the handoff dir."""
    root = HANDOFF_DIR.resolve()
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"path escapes handoff dir: {rel!r}")
    return target


def list_handoff() -> list[str]:
    """List files currently in the handoff directory (handoff-relative paths)."""
    if not HANDOFF_DIR.is_dir():
        return []
    return sorted(
        p.relative_to(HANDOFF_DIR).as_posix()
        for p in HANDOFF_DIR.rglob("*")
        if p.is_file()
    )


def read_handoff(rel: str) -> str:
    """Read a handoff file given a handoff-relative path."""
    return _resolve(rel).read_text()


def write_handoff(rel: str, content: str) -> str:
    """Write a handoff file; returns the handoff-relative path written."""
    target = _resolve(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target.relative_to(HANDOFF_DIR.resolve()).as_posix()
