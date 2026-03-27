"""Tests for katapult.commands helpers and CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

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
