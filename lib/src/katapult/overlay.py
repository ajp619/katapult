"""Template overrides (``~/.katapult/template/``) applied to existing projects.

``kat init`` merges the override folder into new projects and records the copied paths in
``.katapult/.context_manifest``. ``sync_overlay`` brings an existing project up to date
with the current override folder: it refreshes managed files, removes ones the folder no
longer provides, and rewrites the manifest. Credential files (``build/.*_credentials``)
are personal secrets, not template content, so they are never copied, deleted or recorded
here; see :mod:`katapult.secrets`.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from katapult.secrets import is_credentials_path

MANIFEST_REL = Path(".katapult") / ".context_manifest"


def override_dir() -> Path:
    return Path.home() / ".katapult" / "template"


def override_files(src: Path) -> tuple[list[str], list[str]]:
    """(managed, excluded) relative paths under ``src``; credentials are excluded."""
    managed: list[str] = []
    excluded: list[str] = []
    for p in sorted(src.rglob("*")):
        if p.is_file():
            rel = p.relative_to(src).as_posix()
            (excluded if is_credentials_path(rel) else managed).append(rel)
    return managed, excluded


def _safe(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    return bool(parts) and not rel.startswith("/") and ".." not in parts


def read_manifest(project: Path) -> list[str]:
    path = project / MANIFEST_REL
    if not path.is_file():
        return []
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


@dataclass
class OverlayResult:
    source: Path
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)  # credentials found in the overlay
    released: list[str] = field(default_factory=list)  # credentials dropped from the manifest

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.removed or self.released)


def sync_overlay(
    project: Path, *, source: Path | None = None, dry_run: bool = False
) -> OverlayResult | None:
    """Refresh ``project`` from the override folder. None when there is no override folder."""
    src = source or override_dir()
    if not src.is_dir():
        return None
    managed, excluded = override_files(src)
    result = OverlayResult(src, excluded=excluded)
    wanted = set(managed)

    for rel in read_manifest(project):
        if not _safe(rel):
            continue
        if is_credentials_path(rel):
            result.released.append(rel)
            continue
        target = project / rel
        if rel not in wanted and target.is_file():
            result.removed.append(rel)
            if not dry_run:
                target.unlink()

    for rel in managed:
        target = project / rel
        new = (src / rel).read_bytes()
        if not target.is_file():
            result.added.append(rel)
        elif target.read_bytes() != new:
            result.updated.append(rel)
        else:
            result.unchanged.append(rel)
            continue
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / rel, target)

    if not dry_run:
        manifest = project / MANIFEST_REL
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("".join(f"{rel}\n" for rel in managed))
    return result
