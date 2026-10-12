"""Execution engine for katapult project migrations.

Reads a project's recorded version from ``.katapult/manifest.json``, determines which
registered migrations are pending, builds a declarative action plan, and applies it with
a conservative, never-clobber posture (dry-run preview, clean-git guard, sidecars for
locally customized files, and a hard allowlist that keeps user code untouched).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

import click

from katapult.migrations import REGISTRY, all_ids
from katapult.migrations.base import (
    SCAFFOLDING_PREFIXES,
    Action,
    AddFile,
    EnsureBlock,
    Migration,
)

MANIFEST_REL = Path(".katapult") / "manifest.json"


# --- version + manifest -------------------------------------------------------


def katapult_version() -> str:
    """Installed katapult version, or a sentinel when it cannot be determined."""
    try:
        return metadata.version("katapult")
    except metadata.PackageNotFoundError:  # pragma: no cover - dev edge case
        return "0+unknown"


def manifest_path(project: Path) -> Path:
    return project / MANIFEST_REL


def read_manifest(project: Path) -> dict:
    """Return the parsed manifest, or ``{}`` when absent (= baseline project)."""
    p = manifest_path(project)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text() or "{}")
    except json.JSONDecodeError as e:
        raise click.ClickException(f"{p} is not valid JSON: {e}")
    return data if isinstance(data, dict) else {}


def applied_ids(project: Path) -> list[str]:
    data = read_manifest(project)
    ids = data.get("applied_migrations", [])
    return [str(x) for x in ids] if isinstance(ids, list) else []


def write_manifest(project: Path, applied: list[str], version: str | None = None) -> None:
    p = manifest_path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "katapult_version": version or katapult_version(),
        "applied_migrations": applied,
    }
    p.write_text(json.dumps(payload, indent=2) + "\n")


def stamp_fresh_project(project: Path) -> None:
    """Mark a freshly generated project as already at the latest template.

    Called from ``kat init``: a new project is created from the current template, so all
    registered migrations are considered applied and nothing is pending on a later
    ``kat upgrade``.
    """
    write_manifest(project, all_ids())


# --- context reconstruction ---------------------------------------------------


def _read_field(text: str, key: str) -> str | None:
    import re

    m = re.search(rf"^{re.escape(key)}=(.+)$", text, re.MULTILINE)
    return m.group(1).strip().strip('"').strip("'") if m else None


def reconstruct_context(project: Path) -> dict[str, str]:
    """Rebuild the cookiecutter context needed to render templated scaffolding.

    Templated scaffolding only references ``project_slug``, ``python_version`` and
    ``host`` -- all recoverable from ``build/BuildConfig`` + ``.katapult/HOSTVARS`` + the
    directory name. ``project_name`` (the human title) is not recoverable, so it is
    aliased to the slug (unused by scaffolding).
    """
    # Reuse the canonical reader so error messages stay consistent.
    from katapult import commands

    _, slug, _ = commands._read_build_config_identity(project)

    bc = project / "build" / "BuildConfig"
    py = _read_field(bc.read_text(), "PYTHON_VERSION") if bc.is_file() else None

    hostvars = project / ".katapult" / "HOSTVARS"
    host = _read_field(hostvars.read_text(), "HOST") if hostvars.is_file() else None

    return {
        "project_slug": slug,
        "project_name": slug,
        "python_version": py or "3.12",
        "host": host or "localhost",
    }


# --- template access + rendering ----------------------------------------------


def template_slug_dir() -> Path:
    """Absolute path to the packaged template's ``{{cookiecutter.project_slug}}`` root."""
    from katapult import commands

    return (
        Path(commands.__file__).resolve().parent
        / "project_template"
        / "{{cookiecutter.project_slug}}"
    )


def render(text: str, ctx: dict[str, str]) -> str:
    import jinja2

    env = jinja2.Environment(
        keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
        autoescape=False,
    )
    return env.from_string(text).render(cookiecutter=ctx)


# --- action application -------------------------------------------------------


@dataclass
class ActionResult:
    status: str  # created | overwritten | patched | noop | sidecar | missing
    target: str
    note: str = ""

    @property
    def changed(self) -> bool:
        return self.status in {"created", "overwritten", "patched", "sidecar"}


