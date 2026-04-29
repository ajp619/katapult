"""Tests for katapult.commands helpers and CLI commands."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import docker.errors
import pytest
from click.testing import CliRunner

from katapult import commands
from katapult.cli import main


# --- _merge_overrides ---


def test_merge_overrides_copies_nested_files(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "a" / "b").mkdir(parents=True)
    (src / "a" / "b" / "f.txt").write_text("hello")
    dst.mkdir()

    commands._merge_overrides(src, dst)

    assert (dst / "a" / "b" / "f.txt").read_text() == "hello"


def test_merge_overrides_skips_directories(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "emptydir").mkdir()
    dst.mkdir()

    commands._merge_overrides(src, dst)

    assert not any(dst.rglob("*"))


# --- _apply_copy_without_render ---


def test_apply_copy_without_render_merges_patterns(tmp_path: Path) -> None:
    ignore = tmp_path / "ignore"
    ignore.write_text("*.bin\n# comment\n\n*.png\n")
    cc_path = tmp_path / "cookiecutter.json"
    cc_path.write_text(json.dumps({"_copy_without_render": ["existing.txt"]}))

    commands._apply_copy_without_render(ignore, cc_path)

    data = json.loads(cc_path.read_text())
    assert data["_copy_without_render"] == ["existing.txt", "*.bin", "*.png"]


def test_apply_copy_without_render_deduplicates(tmp_path: Path) -> None:
    ignore = tmp_path / "ignore"
    ignore.write_text("*.bin\n*.bin\n")
    cc_path = tmp_path / "cookiecutter.json"
    cc_path.write_text(json.dumps({"_copy_without_render": ["*.bin"]}))

    commands._apply_copy_without_render(ignore, cc_path)

    data = json.loads(cc_path.read_text())
    assert data["_copy_without_render"] == ["*.bin"]


def test_apply_copy_without_render_non_list_existing_reset(tmp_path: Path) -> None:
    ignore = tmp_path / "ignore"
    ignore.write_text("*.md\n")
    cc_path = tmp_path / "cookiecutter.json"
    cc_path.write_text(json.dumps({"_copy_without_render": "bad"}))

    commands._apply_copy_without_render(ignore, cc_path)

    data = json.loads(cc_path.read_text())
    assert data["_copy_without_render"] == ["*.md"]


def test_apply_copy_without_render_missing_ignore_noop(tmp_path: Path) -> None:
    cc_path = tmp_path / "cookiecutter.json"
    cc_path.write_text("{}")

    commands._apply_copy_without_render(tmp_path / "nope", cc_path)

    assert cc_path.read_text() == "{}"


def test_apply_copy_without_render_empty_patterns_noop(tmp_path: Path) -> None:
    ignore = tmp_path / "ignore"
    ignore.write_text("# only comment\n\n")
    cc_path = tmp_path / "cookiecutter.json"
    cc_path.write_text(json.dumps({"_copy_without_render": ["keep"]}))

    commands._apply_copy_without_render(ignore, cc_path)

    assert json.loads(cc_path.read_text())["_copy_without_render"] == ["keep"]


def test_apply_copy_without_render_invalid_json_raises(tmp_path: Path) -> None:
    ignore = tmp_path / "ignore"
    ignore.write_text("*.md\n")
    cc_path = tmp_path / "cookiecutter.json"
    cc_path.write_text("not json")

    with pytest.raises(json.JSONDecodeError):
        commands._apply_copy_without_render(ignore, cc_path)


def test_apply_copy_without_render_missing_cookiecutter_raises(tmp_path: Path) -> None:
    ignore = tmp_path / "ignore"
    ignore.write_text("*.md\n")

    with pytest.raises(FileNotFoundError):
        commands._apply_copy_without_render(ignore, tmp_path / "missing.json")


# --- _override_template_has_content ---


def test_override_template_has_content_false_when_missing(tmp_path: Path) -> None:
    assert not commands._override_template_has_content(tmp_path / "nope")


def test_override_template_has_content_false_when_empty_dir(tmp_path: Path) -> None:
    d = tmp_path / "t"
    d.mkdir()
    assert not commands._override_template_has_content(d)


def test_override_template_has_content_true_with_file(tmp_path: Path) -> None:
    d = tmp_path / "t"
    d.mkdir()
    (d / "f").write_text("x")
    assert commands._override_template_has_content(d)


def test_override_template_has_content_true_nested_file(tmp_path: Path) -> None:
    d = tmp_path / "t"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "f").write_text("x")
    assert commands._override_template_has_content(d)


# --- CLI: rich ---


def test_rich_exits_zero() -> None:
    runner = CliRunner()
    result = runner.invoke(commands.rich, [])
    assert result.exit_code == 0
    assert "Star Wars" in result.output


# --- CLI: config ---


def test_config_appends_bashrc_once(tmp_path: Path) -> None:
    bashrc = tmp_path / ".bashrc"
    bashrc.write_text("# existing\n")

    with patch.object(commands.Path, "home", return_value=tmp_path):
        runner = CliRunner()
        r1 = runner.invoke(commands.config, [])
        r2 = runner.invoke(commands.config, [])

    assert r1.exit_code == 0
    assert "Adding Katapult PATH augmentation" in r1.output
    assert r2.exit_code == 0
    assert "already present" in r2.output
    text = bashrc.read_text()
    assert text.count("Section added by katapult") == 1


def test_config_creates_bashrc(tmp_path: Path) -> None:
    bashrc = tmp_path / ".bashrc"

    with patch.object(commands.Path, "home", return_value=tmp_path):
        runner = CliRunner()
        result = runner.invoke(commands.config, [])

    assert result.exit_code == 0
    assert bashrc.is_file()
    assert "augment_path" in bashrc.read_text()


# --- CLI: init ---


def test_init_no_overrides_calls_cookiecutter_template_dir(tmp_path: Path) -> None:
    template_dir = Path(commands.__file__).resolve().parent / "project_template"

    with patch.object(commands, "cookiecutter") as mock_cc:
        runner = CliRunner()
        result = runner.invoke(main, ["init", "--no-overrides"])

    assert result.exit_code == 0
    mock_cc.assert_called_once_with(str(template_dir))


def test_init_with_overrides_merges_and_calls_cookiecutter(tmp_path: Path) -> None:
    kat = tmp_path / ".katapult"
    (kat / "template").mkdir(parents=True)
    (kat / "template" / "cursor_context" / "x.txt").parent.mkdir(parents=True)
    (kat / "template" / "cursor_context" / "x.txt").write_text("ctx")

    def assert_merged_template(merged_arg: str) -> None:
        # Still inside TemporaryDirectory in init(); merged tree exists until cookiecutter returns.
        merged = Path(merged_arg)
        assert merged.name == "project_template"
        slug_dir = merged / "{{cookiecutter.project_slug}}"
        assert (slug_dir / "cursor_context" / "x.txt").read_text() == "ctx"

    with patch.object(commands.Path, "home", return_value=tmp_path):
        with patch.object(commands, "cookiecutter", side_effect=assert_merged_template):
            runner = CliRunner()
            result = runner.invoke(main, ["init"])

    assert result.exit_code == 0
    assert "Applying overrides" in result.output


def test_init_no_overrides_skips_merge_even_with_template_files(tmp_path: Path) -> None:
    kat = tmp_path / ".katapult"
    (kat / "template").mkdir(parents=True)
    (kat / "template" / "a.txt").write_text("x")
    template_dir = Path(commands.__file__).resolve().parent / "project_template"

    with patch.object(commands.Path, "home", return_value=tmp_path):
        with patch.object(commands, "cookiecutter") as mock_cc:
            runner = CliRunner()
            result = runner.invoke(main, ["init", "--no-overrides"])

    assert result.exit_code == 0
    mock_cc.assert_called_once_with(str(template_dir))
    assert "Applying overrides" not in result.output


# --- CLI: hub ---


def _make_docker_client(
    *,
    network_names: list[str],
    containers: list[tuple[list[str], str]],
) -> MagicMock:
    """containers: list of (image_tags, status)."""
    client = MagicMock()

    class _Net:
        def __init__(self, name: str) -> None:
            self.name = name

    nets = [_Net(n) for n in network_names]

    def list_networks() -> list[_Net]:
        return list(nets)

    def create_network(name: str) -> None:
        nets.append(_Net(name))

    client.networks.list.side_effect = list_networks
    client.networks.create.side_effect = create_network

    class _Img:
        def __init__(self, tags: list[str]) -> None:
            self.tags = tags

    class _Cont:
        def __init__(self, tags: list[str], status: str) -> None:
            self.image = _Img(tags)
            self.status = status

    cont_objs = [_Cont(tags, st) for tags, st in containers]

    def list_containers(all: bool = True) -> list[_Cont]:  # noqa: ARG001
        return cont_objs

    client.containers.list.side_effect = list_containers
    client.containers.run.return_value = object()

    return client


def test_hub_traefik_already_running() -> None:
    client = _make_docker_client(
        network_names=["katapult"],
        containers=[(["traefik:v3.6"], "running")],
    )
    with patch.object(commands.docker, "from_env", return_value=client):
        runner = CliRunner()
        result = runner.invoke(main, ["hub"])

    assert result.exit_code == 0
    assert "already running" in result.output
    client.networks.create.assert_not_called()
    client.containers.run.assert_not_called()


def test_hub_creates_network_and_launches_traefik() -> None:
    client = _make_docker_client(network_names=[], containers=[])
    with patch.object(commands.docker, "from_env", return_value=client):
        runner = CliRunner()
        result = runner.invoke(main, ["hub"], input="y\ny\n")

    assert result.exit_code == 0
    assert "Created 'katapult' network" in result.output
    assert "Launched Traefik" in result.output
    client.networks.create.assert_called_once_with("katapult")
    client.containers.run.assert_called_once()


def test_hub_aborts_when_network_declined() -> None:
    client = _make_docker_client(network_names=[], containers=[])
    with patch.object(commands.docker, "from_env", return_value=client):
        runner = CliRunner()
        result = runner.invoke(main, ["hub"], input="n\n")

    assert result.exit_code == 0
    assert "Aborting" in result.output
    client.networks.create.assert_not_called()


def test_hub_aborts_when_traefik_launch_declined() -> None:
    client = _make_docker_client(
        network_names=["katapult"],
        containers=[],
    )
    with patch.object(commands.docker, "from_env", return_value=client):
        runner = CliRunner()
        result = runner.invoke(main, ["hub"], input="n\n")

    assert result.exit_code == 0
    assert "Traefik container is required" in result.output
    client.containers.run.assert_not_called()


# --- _make_copy_ignore ---


def test_make_copy_ignore_excludes_top_level_docs(tmp_path: Path) -> None:
    ignore = commands._make_copy_ignore(tmp_path)
    skipped = ignore(str(tmp_path), ["docs", "srv", "README.md", ".git"])
    assert "docs" in skipped
    assert ".git" in skipped
    assert "srv" not in skipped
    assert "README.md" not in skipped


def test_make_copy_ignore_keeps_nested_docs(tmp_path: Path) -> None:
    """Nested directories named `docs` (e.g. srv/docs/) are documentation sources."""
    nested = tmp_path / "srv"
    nested.mkdir()
    ignore = commands._make_copy_ignore(tmp_path)
    skipped = ignore(str(nested), ["docs", "documentation.qmd", "__pycache__"])
    assert "docs" not in skipped
    assert "__pycache__" in skipped


def test_make_copy_ignore_at_any_depth_patterns_apply_everywhere(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    ignore = commands._make_copy_ignore(tmp_path)
    skipped = ignore(str(deep), [".venv", "__pycache__", ".quarto", "real_file.py"])
    assert {".venv", "__pycache__", ".quarto"}.issubset(skipped)
    assert "real_file.py" not in skipped


# --- _read_build_config_name ---


def test_read_build_config_name_parses_value(tmp_path: Path) -> None:
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "BuildConfig").write_text(
        "IMAGE_ROOT=katapult\nPROJECT_NAME=my_project\nUSERNAME=katapult\n"
    )
    assert commands._read_build_config_name(tmp_path) == "my_project"


def test_read_build_config_name_returns_none_when_missing_field(tmp_path: Path) -> None:
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "BuildConfig").write_text("IMAGE_ROOT=katapult\n")
    assert commands._read_build_config_name(tmp_path) is None


def test_read_build_config_name_returns_none_when_no_file(tmp_path: Path) -> None:
    assert commands._read_build_config_name(tmp_path) is None


# --- _image_exists ---


def test_image_exists_true_when_get_succeeds() -> None:
    client = MagicMock()
    client.images.get.return_value = MagicMock()
    assert commands._image_exists(client, "katapult/export-docs:latest") is True
    client.images.get.assert_called_once_with("katapult/export-docs:latest")


def test_image_exists_false_when_image_not_found() -> None:
    client = MagicMock()
    client.images.get.side_effect = docker.errors.ImageNotFound("nope")
    assert commands._image_exists(client, "katapult/export-docs:latest") is False


# --- CLI: export_docs ---


def _make_project(root: Path) -> Path:
    """Create a minimal katapult-shaped project with _quarto.yml + BuildConfig."""
    proj = root / "proj"
    proj.mkdir()
    (proj / "_quarto.yml").write_text("project:\n  type: website\n")
    (proj / "build").mkdir()
    (proj / "build" / "BuildConfig").write_text(
        "IMAGE_ROOT=katapult\nPROJECT_NAME=my_project\n"
    )
    (proj / "srv").mkdir()
    (proj / "srv" / "index.qmd").write_text("# hi\n")
    return proj


def _docker_run_creates_render_dir(cmd, check=True, **kwargs):  # noqa: ARG001
    """Stand-in for subprocess.run that creates export-docs-build inside the bind-mount."""
    work_host = None
    for i, a in enumerate(cmd):
        if a == "-v" and i + 1 < len(cmd) and ":/work" in cmd[i + 1]:
            work_host = cmd[i + 1].split(":/work", 1)[0]
            break
    if work_host is not None:
        (Path(work_host) / "export-docs-build").mkdir(parents=True, exist_ok=True)
    result = MagicMock()
    result.returncode = 0
    return result


def _patched_docker_env(images_get_side_effect=None):
    """Build a docker client mock and the patcher for docker.from_env."""
    client = MagicMock()
    if images_get_side_effect is not None:
        client.images.get.side_effect = images_get_side_effect
    else:
        client.images.get.return_value = MagicMock()
    return client


def test_export_docs_errors_without_quarto_yml(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["export-docs", str(tmp_path)])
    assert result.exit_code != 0
    assert "does not look like a Quarto project" in result.output


def test_export_docs_skips_build_when_image_exists(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    out = tmp_path / "out"
    client = _patched_docker_env()

    with (
        patch.object(commands.docker, "from_env", return_value=client),
        patch.object(commands.subprocess, "run", side_effect=_docker_run_creates_render_dir) as mock_run,
        patch.object(commands.shutil, "make_archive") as mock_archive,
    ):
        runner = CliRunner()
        result = runner.invoke(
            main, ["export-docs", str(proj), "--output-dir", str(out)]
        )

    assert result.exit_code == 0, result.output
    client.images.build.assert_not_called()
    assert mock_run.call_count == 1
    argv = mock_run.call_args.args[0]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert commands.IMAGE in argv
    assert "quarto" in argv and "render" in argv
    assert "--profile" in argv and "export-docs" in argv
    mock_archive.assert_called_once()


def test_export_docs_builds_when_image_missing(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    out = tmp_path / "out"
    client = _patched_docker_env(
        images_get_side_effect=docker.errors.ImageNotFound("missing")
    )

    with (
        patch.object(commands.docker, "from_env", return_value=client),
        patch.object(commands.subprocess, "run", side_effect=_docker_run_creates_render_dir),
        patch.object(commands.shutil, "make_archive"),
    ):
        runner = CliRunner()
        result = runner.invoke(
            main, ["export-docs", str(proj), "--output-dir", str(out)]
        )

    assert result.exit_code == 0, result.output
    client.images.build.assert_called_once()
    build_kwargs = client.images.build.call_args.kwargs
    assert build_kwargs["tag"] == commands.IMAGE
    assert build_kwargs["path"].endswith(os.path.join("resources", "export_docs"))
    assert "Building export-docs image" in result.output


def test_export_docs_rebuild_forces_build_even_when_present(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    out = tmp_path / "out"
    client = _patched_docker_env()

    with (
        patch.object(commands.docker, "from_env", return_value=client),
        patch.object(commands.subprocess, "run", side_effect=_docker_run_creates_render_dir),
        patch.object(commands.shutil, "make_archive"),
    ):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["export-docs", str(proj), "--output-dir", str(out), "--rebuild"],
        )

    assert result.exit_code == 0, result.output
    client.images.build.assert_called_once()


def test_export_docs_name_override_used_in_zip_basename(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    out = tmp_path / "out"
    client = _patched_docker_env()

    with (
        patch.object(commands.docker, "from_env", return_value=client),
        patch.object(commands.subprocess, "run", side_effect=_docker_run_creates_render_dir),
        patch.object(commands.shutil, "make_archive") as mock_archive,
    ):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["export-docs", str(proj), "--output-dir", str(out), "--name", "custom"],
        )

    assert result.exit_code == 0, result.output
    base_dir = mock_archive.call_args.kwargs["base_dir"]
    base_name = mock_archive.call_args.kwargs["base_name"]
    assert base_dir == "custom_docs"
    assert "custom_docs_" in base_name
    assert "my_project" not in base_name


def test_export_docs_uses_build_config_name_when_no_override(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    out = tmp_path / "out"
    client = _patched_docker_env()

    with (
        patch.object(commands.docker, "from_env", return_value=client),
        patch.object(commands.subprocess, "run", side_effect=_docker_run_creates_render_dir),
        patch.object(commands.shutil, "make_archive") as mock_archive,
    ):
        runner = CliRunner()
        result = runner.invoke(
            main, ["export-docs", str(proj), "--output-dir", str(out)]
        )

    assert result.exit_code == 0, result.output
    assert mock_archive.call_args.kwargs["base_dir"] == "my_project_docs"


def test_export_docs_passes_user_flag(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    out = tmp_path / "out"
    client = _patched_docker_env()

    with (
        patch.object(commands.docker, "from_env", return_value=client),
        patch.object(commands.subprocess, "run", side_effect=_docker_run_creates_render_dir) as mock_run,
        patch.object(commands.shutil, "make_archive"),
    ):
        runner = CliRunner()
        result = runner.invoke(
            main, ["export-docs", str(proj), "--output-dir", str(out)]
        )

    assert result.exit_code == 0, result.output
    argv = mock_run.call_args.args[0]
    expected_user = f"{os.getuid()}:{os.getgid()}"
    assert "--user" in argv
    user_idx = argv.index("--user")
    assert argv[user_idx + 1] == expected_user


def test_export_docs_blocks_known_slow_render_hosts(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    out = tmp_path / "out"
    client = _patched_docker_env()

    with (
        patch.object(commands.docker, "from_env", return_value=client),
        patch.object(commands.subprocess, "run", side_effect=_docker_run_creates_render_dir) as mock_run,
        patch.object(commands.shutil, "make_archive"),
    ):
        runner = CliRunner()
        result = runner.invoke(
            main, ["export-docs", str(proj), "--output-dir", str(out)]
        )

    assert result.exit_code == 0, result.output
    argv = mock_run.call_args.args[0]
    add_host_indices = [i for i, a in enumerate(argv) if a == "--add-host"]
    blocked = {argv[i + 1] for i in add_host_indices}
    for host in commands._BLOCKED_RENDER_HOSTS:
        assert f"{host}:127.0.0.1" in blocked, f"missing block for {host}"
