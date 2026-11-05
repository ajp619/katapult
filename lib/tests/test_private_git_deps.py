"""Tests for private git dependency support (katx build secret + migration 0004)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

from click.testing import CliRunner

from katapult import commands
from katapult.cli import main
from katapult.migrations.m0004_private_git_deps import MARKER

GIT_ID = "0004_private_git_deps"
TEMPLATE = (
    Path(commands.__file__).resolve().parent / "project_template" / "{{cookiecutter.project_slug}}"
)
CONFD_COPY = "COPY ./build/conf/supervisor/conf.d/* /etc/supervisor/conf.d/"
SSH_LOCK = 'source = { git = "ssh://git@example.org/org/repo.git?subdirectory=lib&rev=v1#abc" }\n'


def _make_project(root: Path) -> Path:
    proj = root / "proj"
    (proj / ".katapult").mkdir(parents=True)
    (proj / ".katapult" / "HOSTVARS").write_text("HOST=localhost\n")
    (proj / ".katapult" / "katx").write_text("#!/bin/bash\n# old katx\n")
    build = proj / "build"
    build.mkdir()
    (build / "BuildConfig").write_text(
        "IMAGE_ROOT=katapult\nPROJECT_NAME=my_project\nPYTHON_VERSION=3.12\n"
    )
    (build / "Dockerfile").write_text(
        "FROM ubuntu:24.04\n"
        "COPY ./build/conf/supervisor/supervisord.conf /etc/supervisor/supervisord.conf\n"
        f"{CONFD_COPY}\n"
        "USER ${USERNAME}\n"
        "RUN uv sync --frozen\n"
        'CMD ["/usr/bin/supervisord"]\n'
    )
    (build / "docker-compose.yml").write_text("services:\n  my_project:\n    volumes:\n")
    (proj / "lib" / "src").mkdir(parents=True)
    return proj


def _upgrade(proj: Path):
    return CliRunner().invoke(main, ["upgrade", str(proj), "--allow-dirty"])


# --- migration 0004 -----------------------------------------------------------


def test_upgrade_inserts_private_git_step_after_confd_copy(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    result = _upgrade(proj)
    assert result.exit_code == 0, result.output

    df = (proj / "build" / "Dockerfile").read_text()
    assert df.count(MARKER) == 1
    block_at = df.index("katapult migration 0004")
    assert df.index(CONFD_COPY) < block_at < df.index('CMD ["/usr/bin/supervisord"]')
    block = df[block_at : df.index("USER root", block_at)]
    assert "openssh-client" in block
    assert "required=false" in block
    assert "UserKnownHostsFile=/content/build/conf/ssh/known_hosts" in block
    assert "StrictHostKeyChecking=yes" in block
    # the step runs as the app user and hands root back to the rest of the Dockerfile
    assert "USER ${USERNAME}\nRUN --mount=type=cache" in block


def test_upgrade_private_git_step_is_idempotent(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    assert _upgrade(proj).exit_code == 0
    df_first = (proj / "build" / "Dockerfile").read_text()

    manifest = proj / ".katapult" / "manifest.json"
    data = json.loads(manifest.read_text())
    data["applied_migrations"].remove(GIT_ID)
    manifest.write_text(json.dumps(data))
    assert _upgrade(proj).exit_code == 0

    assert (proj / "build" / "Dockerfile").read_text() == df_first


def test_upgrade_skips_dockerfile_that_already_mounts_the_secret(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    df = proj / "build" / "Dockerfile"
    df.write_text(df.read_text() + "RUN --mount=type=secret,id=git_ssh_key true\n")

    assert _upgrade(proj).exit_code == 0
    after = df.read_text()
    assert after.count(MARKER) == 1
    assert "katapult migration 0004" not in after


def test_template_dockerfile_supports_private_git_deps_natively() -> None:
    df = (TEMPLATE / "build" / "Dockerfile").read_text()
    # fresh projects are stamped with every migration, so the template must not need 0004
    assert MARKER in df
    assert "openssh-client" in df
    bake = df[df.index("ARG SERVICES") :]
    assert "--mount=type=secret,id=git_ssh_key,uid=${UID_},required=false" in bake
    assert "GIT_SSH_COMMAND=" in bake


# --- katx build gate ----------------------------------------------------------


def _katx_build_args(tmp_path: Path, *, key: bool, lock: str | None) -> str:
    """Run the template katx `build` against a fake docker; return its argv."""
    proj = _make_project(tmp_path)
    katx = proj / ".katapult" / "katx"
    katx.write_text((TEMPLATE / ".katapult" / "katx").read_text())
    katx.chmod(katx.stat().st_mode | stat.S_IEXEC)
    if key:
        (proj / "build" / ".git_ssh_credentials").write_text("not a real key\n")
    if lock is not None:
        (proj / "lib" / "uv.lock").write_text(lock)

    bindir = tmp_path / "bin"
    bindir.mkdir()
    record = tmp_path / "docker_args"
    for name, body in {
        "docker": f'#!/bin/bash\necho "$@" >> {record}\n',
        "tput": "#!/bin/bash\nexit 0\n",
    }.items():
        exe = bindir / name
        exe.write_text(body)
        exe.chmod(0o755)

    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "TERM": "dumb"}
    proc = subprocess.run(
        ["bash", str(katx), "build"], cwd=proj, env=env, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    return record.read_text()


def test_katx_build_passes_secret_with_key_and_git_ssh_lock(tmp_path: Path) -> None:
    args = _katx_build_args(tmp_path, key=True, lock=SSH_LOCK)
    assert "--secret id=git_ssh_key,src=" in args
    assert args.split("src=", 1)[1].split()[0].endswith("build/.git_ssh_credentials")


def test_katx_build_no_secret_without_git_ssh_source(tmp_path: Path) -> None:
    lock = 'source = { registry = "https://pypi.org/simple" }\n'
    assert "--secret" not in _katx_build_args(tmp_path, key=True, lock=lock)


def test_katx_build_no_secret_without_key(tmp_path: Path) -> None:
    assert "--secret" not in _katx_build_args(tmp_path, key=False, lock=SSH_LOCK)
