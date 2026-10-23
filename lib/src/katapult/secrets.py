"""Personal, per-machine secrets seeded into katapult projects.

Credentials belong to a person, not to a project or a shared template. They live in a
local secrets folder that is never a git repository and mirrors the project tree, e.g.
``~/.config/katapult/secrets/build/.agent_credentials``. ``kat init`` and ``kat sync``
copy them into a project, but only to credential paths (``build/.*_credentials``) that the
project's ``.gitignore`` and ``.dockerignore`` both exclude, so a secret never reaches a
commit or an image. Copies are create-only unless forced, written with mode 600, and
never recorded in any manifest.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

CREDENTIALS_PATTERN = "build/.*_credentials"


def secrets_dir() -> Path:
    """``$KATAPULT_SECRETS_DIR``, else ``${XDG_CONFIG_HOME:-~/.config}/katapult/secrets``."""
    override = os.environ.get("KATAPULT_SECRETS_DIR")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base).expanduser() / "katapult" / "secrets"


def is_credentials_path(rel: str) -> bool:
    """True for ``build/.<name>_credentials`` (``.example`` templates excluded)."""
    parts = PurePosixPath(rel.replace("\\", "/")).parts
    return len(parts) == 2 and parts[0] == "build" and fnmatchcase(parts[1], ".*_credentials")


def _pattern_covers(pattern: str, rel: str) -> bool:
    pat = pattern.lstrip("/").rstrip("/")
    if pat.startswith("**/"):
        pat = pat[3:]
    if "/" not in pat:  # a bare name matches at any depth
        return any(fnmatchcase(part, pat) for part in PurePosixPath(rel).parts)
    return fnmatchcase(rel, pat) or fnmatchcase(rel, pat + "/*")


def ignore_file_covers(ignore_file: Path, rel: str) -> bool:
    """Whether an ignore file excludes ``rel`` (last matching pattern wins, ``!`` negates)."""
    if not ignore_file.is_file():
        return False
    hit = False
    for raw in ignore_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        if _pattern_covers(line[1:] if negate else line, rel):
            hit = not negate
    return hit


def _git_ignored(project: Path, rel: str) -> bool:
    try:
        r = subprocess.run(
            ["git", "-C", str(project), "check-ignore", "-q", rel], capture_output=True
        )
    except FileNotFoundError:
        r = None
    if r is not None and r.returncode in (0, 1):
        return r.returncode == 0
    return ignore_file_covers(project / ".gitignore", rel)  # not a repo / no git


def ignore_problem(project: Path, rel: str) -> str | None:
    """Why ``rel`` must not receive a secret in ``project``, or None when it is safe."""
    if not _git_ignored(project, rel):
        return "not git-ignored"
    if not ignore_file_covers(project / ".dockerignore", rel):
        return "not docker-ignored"
    return None


@dataclass
class SeedResult:
    source: Path
    copied: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)  # already present; not overwritten
    refused: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)


def _write_private(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(src.read_bytes())
    os.chmod(dst, 0o600)


def seed_secrets(
    project: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
    source: Path | None = None,
) -> SeedResult | None:
    """Copy personal credential files into ``project``. None when there is no secrets folder."""
    src_root = source or secrets_dir()
    if not src_root.is_dir():
        return None
    result = SeedResult(src_root)
    for src in sorted(p for p in src_root.rglob("*") if p.is_file()):
        rel = src.relative_to(src_root).as_posix()
        if "/" not in rel and rel.upper().startswith("README"):
            continue
        if not is_credentials_path(rel):
            result.refused.append((rel, f"not a credentials path ({CREDENTIALS_PATTERN})"))
            continue
        problem = ignore_problem(project, rel)
        if problem:
            result.refused.append((rel, f"{problem} in this project; run `kat upgrade`"))
            continue
        dst = project / rel
        if dst.exists() and not force:
            result.kept.append(rel)
            continue
        if not dry_run:
            _write_private(src, dst)
        result.copied.append(rel)
    return result
