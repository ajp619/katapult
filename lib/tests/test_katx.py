"""Tests for the template katx: failure propagation and running without a terminal."""

from __future__ import annotations

import os
import pty
import subprocess
from pathlib import Path

from katapult import commands

TEMPLATE_KATX = (
    Path(commands.__file__).resolve().parent
    / "project_template"
    / "{{cookiecutter.project_slug}}"
    / ".katapult"
    / "katx"
)


def _project(tmp_path: Path, *, fail: str = "") -> tuple[Path, Path, dict[str, str]]:
    """A minimal project with the template katx and fake docker/tput on PATH.

    The fake docker records each call in `docker_args` and exits 3 when its first
    argument equals `fail`; the fake tput records that it was called.
    """
    proj = tmp_path / "proj"
    (proj / ".katapult").mkdir(parents=True)
    (proj / ".katapult" / "HOSTVARS").write_text("HOST=localhost\n")
    katx = proj / ".katapult" / "katx"
    katx.write_text(TEMPLATE_KATX.read_text())
    (proj / "build").mkdir()
    (proj / "build" / "BuildConfig").write_text(
        "IMAGE_ROOT=katapult\nPROJECT_NAME=my_project\nPYTHON_VERSION=3.12\n"
    )

    record = tmp_path / "docker_args"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in {
        "docker": (
            f'#!/bin/bash\necho "$@" >> {record}\n'
            f'if [ "$1" = "{fail}" ]; then exit 3; fi\n'
        ),
        "tput": f"#!/bin/bash\necho tput >> {tmp_path / 'tput_calls'}\n",
    }.items():
        exe = bindir / name
        exe.write_text(body)
        exe.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "TERM": "dumb"}
    return katx, record, env


def _run(katx: Path, env: dict[str, str], *args: str, stdin=subprocess.DEVNULL):
    return subprocess.run(
        ["bash", str(katx), *args],
        cwd=katx.parent.parent,
        env=env,
        stdin=stdin,
        capture_output=True,
        text=True,
    )


def _calls(record: Path) -> list[str]:
    return record.read_text().splitlines() if record.exists() else []


def test_build_failure_exits_nonzero(tmp_path: Path) -> None:
    katx, record, env = _project(tmp_path, fail="build")
    proc = _run(katx, env, "build")
    assert proc.returncode == 3
    assert "docker build failed (exit 3)" in proc.stderr


def test_rebuild_stops_before_up_when_build_fails(tmp_path: Path) -> None:
    katx, record, env = _project(tmp_path, fail="build")
    proc = _run(katx, env, "rebuild")
    assert proc.returncode == 3
    calls = _calls(record)
    assert calls[-1].startswith("build ")
    assert not any(" up -d" in c for c in calls)


def test_rebuild_runs_down_build_up_when_build_succeeds(tmp_path: Path) -> None:
    katx, record, env = _project(tmp_path)
    proc = _run(katx, env, "rebuild")
    assert proc.returncode == 0, proc.stderr
    calls = _calls(record)
    assert calls[0].startswith("compose ") and calls[0].endswith(" down")
    assert calls[1].startswith("build ")
    assert calls[2].startswith("compose ") and calls[2].endswith(" up -d")


def test_render_failure_exits_nonzero(tmp_path: Path) -> None:
    katx, record, env = _project(tmp_path, fail="exec")
    proc = _run(katx, env, "render")
    assert proc.returncode == 3
    assert "render failed (exit 3)" in proc.stderr


def test_exec_commands_work_without_a_terminal(tmp_path: Path) -> None:
    katx, record, env = _project(tmp_path)
    for verb in ("render", "status", "mcp", "jlab"):
        assert _run(katx, env, verb).returncode == 0
    execs = [c.split() for c in _calls(record) if c.startswith("exec ")]
    assert len(execs) == 4
    for argv in execs:
        assert argv[1] == "-i"
    # no cursor juggling (and no tput noise) when output is captured
    assert not (tmp_path / "tput_calls").exists()


def test_exec_requests_a_tty_when_stdin_is_a_terminal(tmp_path: Path) -> None:
    katx, record, env = _project(tmp_path)
    leader, follower = pty.openpty()
    try:
        assert _run(katx, env, "status", stdin=follower).returncode == 0
    finally:
        os.close(follower)
        os.close(leader)
    assert _calls(record)[0].split()[1] == "-it"
