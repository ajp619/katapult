import fnmatch
import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
from datetime import datetime
from importlib import resources
from pathlib import Path

import click
import docker
import docker.errors
from cookiecutter.main import cookiecutter
from rich.console import Console
from rich.table import Table

console = Console()


@click.command()
def rich():
    """Display a rich table.

    Demonstrates the use of rich library to create a styled table."""

    table = Table(title="Star Wars Movies")

    table.add_column("Released", justify="right", style="cyan", no_wrap=True)
    table.add_column("Title", style="magenta")
    table.add_column("Box Office", justify="right", style="green")

    table.add_row("Dec 20, 2019", "Star Wars: The Rise of Skywalker", "$952,110,690")
    table.add_row("May 25, 2018", "Solo: A Star Wars Story", "$393,151,347")
    table.add_row("Dec 15, 2017", "Star Wars Ep. V111: The Last Jedi", "$1,332,539,889")
    table.add_row("Dec 16, 2016", "Rogue One: A Star Wars Story", "$1,332,439,889")

    with console.capture() as capture:
        console.print(table)

    click.echo(capture.get())


def _merge_overrides(src: Path, dst: Path) -> None:
    """Copy override files into the template project directory."""
    for item in src.rglob("*"):
        if not item.is_file():
            continue
        target = dst / item.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def _apply_copy_without_render(ignore_file: Path, cookiecutter_json: Path) -> None:
    """Merge glob patterns from ignore_file into cookiecutter.json's _copy_without_render."""
    if not ignore_file.is_file():
        return
    patterns = [
        line.strip()
        for line in ignore_file.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not patterns:
        return
    cc = json.loads(cookiecutter_json.read_text())
    existing = cc.get("_copy_without_render", [])
    if not isinstance(existing, list):
        existing = []
    seen: set[str] = set()
    merged: list[str] = []
    for p in existing + patterns:
        if p not in seen:
            seen.add(p)
            merged.append(p)
    cc["_copy_without_render"] = merged
    cookiecutter_json.write_text(json.dumps(cc, indent=4) + "\n")


def _override_template_has_content(override_dir: Path) -> bool:
    """True if override_dir exists and has at least one file under it."""
    if not override_dir.is_dir():
        return False
    return any(p.is_file() for p in override_dir.rglob("*"))


@click.command()
@click.option(
    "--no-overrides",
    is_flag=True,
    help="Ignore ~/.katapult/template/ overrides.",
)
def init(no_overrides: bool) -> None:
    """Initialize a new Katapult application."""
    current_file_path = Path(__file__)
    template_dir = current_file_path.parent / "project_template"
    katapult_dir = Path.home() / ".katapult"
    override_dir = katapult_dir / "template"

    if (
        not no_overrides
        and _override_template_has_content(override_dir)
    ):
        with tempfile.TemporaryDirectory() as tmp:
            merged = Path(tmp) / "project_template"
            shutil.copytree(template_dir, merged)
            _merge_overrides(override_dir, merged / "{{cookiecutter.project_slug}}")
            _apply_copy_without_render(
                katapult_dir / "copy_without_render",
                merged / "cookiecutter.json",
            )
            click.echo(f"Applying overrides from {override_dir}")
            cookiecutter(str(merged))
    else:
        cookiecutter(str(template_dir))


@click.command()
def hub():
    """Manage the Katapult hub and Traefik container."""
    client = docker.from_env()

    # Check if 'katapult' network exists
    networks = [net.name for net in client.networks.list()]
    if "katapult" not in networks:
        if click.confirm(
            "Docker network 'katapult' does not exist. Create it?", default=True
        ):
            client.networks.create("katapult")
            click.echo("Created 'katapult' network.")
        else:
            click.echo("Aborting: 'katapult' network is required.")
            return

    # Check if Traefik container is running
    traefik_containers = [
        c
        for c in client.containers.list(all=True)
        if any("traefik" in tag for tag in c.image.tags)
    ]
    running = any(c.status == "running" for c in traefik_containers)

    if running:
        click.echo("Traefik container is already running.")
    else:
        if click.confirm("Traefik container is not running. Launch it?", default=True):
            client.containers.run(
                "traefik:v3.6",
                detach=True,
                network="katapult",
                ports={"80/tcp": 80, "8080/tcp": 8080},
                volumes={
                    "/var/run/docker.sock": {
                        "bind": "/var/run/docker.sock",
                        "mode": "rw",
                    }
                },
                command=["--api.insecure=true", "--providers.docker"],
                name="katapult-traefik",
                restart_policy={"Name": "always"},
            )
            click.echo("Launched Traefik container.")
        else:
            click.echo("Aborting: Traefik container is required.")


@click.command()
def config():
    """Add Katapult dynamic PATH augmentation to the user's .bashrc."""
    bashrc_path = Path.home() / ".bashrc"
    marker = (
        "# Section added by katapult to dynamically add katx to path based on project"
    )
    config_block = textwrap.dedent(
        f"""
        # ------------------------------------------------------------------------------
        {marker}
        # Intended to be added to .bashrc

        # Store the original PATH so we can rebuild it cleanly later
        RAW_PATH="$PATH"

        # Keep track of the last working directory so we only update when it changes
        LAST_WD=`pwd`

        # Function to augment the PATH based on presence of .katapult directories
        augment_path() {{
            target=".katapult"

            # If we have not changed directories, skip updating the PATH
            if [ "$PWD" = "$LAST_WD" ]; then return 0; fi;

            PATH_ADDITION=""
            scandir="$PWD"

            # Walk up the directory tree toward root
            until [ "$scandir" = "" ]; do
                resolved_target="$scandir"/"$target"

                # If a .katapult directory is found, add it to PATH_ADDITION
                if [ -d "$resolved_target" ]; then
                    PATH_ADDITION="$PATH_ADDITION:$resolved_target"
                fi

                # Move up one level in the directory tree
                scandir="${{scandir%/*}}"
            done

            # Rebuild PATH with all found .katapult directories at the front
            # followed by the original PATH
            PATH="$PATH_ADDITION:$RAW_PATH"

            # Update the last known working directory
            LAST_WD=`pwd`
        }}

        # Ensure augment_path runs every time the prompt is displayed
        # This hooks into PROMPT_COMMAND, which is executed before the shell prompt
        if [ -z ${{PROMPT_COMMAND+x}} ]; then
            # If PROMPT_COMMAND is not set, initialize it
            PROMPT_COMMAND="augment_path"
        else
            # If PROMPT_COMMAND exists, append augment_path to it
            PROMPT_COMMAND="$PROMPT_COMMAND; augment_path"
        fi

        # End of section generated by katapult
        # ------------------------------------------------------------------------------
        """
    )

    # Read .bashrc and check if already present
    if not bashrc_path.exists():
        click.echo(f"{bashrc_path} does not exist. Creating it.")
        bashrc_path.touch()
    content = bashrc_path.read_text()
    if marker in content:
        click.echo("Katapult PATH augmentation already present in .bashrc.")
    else:
        click.echo("Adding Katapult PATH augmentation to .bashrc.")
        with bashrc_path.open("a") as f:
            f.write("\n" + config_block + "\n")
            click.echo("Added Katapult PATH augmentation to .bashrc.")


IMAGE = "katapult/export-docs:latest"

# Hosts Pandoc tries to fetch when inlining external resources for
# `embed-resources: true`. On networks where these CDNs are unreachable
# (corporate egress filtering, restricted VPC), each fetch eats the full
# default response timeout per page. Routing them to 127.0.0.1 makes the
# connection fail immediately so the render never blocks on them. The
# rendered HTML simply falls back to the system font stack for these
# resources, which is what would have happened anyway after the timeout.
_BLOCKED_RENDER_HOSTS = (
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdnjs.cloudflare.com",
)

# Patterns matched at any directory depth (heavy / generated trees Quarto
# does not need).
_COPY_IGNORE_AT_ANY_DEPTH = (
    ".git",
    ".venv",
    "__pycache__",
    ".quarto",
    "node_modules",
    "export-docs-build",
)

# Names ignored only at the top level of the project being copied.
# `docs` is the conventional rendered-output folder in katapult projects
# (per `_quarto.yml` `output-dir: docs`); excluding it avoids copying a
# stale prior render. We must NOT exclude `docs` at deeper paths because
# katapult projects use `srv/docs/`, `nbk/docs/`, etc. for documentation
# source files; excluding those silently breaks internal links in the
# rendered output.
_COPY_IGNORE_TOP_LEVEL_ONLY = ("docs",)


def _make_copy_ignore(src_root: Path):
    src_real = os.path.realpath(src_root)

    def _ignore(path: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for pattern in _COPY_IGNORE_AT_ANY_DEPTH:
            ignored.update(fnmatch.filter(names, pattern))
        if os.path.realpath(path) == src_real:
            for pattern in _COPY_IGNORE_TOP_LEVEL_ONLY:
                ignored.update(fnmatch.filter(names, pattern))
        return ignored

    return _ignore


def _read_build_config_name(project: Path) -> str | None:
    """Return PROJECT_NAME from <project>/build/BuildConfig if present."""
    cfg = project / "build" / "BuildConfig"
    if not cfg.is_file():
        return None
    m = re.search(r"^PROJECT_NAME=(.+)$", cfg.read_text(), re.MULTILINE)
    return m.group(1).strip() if m else None


def _image_exists(client, tag: str) -> bool:
    try:
        client.images.get(tag)
        return True
    except docker.errors.ImageNotFound:
        return False


def _ensure_image(client, rebuild: bool) -> None:
    if not rebuild and _image_exists(client, IMAGE):
        return
    click.echo("Building export-docs image (first run)...")
    build_ctx = resources.files("katapult.resources.export_docs")
    with resources.as_file(build_ctx) as ctx_path:
        client.images.build(path=str(ctx_path), tag=IMAGE, rm=True)


@click.command(name="export-docs")
@click.argument(
    "project_dir",
    type=click.Path(file_okay=False, exists=True),
    default=".",
)
@click.option(
    "--rebuild",
    is_flag=True,
    help="Force rebuild of the export-docs Docker image.",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Output directory for the zip (default: ~/outputs).",
)
@click.option(
    "--keep-build",
    is_flag=True,
    help="Also write the unzipped render folder next to the zip and retain the temp working copy.",
)
@click.option(
    "--name",
    default=None,
    help="Override the inferred project name used in the zip filename.",
)
def export_docs(
    project_dir: str,
    rebuild: bool,
    output_dir: str | None,
    keep_build: bool,
    name: str | None,
) -> None:
    """Render a project's docs to a self-contained HTML zip in ~/outputs."""
    project = Path(project_dir).resolve()
    if not (project / "_quarto.yml").is_file():
        raise click.ClickException(
            f"{project} does not look like a Quarto project (no _quarto.yml)."
        )

    project_name = name or _read_build_config_name(project) or project.name
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_stem = f"{project_name}_docs_{ts}"

    out = Path(output_dir).expanduser() if output_dir else Path.home() / "outputs"
    out.mkdir(parents=True, exist_ok=True)

    client = docker.from_env()
    _ensure_image(client, rebuild=rebuild)

    work_parent = Path(tempfile.mkdtemp(prefix="kat-export-docs-"))
    try:
        work = work_parent / "work"
        shutil.copytree(project, work, ignore=_make_copy_ignore(project))

        profile_src = (
            resources.files("katapult.resources.export_docs")
            / "_quarto-export-docs.yml"
        )
        with resources.as_file(profile_src) as p:
            shutil.copy2(p, work / "_quarto-export-docs.yml")

        docker_cmd = [
            "docker",
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-e",
            "HOME=/work",
        ]
        for host in _BLOCKED_RENDER_HOSTS:
            docker_cmd += ["--add-host", f"{host}:127.0.0.1"]
        docker_cmd += [
            "-v",
            f"{work}:/work",
            "-w",
            "/work",
            IMAGE,
            "quarto",
            "render",
            "--profile",
            "export-docs",
        ]
        subprocess.run(docker_cmd, check=True)

        rendered = work / "export-docs-build"
        staged = work / f"{project_name}_docs"
        rendered.rename(staged)

        zip_path = out / f"{zip_stem}.zip"
        shutil.make_archive(
            base_name=str(out / zip_stem),
            format="zip",
            root_dir=work,
            base_dir=f"{project_name}_docs",
        )

        if keep_build:
            dest = out / f"{zip_stem}_unzipped"
            shutil.copytree(staged, dest)
            click.echo(f"Kept unzipped tree at {dest}")

        click.echo(f"Wrote {zip_path}")
    finally:
        if not keep_build:
            shutil.rmtree(work_parent, ignore_errors=True)
        else:
            click.echo(f"Working copy retained at {work_parent}")