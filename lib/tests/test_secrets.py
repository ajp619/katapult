"""Tests for katapult.secrets (personal, per-machine credential seeding)."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

from katapult.secrets import (
    ignore_file_covers,
    ignore_problem,
    is_credentials_path,
    secrets_dir,
    seed_secrets,
)

IGNORES = "build/.*_credentials\n!build/.*_credentials.example\n"


def _project(root: Path, *, gitignore: str = IGNORES, dockerignore: str = IGNORES) -> Path:
    proj = root / "proj"
    (proj / "build").mkdir(parents=True)
    if gitignore is not None:
        (proj / ".gitignore").write_text(gitignore)
    if dockerignore is not None:
        (proj / ".dockerignore").write_text(dockerignore)
    return proj


def _secrets(root: Path, files: dict[str, str]) -> Path:
    src = root / "secrets"
    src.mkdir()
    for rel, text in files.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(text)
    return src


# --- secrets_dir ---------------------------------------------------------------


def test_secrets_dir_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KATAPULT_SECRETS_DIR", str(tmp_path / "mine"))
    assert secrets_dir() == tmp_path / "mine"


def test_secrets_dir_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("KATAPULT_SECRETS_DIR")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert secrets_dir() == tmp_path / "cfg" / "katapult" / "secrets"


def test_secrets_dir_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("KATAPULT_SECRETS_DIR")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert secrets_dir() == tmp_path / ".config" / "katapult" / "secrets"


# --- path rules ------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel, ok",
    [
        ("build/.agent_credentials", True),
        ("build/.aws_credentials", True),
        ("build/.agent_credentials.example", False),
        ("build/agent.env", False),
        ("build/sub/.agent_credentials", False),
        (".agent_credentials", False),
        ("lib/.agent_credentials", False),
    ],
)
def test_is_credentials_path(rel: str, ok: bool) -> None:
    assert is_credentials_path(rel) is ok


def test_ignore_file_covers_negation_and_order(tmp_path: Path) -> None:
    f = tmp_path / "ignore"
    f.write_text("# c\nbuild/.*_credentials\n!build/.agent_credentials\n")
    assert ignore_file_covers(f, "build/.aws_credentials")
    assert not ignore_file_covers(f, "build/.agent_credentials")
    assert not ignore_file_covers(tmp_path / "missing", "build/.aws_credentials")


@pytest.mark.parametrize("pattern", ["build/", "/build/.*_credentials", ".*_credentials", "**/.*_credentials"])
def test_ignore_file_covers_pattern_forms(tmp_path: Path, pattern: str) -> None:
    f = tmp_path / "ignore"
    f.write_text(pattern + "\n")
    assert ignore_file_covers(f, "build/.agent_credentials")


def test_ignore_problem_uses_git_when_in_repo(tmp_path: Path) -> None:
    proj = _project(tmp_path, gitignore=None)
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    (proj / ".git" / "info" / "exclude").write_text("build/.*_credentials\n")
    assert ignore_problem(proj, "build/.agent_credentials") is None


def test_ignore_problem_reports_missing_ignores(tmp_path: Path) -> None:
    proj = _project(tmp_path, gitignore="", dockerignore=IGNORES)
    assert ignore_problem(proj, "build/.agent_credentials") == "not git-ignored"
    proj2 = _project(tmp_path / "b", dockerignore=None)
    assert ignore_problem(proj2, "build/.agent_credentials") == "not docker-ignored"


# --- seed_secrets -------------------------------------------------------------------


def test_seed_returns_none_without_secrets_folder(tmp_path: Path) -> None:
    proj = _project(tmp_path)
    assert seed_secrets(proj, source=tmp_path / "absent") is None


def test_seed_copies_create_only_with_mode_600(tmp_path: Path) -> None:
    proj = _project(tmp_path)
    src = _secrets(tmp_path, {"build/.agent_credentials": "KEY=new\n", "README.md": "doc"})
    (proj / "build" / ".aws_credentials").write_text("AWS=mine\n")
    (src / "build" / ".aws_credentials").write_text("AWS=other\n")

    result = seed_secrets(proj, source=src)

    assert result.copied == ["build/.agent_credentials"]
    assert result.kept == ["build/.aws_credentials"]
    assert result.refused == []
    dst = proj / "build" / ".agent_credentials"
    assert dst.read_text() == "KEY=new\n"
    assert stat.S_IMODE(dst.stat().st_mode) == 0o600
    assert (proj / "build" / ".aws_credentials").read_text() == "AWS=mine\n"
    assert not (proj / "README.md").exists()


def test_seed_force_overwrites(tmp_path: Path) -> None:
    proj = _project(tmp_path)
    src = _secrets(tmp_path, {"build/.agent_credentials": "KEY=rotated\n"})
    dst = proj / "build" / ".agent_credentials"
    dst.write_text("KEY=old\n")
    dst.chmod(0o644)

    result = seed_secrets(proj, source=src, force=True)

    assert result.copied == ["build/.agent_credentials"]
    assert dst.read_text() == "KEY=rotated\n"
    assert stat.S_IMODE(dst.stat().st_mode) == 0o600


def test_seed_dry_run_writes_nothing(tmp_path: Path) -> None:
    proj = _project(tmp_path)
    src = _secrets(tmp_path, {"build/.agent_credentials": "KEY=x\n"})
    result = seed_secrets(proj, source=src, dry_run=True)
    assert result.copied == ["build/.agent_credentials"]
    assert not (proj / "build" / ".agent_credentials").exists()


def test_seed_refuses_non_credentials_paths(tmp_path: Path) -> None:
    proj = _project(tmp_path)
    src = _secrets(
        tmp_path,
        {"build/agent.env": "AGENT_BACKEND=cursor\n", "lib/.agent_credentials": "x"},
    )
    result = seed_secrets(proj, source=src)
    assert result.copied == []
    assert {rel for rel, _ in result.refused} == {"build/agent.env", "lib/.agent_credentials"}
    assert not (proj / "build" / "agent.env").exists()


def test_seed_refuses_when_not_ignored(tmp_path: Path) -> None:
    proj = _project(tmp_path, dockerignore=None)
    src = _secrets(tmp_path, {"build/.agent_credentials": "KEY=x\n"})
    result = seed_secrets(proj, source=src)
    assert result.copied == []
    (rel, reason), = result.refused
    assert rel == "build/.agent_credentials"
    assert "docker-ignored" in reason and "kat upgrade" in reason
    assert not (proj / "build" / ".agent_credentials").exists()


def test_seed_uses_secrets_dir_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _project(tmp_path)
    src = _secrets(tmp_path, {"build/.agent_credentials": "KEY=x\n"})
    monkeypatch.setenv("KATAPULT_SECRETS_DIR", str(src))
    assert seed_secrets(proj).copied == ["build/.agent_credentials"]
