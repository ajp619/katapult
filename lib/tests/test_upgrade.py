"""Tests for `kat upgrade` and the migration engine (katapult.migrations)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from katapult import commands
from katapult.cli import main
from katapult.migrations import all_ids
from katapult.migrations.base import AddFile, EnsureBlock
from katapult.migrations.runner import (
    reconstruct_context,
    read_manifest,
    stamp_fresh_project,
    validate_action_path,
)

MCP_ID = "0001_add_internal_mcp"


# --- fixtures -----------------------------------------------------------------


def _make_old_project(root: Path) -> Path:
    """A minimal pre-MCP katapult project (no mcp/, short katx, no MCP_SERVER)."""
    proj = root / "proj"
    (proj / ".katapult").mkdir(parents=True)
    (proj / ".katapult" / "HOSTVARS").write_text("HOST=localhost")
    (proj / ".katapult" / "katx").write_text("#!/bin/bash\n# old katx\n")

    build = proj / "build"
    build.mkdir()
    (build / "BuildConfig").write_text(
        "IMAGE_ROOT=katapult\nPROJECT_NAME=my_project\nPYTHON_VERSION=3.12\n"
    )
    (build / "Dockerfile").write_text(
        "FROM ubuntu:24.04\n"
        "COPY ./build/conf/supervisor/supervisord.conf /etc/supervisor/supervisord.conf\n"
        "COPY ./build/conf/supervisor/conf.d/* /etc/supervisor/conf.d/\n"
        'CMD ["/usr/bin/supervisord"]\n'
    )
    (build / "docker-compose.yml").write_text(
        "services:\n"
        "  my_project:\n"
        "    volumes:\n"
        "      - ../.:/content/\n"
        "      - /content/lib/.venv\n"
        "      - /content/nbk/.venv\n"
        "    env_file:\n"
        "      - .aws_credentials\n"
        "    networks:\n"
        "      - ${IMAGE_ROOT}\n"
        "networks:\n"
        "  katapult:\n"
        "    name: ${IMAGE_ROOT}\n"
        "    external: true\n"
    )

    # User code that must never be touched by an upgrade.
    (proj / "lib" / "src").mkdir(parents=True)
    (proj / "lib" / "src" / "sentinel.py").write_text("USER = 'do not touch'\n")
    return proj


def _upgrade(proj: Path, *args: str):
    return CliRunner().invoke(main, ["upgrade", str(proj), *args])


# --- context reconstruction ---------------------------------------------------


def test_reconstruct_context_from_buildconfig_and_hostvars(tmp_path: Path) -> None:
    proj = _make_old_project(tmp_path)
    ctx = reconstruct_context(proj)
    assert ctx["project_slug"] == "my_project"
    assert ctx["python_version"] == "3.12"
    assert ctx["host"] == "localhost"


# --- allowlist guard ----------------------------------------------------------


def test_validate_action_path_rejects_user_code() -> None:
    with pytest.raises(commands.click.ClickException):
        validate_action_path(AddFile("lib/pyproject.toml", "lib/src/evil.py"))


def test_validate_action_path_rejects_parent_escape() -> None:
    with pytest.raises(commands.click.ClickException):
        validate_action_path(EnsureBlock("build/../../x", marker="m", block="b"))


def test_validate_action_path_allows_scaffolding() -> None:
    for dst in ("mcp/server.py", "build/Dockerfile", ".katapult/katx"):
        validate_action_path(AddFile("x", dst))  # does not raise


# --- dry run ------------------------------------------------------------------


def test_upgrade_dry_run_writes_nothing(tmp_path: Path) -> None:
    proj = _make_old_project(tmp_path)
    before_dockerfile = (proj / "build" / "Dockerfile").read_text()

    result = _upgrade(proj, "--dry-run", "--allow-dirty")

    assert result.exit_code == 0, result.output
    assert MCP_ID in result.output
    assert "Would apply" in result.output
    # nothing written
    assert not (proj / "mcp").exists()
    assert not (proj / ".katapult" / "manifest.json").exists()
    assert (proj / "build" / "Dockerfile").read_text() == before_dockerfile
    assert "ARG MCP_SERVER" not in (proj / "build" / "Dockerfile").read_text()


# --- apply --------------------------------------------------------------------


def test_upgrade_adds_mcp_scaffolding(tmp_path: Path) -> None:
    proj = _make_old_project(tmp_path)

    result = _upgrade(proj, "--allow-dirty")
    assert result.exit_code == 0, result.output

    # mcp/ tree created, with templated files rendered for this project
    assert (proj / "mcp" / "src" / "project_mcp" / "server.py").is_file()
    pyproject = (proj / "mcp" / "pyproject.toml").read_text()
    assert 'requires-python = ">=3.12"' in pyproject
    assert "{{" not in pyproject  # fully rendered, no stray jinja
    config = (proj / "mcp" / "src" / "project_mcp" / "config.py").read_text()
    assert 'PROJECT_SLUG = "my_project"' in config

    # server.py disables MCP's DNS-rebinding Host check so the server is reachable
    # through the project's nginx/traefik proxy (which forwards an external Host).
    server = (proj / "mcp" / "src" / "project_mcp" / "server.py").read_text()
    assert "enable_dns_rebinding_protection=False" in server

    # service wiring
    assert (proj / "build" / "agent.env").is_file()
    assert (proj / "build" / "conf" / "supervisor" / "conf.d" / "mcp.conf").is_file()
    nginx = (proj / "build" / "conf" / "nginx" / "location_blocks" / "mcp.conf").read_text()
    assert "/my_project/mcp" in nginx

    # katx refreshed wholesale (agnostic file)
    katx = (proj / ".katapult" / "katx").read_text()
    assert "# old katx" not in katx
    assert katx == (
        Path(commands.__file__).resolve().parent
        / "project_template"
        / "{{cookiecutter.project_slug}}"
        / ".katapult"
        / "katx"
    ).read_text()


def test_upgrade_patches_build_files(tmp_path: Path) -> None:
    proj = _make_old_project(tmp_path)
    assert _upgrade(proj, "--allow-dirty").exit_code == 0

    dockerfile = (proj / "build" / "Dockerfile").read_text()
    assert "ARG MCP_SERVER=on" in dockerfile
    assert 'rm -f /etc/nginx/conf.d/location_blocks/mcp.conf' in dockerfile
    # mcp service .venv mountpoint pre-created + chowned (older pre-MCP
    # Dockerfiles seed only api/app/lib/nbk, so the anon volume would otherwise
    # come up root-owned and the mcp service would FATAL).
    assert "mkdir -p /content/mcp/.venv && chown" in dockerfile

    compose = (proj / "build" / "docker-compose.yml").read_text()
    assert "- /content/mcp/.venv" in compose
    assert "mcp_agent_store:/home/katapult/.cursor" in compose
    assert "\nvolumes:\n  mcp_agent_store:\n" in compose
    assert "./.agent_credentials" in compose

    buildconfig = (proj / "build" / "BuildConfig").read_text()
    assert "MCP_SERVER=on" in buildconfig


def test_upgrade_writes_manifest(tmp_path: Path) -> None:
    proj = _make_old_project(tmp_path)
    assert _upgrade(proj, "--allow-dirty").exit_code == 0

    manifest = json.loads((proj / ".katapult" / "manifest.json").read_text())
    assert manifest["applied_migrations"] == all_ids()
    assert MCP_ID in manifest["applied_migrations"]
    assert manifest["katapult_version"]


def test_upgrade_does_not_touch_user_code(tmp_path: Path) -> None:
    proj = _make_old_project(tmp_path)
    assert _upgrade(proj, "--allow-dirty").exit_code == 0
    assert (proj / "lib" / "src" / "sentinel.py").read_text() == "USER = 'do not touch'\n"


# --- 0002: seed project rules -------------------------------------------------

RULE_ID = "0002_seed_project_rules"
_SEEDED_RULES = (
    "engineering-standards.mdc",
    "katapult-project-conventions.mdc",
    "notebook-standards.mdc",
)


def _template_rule(name: str) -> str:
    return (
        Path(commands.__file__).resolve().parent
        / "project_template"
        / "{{cookiecutter.project_slug}}"
        / ".cursor"
        / "rules"
        / name
    ).read_text()


def test_upgrade_seeds_project_rules(tmp_path: Path) -> None:
    proj = _make_old_project(tmp_path)
    assert _upgrade(proj, "--allow-dirty").exit_code == 0

    for name in _SEEDED_RULES:
        dst = proj / ".cursor" / "rules" / name
        assert dst.is_file(), name
        assert dst.read_text() == _template_rule(name)

    # the jupytext convention rode along in notebook-standards
    assert "jupytext" in (proj / ".cursor" / "rules" / "notebook-standards.mdc").read_text()


def test_upgrade_rules_are_create_only_no_clobber(tmp_path: Path) -> None:
    proj = _make_old_project(tmp_path)
    rule = proj / ".cursor" / "rules" / "engineering-standards.mdc"
    rule.parent.mkdir(parents=True)
    rule.write_text("# my own standards\n")

    result = _upgrade(proj, "--allow-dirty")
    assert result.exit_code == 0, result.output

    assert rule.read_text() == "# my own standards\n"  # untouched
    sidecar = rule.with_name("engineering-standards.mdc.katapult-new")
    assert sidecar.is_file()
    assert sidecar.read_text() == _template_rule("engineering-standards.mdc")


def test_upgrade_to_stops_after_first_migration(tmp_path: Path) -> None:
    proj = _make_old_project(tmp_path)
    result = _upgrade(proj, "--allow-dirty", "--to", MCP_ID)
    assert result.exit_code == 0, result.output

    # mcp migration applied, rules migration not yet
    assert (proj / "mcp" / "src" / "project_mcp" / "server.py").is_file()
    assert not (proj / ".cursor" / "rules" / "engineering-standards.mdc").exists()
    manifest = json.loads((proj / ".katapult" / "manifest.json").read_text())
    assert manifest["applied_migrations"] == [MCP_ID]

    # a follow-up upgrade applies the remaining migration
    r2 = _upgrade(proj, "--allow-dirty")
    assert r2.exit_code == 0, r2.output
    assert (proj / ".cursor" / "rules" / "engineering-standards.mdc").is_file()
    manifest2 = json.loads((proj / ".katapult" / "manifest.json").read_text())
    assert manifest2["applied_migrations"] == [MCP_ID, RULE_ID]


# --- idempotency --------------------------------------------------------------


def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    proj = _make_old_project(tmp_path)
    assert _upgrade(proj, "--allow-dirty").exit_code == 0
    compose_after_first = (proj / "build" / "docker-compose.yml").read_text()
    katx_after_first = (proj / ".katapult" / "katx").read_text()
    dockerfile_after_first = (proj / "build" / "Dockerfile").read_text()

    r2 = _upgrade(proj, "--allow-dirty")
    assert r2.exit_code == 0, r2.output
    assert "Already up to date" in r2.output
    # no further edits (the marker-guarded EnsureBlocks must not re-insert)
    assert (proj / "build" / "docker-compose.yml").read_text() == compose_after_first
    assert (proj / ".katapult" / "katx").read_text() == katx_after_first
    assert (proj / "build" / "Dockerfile").read_text() == dockerfile_after_first
    assert dockerfile_after_first.count("mkdir -p /content/mcp/.venv && chown") == 1


# --- conservative conflict handling ------------------------------------------


def test_upgrade_sidecars_customized_file_instead_of_clobbering(tmp_path: Path) -> None:
    proj = _make_old_project(tmp_path)
    # Pre-create a locally customized (templated) target.
    nginx = proj / "build" / "conf" / "nginx" / "location_blocks" / "mcp.conf"
    nginx.parent.mkdir(parents=True)
    nginx.write_text("# my custom mcp proxy\n")

    result = _upgrade(proj, "--allow-dirty")
    assert result.exit_code == 0, result.output
    assert "sidecar" in result.output

    # original left untouched; new content offered alongside
    assert nginx.read_text() == "# my custom mcp proxy\n"
    sidecar = nginx.with_name("mcp.conf.katapult-new")
    assert sidecar.is_file()
    assert "/my_project/mcp" in sidecar.read_text()


def test_upgrade_sidecars_dockerfile_when_anchor_missing(tmp_path: Path) -> None:
    proj = _make_old_project(tmp_path)
    # Remove the anchor line so the Dockerfile patch cannot find its insertion point.
    df = proj / "build" / "Dockerfile"
    df.write_text("FROM ubuntu:24.04\nCMD [\"/usr/bin/supervisord\"]\n")

    result = _upgrade(proj, "--allow-dirty")
    assert result.exit_code == 0, result.output

    # Dockerfile unchanged; a .katapult-patch sidecar carries the block.
    assert "ARG MCP_SERVER" not in df.read_text()
    patch = df.with_name("Dockerfile.katapult-patch")
    assert patch.is_file()
    assert "ARG MCP_SERVER=on" in patch.read_text()


# --- git-clean guard ----------------------------------------------------------


def test_upgrade_refuses_without_git_when_not_allowed(tmp_path: Path) -> None:
    proj = _make_old_project(tmp_path)  # tmp_path is not a git repo
    result = _upgrade(proj)  # no --allow-dirty
    assert result.exit_code != 0
    assert "git" in result.output.lower()
    assert not (proj / "mcp").exists()


# --- manifest helpers + fresh stamp ------------------------------------------


def test_stamp_fresh_project_marks_all_migrations_applied(tmp_path: Path) -> None:
    proj = tmp_path / "fresh"
    (proj / ".katapult").mkdir(parents=True)
    stamp_fresh_project(proj)
    data = read_manifest(proj)
    assert data["applied_migrations"] == all_ids()
    assert data["katapult_version"]


def test_pending_is_empty_after_fresh_stamp(tmp_path: Path) -> None:
    proj = _make_old_project(tmp_path)
    stamp_fresh_project(proj)
    result = _upgrade(proj, "--allow-dirty")
    assert result.exit_code == 0, result.output
    assert "Already up to date" in result.output