def validate_action_path(action: Action) -> None:
    """Hard guard: a migration may only touch scaffolding, never user code."""
    rel = action.target.replace("\\", "/")
    if rel.startswith("/") or ".." in Path(rel).parts:
        raise click.ClickException(
            f"Migration action targets an unsafe path: {action.target!r}"
        )
    if not any(rel == p.rstrip("/") or rel.startswith(p) for p in SCAFFOLDING_PREFIXES):
        raise click.ClickException(
            f"Migration action targets {action.target!r}, outside the scaffolding "
            f"allowlist {SCAFFOLDING_PREFIXES}."
        )


def _source_text(action: AddFile, ctx: dict[str, str]) -> str:
    src = template_slug_dir() / action.src_rel
    if not src.is_file():
        raise click.ClickException(
            f"Template source missing for migration: {action.src_rel!r} "
            f"(expected under {template_slug_dir()})."
        )
    text = src.read_text()
    return render(text, ctx) if action.templated else text


def _apply_add_file(
    action: AddFile, project: Path, ctx: dict[str, str], *, dry_run: bool
) -> ActionResult:
    want = _source_text(action, ctx)
    dst = project / action.dst_rel
    if not dst.exists():
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(want)
        return ActionResult("created", action.dst_rel)
    current = dst.read_text()
    if current == want:
        return ActionResult("noop", action.dst_rel, "already up to date")
    if action.overwrite_ok:
        if not dry_run:
            dst.write_text(want)
        return ActionResult("overwritten", action.dst_rel, "refreshed agnostic file")
    sidecar = dst.with_name(dst.name + ".katapult-new")
    if not dry_run:
        sidecar.write_text(want)
    return ActionResult(
        "sidecar",
        action.dst_rel,
        f"locally customized; wrote {sidecar.name} for you to merge",
    )


def _insert_block(text: str, action: EnsureBlock) -> tuple[str | None, str]:
    """Return (new_text, note). new_text is None when the anchor was not found."""
    import re

    block = action.block if action.block.endswith("\n") else action.block + "\n"
    lines = text.splitlines(keepends=True)

    if action.after is not None:
        for i, line in enumerate(lines):
            if action.after in line:
                lines.insert(i + 1, block)
                return "".join(lines), f"inserted after line {i + 1}"
        return None, f"anchor not found: after {action.after!r}"

    if action.before_regex is not None:
        pat = re.compile(action.before_regex, re.MULTILINE)
        for i, line in enumerate(lines):
            if pat.search(line):
                lines.insert(i, block)
                return "".join(lines), f"inserted before line {i + 1}"
        return None, f"anchor not found: before /{action.before_regex}/"

    if text and not text.endswith("\n"):
        block = "\n" + block
    return text + block, "appended at end of file"


def _apply_ensure_block(
    action: EnsureBlock, project: Path, *, dry_run: bool
) -> ActionResult:
    dst = project / action.dst_rel
    if not dst.is_file():
        return ActionResult(
            "missing", action.dst_rel, "target file absent; skipped (nothing to patch)"
        )
    text = dst.read_text()
    if action.marker in text:
        return ActionResult("noop", action.dst_rel, "already present")
    new_text, note = _insert_block(text, action)
    if new_text is None:
        sidecar = dst.with_name(dst.name + ".katapult-patch")
        if not dry_run:
            block = action.block if action.block.endswith("\n") else action.block + "\n"
            # Several EnsureBlocks may target the same file (e.g. multiple
            # Dockerfile patches). When each can't find its anchor, accumulate the
            # blocks in one sidecar instead of clobbering, and stay idempotent
            # across re-runs by only appending blocks not already present.
            existing = sidecar.read_text() if sidecar.exists() else ""
            if action.block.strip() not in existing:
                sep = "" if (not existing or existing.endswith("\n")) else "\n"
                existing = existing + sep + block
            sidecar.write_text(existing)
        return ActionResult(
            "sidecar",
            action.dst_rel,
            f"{note}; wrote {sidecar.name} for you to apply manually",
        )
    if not dry_run:
        dst.write_text(new_text)
    return ActionResult("patched", action.dst_rel, note)


def apply_action(
    action: Action, project: Path, ctx: dict[str, str], *, dry_run: bool
) -> ActionResult:
    validate_action_path(action)
    if isinstance(action, AddFile):
        return _apply_add_file(action, project, ctx, dry_run=dry_run)
    if isinstance(action, EnsureBlock):
        return _apply_ensure_block(action, project, dry_run=dry_run)
    raise click.ClickException(f"Unknown migration action: {action!r}")  # pragma: no cover


# --- planning + orchestration -------------------------------------------------


def pending_migrations(project: Path, to_id: str | None = None) -> list[Migration]:
    done = set(applied_ids(project))
    pending: list[Migration] = []
    for mig in REGISTRY:
        if mig.id in done:
            continue
        pending.append(mig)
        if to_id is not None and mig.id == to_id:
            break
    if to_id is not None and all(m.id != to_id for m in REGISTRY):
        raise click.ClickException(f"Unknown migration id: {to_id!r}.")
    return pending


def _git_is_clean(project: Path) -> tuple[bool, str]:
    """Return (clean, reason). clean=False with a reason when dirty or not a repo."""
    try:
        r = subprocess.run(
            ["git", "-C", str(project), "status", "--porcelain"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:  # pragma: no cover - git not installed
        return False, "git is not installed"
    if r.returncode != 0:
        return False, "not a git repository (no rollback safety)"
    if r.stdout.strip():
        return False, "working tree has uncommitted changes"
    return True, ""


def run_upgrade(
    project: Path,
    *,
    dry_run: bool = False,
    allow_dirty: bool = False,
    to_id: str | None = None,
    echo=click.echo,
) -> list[ActionResult]:
    """Apply pending migrations to ``project``. Returns the flattened action results."""
    ctx = reconstruct_context(project)
    pending = pending_migrations(project, to_id)

    cur = katapult_version()
    recorded = read_manifest(project).get("katapult_version", "(unstamped)")
    echo(f"Project: {project}")
    echo(f"Recorded katapult version: {recorded}; installed: {cur}")

    if not pending:
        echo("Already up to date; no migrations pending.")
        return []

    echo(f"Pending migrations ({len(pending)}):")
    for mig in pending:
        echo(f"  - {mig.id}  ({mig.katapult_version})  {mig.description}")

    if not dry_run:
        clean, reason = _git_is_clean(project)
        if not clean and not allow_dirty:
            raise click.ClickException(
                f"Refusing to upgrade: {reason}. Commit or stash first, or pass "
                "--allow-dirty to override."
            )
        if not clean:
            echo(f"warning: {reason}; proceeding because --allow-dirty was given.")

    echo("")
    echo("Dry run -- no files will be written:" if dry_run else "Applying:")

    results: list[ActionResult] = []
    newly_applied: list[str] = []
    for mig in pending:
        echo(f"[{mig.id}]")
        for action in mig.actions:
            res = apply_action(action, project, ctx, dry_run=dry_run)
            results.append(res)
            suffix = f" -- {res.note}" if res.note else ""
            echo(f"  {res.status:>11}  {res.target}{suffix}")
        newly_applied.append(mig.id)

    changed = [r for r in results if r.changed]
    sidecars = [r for r in results if r.status == "sidecar"]

    if dry_run:
        echo("")
        echo(
            f"Would apply {len(newly_applied)} migration(s): "
            f"{len(changed)} file change(s), {len(sidecars)} sidecar(s). "
            "Re-run without --dry-run to apply."
        )
        return results

    write_manifest(project, applied_ids(project) + newly_applied, cur)
    echo("")
    echo(
        f"Applied {len(newly_applied)} migration(s): {len(changed)} file change(s), "
        f"{len(sidecars)} sidecar(s). Stamped {MANIFEST_REL}."
    )
    if sidecars:
        echo("")
        echo("Review and merge these sidecars (your files were left untouched):")
        for r in sidecars:
            echo(f"  - {r.target}: {r.note}")
    echo("")
    echo("Next steps: rebuild the container (`.katapult/katx rebuild`) to pick up the")
    echo("new scaffolding, then optionally `kat register-mcp <project>` to wire the hub.")
    return results
