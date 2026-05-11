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
from click.exceptions import NoArgsIsHelpError
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
    required=False,
    default=None,
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
    project_dir: str | None,
    rebuild: bool,
    output_dir: str | None,
    keep_build: bool,
    name: str | None,
) -> None:
    """Render a project's docs to a self-contained HTML zip in ~/outputs."""
    project = _resolve_scan_project(project_dir, implicit_first_arg=None)
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


# --- Security scan (Trivy + Hadolint) -----------------------------------------

TRIVY_IMAGE = "aquasec/trivy:latest"
HADOLINT_IMAGE = "hadolint/hadolint:latest-debian"

SCANS_INDEX_BODY = """\
---
title: "Security scans"
description: "Automated vulnerability, misconfiguration, and secret scans of the repository and container image."
listing:
  type: table
  contents:
    - scans/*.qmd
  sort: "date desc"
  fields: [date, title, result]
  field-display-names:
    result: "Result"
---

## What is scanned

Two scopes are covered each time a `kat scan` report is generated:

- **Repository filesystem** - project files are checked for vulnerabilities in pinned dependencies, Dockerfile misconfigurations, and secrets in text content with Trivy.
- **Container image** - the locally-built image from `build/BuildConfig` is checked for OS package CVEs and vulnerabilities in bundled binaries with Trivy.

Dockerfiles are additionally linted with **Hadolint**, which catches Dockerfile and shell-script issues that vulnerability scanners do not.

The image scan reflects whatever image tag exists in the local Docker daemon at scan time. Rebuild the image after Dockerfile or dependency changes if you need an up-to-date image report.

## Tools

- [Trivy](https://trivy.dev) - vulnerability, misconfiguration, and secret scanner. Invoked as `trivy fs` for the repository and `trivy image` for the container.
- [Hadolint](https://github.com/hadolint/hadolint) - Dockerfile linter that wraps shellcheck for `RUN` blocks.
- [`kat scan`](https://github.com/ajp619/katapult) - katapult CLI command that orchestrates Trivy and Hadolint with project-aware defaults. The `--report` flag writes each run as `srv/scans/scan_<timestamp>.qmd`.

## Reproducing a report

```bash
kat scan --report
```

Then re-render the site (`quarto render` from the repository root, or a subset of targets) so `docs/` picks up the new `srv/scans/scan_<timestamp>.qmd` artifact.

## Reports
"""


class _ScanGroup(click.Group):
    """Allow `kat scan <PROJECT_DIR>` when the first arg is not a subcommand.

    Stash the path on ``ctx.obj`` during ``parse_args`` so it never occupies
    ``ctx._protected_args`` (which would otherwise force a bogus "subcommand"
    and break ``invoke_without_command``).
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if not args and self.no_args_is_help and not ctx.resilient_parsing:
            raise NoArgsIsHelpError(ctx)
        if args and args[0] not in self.commands and not str(args[0]).startswith("-"):
            implicit = str(args[0])
            rest = args[1:]
            if not rest or str(rest[0]).startswith("-"):
                ctx.ensure_object(dict)
                ctx.obj["_implicit_project_dir"] = implicit
                args = rest
        return super().parse_args(ctx, args)


def _find_project_root(start: Path | None = None) -> Path | None:
    """Walk up from `start` (default: CWD) looking for `.katapult/`."""
    cur = (start or Path.cwd()).resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / ".katapult").is_dir():
            return candidate
    return None


def _assert_katapult_project(path: Path) -> None:
    if not (path / ".katapult").is_dir():
        raise click.ClickException(
            f"{path} does not look like a katapult project (no .katapult directory)."
        )


def _resolve_scan_project(
    project_dir: str | None,
    *,
    implicit_first_arg: str | None = None,
) -> Path:
    if project_dir:
        path = Path(project_dir).resolve()
        _assert_katapult_project(path)
        return path
    if implicit_first_arg:
        path = Path(implicit_first_arg).resolve()
        _assert_katapult_project(path)
        return path
    found = _find_project_root()
    if found is None:
        raise click.ClickException(
            "No katapult project found in CWD or parents; pass PROJECT_DIR explicitly."
        )
    return found


def _resolve_scan_project_from_context(ctx: click.Context) -> Path:
    implicit = (ctx.obj or {}).get("_implicit_project_dir")
    return _resolve_scan_project(None, implicit_first_arg=implicit)


def _read_build_config_image_tag(project: Path) -> str | None:
    """Return IMAGE_ROOT/PROJECT_NAME:latest from build/BuildConfig if present."""
    cfg = project / "build" / "BuildConfig"
    if not cfg.is_file():
        return None
    text = cfg.read_text()
    m_root = re.search(r"^IMAGE_ROOT=(.+)$", text, re.MULTILINE)
    m_name = re.search(r"^PROJECT_NAME=(.+)$", text, re.MULTILINE)
    if not m_root or not m_name:
        return None
    root = m_root.group(1).strip()
    name = m_name.group(1).strip()
    return f"{root}/{name}:latest"


def _trivy_cache_dir() -> Path:
    d = Path.home() / ".cache" / "trivy"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _find_dockerfiles(project: Path) -> list[Path]:
    ignore_names = set(_COPY_IGNORE_AT_ANY_DEPTH)
    out: list[Path] = []
    for p in project.rglob("Dockerfile*"):
        if not p.is_file():
            continue
        if any(part in ignore_names for part in p.parts):
            continue
        out.append(p)
    return sorted(out)


# Project-rooted Trivy ignore-file conventions, in priority order.
# `.trivyignore.yaml` (richer schema) is preferred; `.trivyignore`
# is the legacy plaintext form Trivy still reads.
_TRIVY_IGNORE_BASENAMES = (".trivyignore.yaml", ".trivyignore")


def _project_trivyignore(project: Path) -> str | None:
    """Return ``/scan/<basename>`` if the project root has a Trivy ignore file.

    Trivy's default ignore-file lookup is cwd-relative; under
    ``kat scan`` the trivy container runs with cwd ``/`` and the project
    is bind-mounted at ``/scan``, so we have to pass ``--ignorefile``
    explicitly when one exists.
    """
    for name in _TRIVY_IGNORE_BASENAMES:
        if (project / name).is_file():
            return f"/scan/{name}"
    return None


def _trivy_fs_cmd(project: Path, severity: str, fmt: str, exit_on_vuln: bool) -> list[str]:
    # Trivy: --exit-code 1 => process exits 1 when vulnerabilities are found.
    exit_flag = "1" if exit_on_vuln else "0"
    cmd: list[str] = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{project}:/scan:ro",
        "-v",
        f"{_trivy_cache_dir()}:/root/.cache",
        TRIVY_IMAGE,
        "fs",
    ]
    ignore_path = _project_trivyignore(project)
    if ignore_path is not None:
        cmd += ["--ignorefile", ignore_path]
    cmd += [
        "--scanners",
        "vuln,misconfig,secret",
        "--severity",
        severity,
        "--format",
        fmt,
        "--exit-code",
        exit_flag,
        "/scan",
    ]
    return cmd


def _trivy_image_cmd(image: str, severity: str, fmt: str, exit_on_vuln: bool) -> list[str]:
    # No --ignorefile here: image scans don't bind-mount the project tree,
    # and ignore-file paths inside the image would have to use in-container
    # layout, not project-relative paths. Filesystem scan only for now.
    exit_flag = "1" if exit_on_vuln else "0"
    return [
        "docker",
        "run",
        "--rm",
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
        "-v",
        f"{_trivy_cache_dir()}:/root/.cache",
        TRIVY_IMAGE,
        "image",
        "--severity",
        severity,
        "--format",
        fmt,
        "--exit-code",
        exit_flag,
        image,
    ]


def _hadolint_cmd() -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "-i",
        HADOLINT_IMAGE,
        "hadolint",
        "-",
    ]


def _run_trivy_fs(
    project: Path, severity: str, fmt: str, exit_on_vuln: bool, *, capture: bool
) -> tuple[int, str]:
    cmd = _trivy_fs_cmd(project, severity, fmt, exit_on_vuln)
    if capture:
        r = subprocess.run(cmd, capture_output=True, text=True)
        combined = (r.stdout or "") + (r.stderr or "")
        click.echo(combined, nl=False)
        if combined and not combined.endswith("\n"):
            click.echo()
        return r.returncode, combined
    r = subprocess.run(cmd)
    return r.returncode, ""


def _run_trivy_image(
    image: str, severity: str, fmt: str, exit_on_vuln: bool, *, capture: bool
) -> tuple[int, str]:
    cmd = _trivy_image_cmd(image, severity, fmt, exit_on_vuln)
    if capture:
        r = subprocess.run(cmd, capture_output=True, text=True)
        combined = (r.stdout or "") + (r.stderr or "")
        click.echo(combined, nl=False)
        if combined and not combined.endswith("\n"):
            click.echo()
        return r.returncode, combined
    r = subprocess.run(cmd)
    return r.returncode, ""


def _run_hadolint(dockerfile: Path, *, capture: bool) -> tuple[int, str]:
    cmd = _hadolint_cmd()
    with dockerfile.open("rb") as stdin_f:
        if capture:
            r = subprocess.run(
                cmd,
                stdin=stdin_f,
                capture_output=True,
                text=True,
            )
            combined = (r.stdout or "") + (r.stderr or "")
            click.echo(combined, nl=False)
            if combined and not combined.endswith("\n"):
                click.echo()
            return r.returncode, combined
        r = subprocess.run(cmd, stdin=stdin_f)
    return r.returncode, ""


def _ensure_scans_index(srv_dir: Path) -> Path:
    path = srv_dir / "scans.qmd"
    if not path.exists():
        path.write_text(SCANS_INDEX_BODY)
    return path


def _ensure_quarto_wired(project: Path) -> bool:
    """Add srv/scans/* to render and Scans to navbar. Returns True if file changed."""
    qy = project / "_quarto.yml"
    if not qy.is_file():
        return False
    try:
        from ruamel.yaml import YAML
    except ModuleNotFoundError as e:
        raise click.ClickException(
            "Editing _quarto.yml requires the 'ruamel.yaml' package. "
            "Install it into the Python environment used by `kat` "
            "(see the shebang line of the `kat` script), or reinstall katapult "
            "from a checkout where `lib/pyproject.toml` lists `ruamel.yaml` "
            "so your installer applies dependencies (e.g. `uv sync` in `lib/`, "
            "then `uv tool install --force` / `pip install -e .` as you originally used)."
        ) from e
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    with qy.open() as f:
        data = yaml.load(f)
    if data is None:
        return False

    changed = False
    project_block = data.setdefault("project", {})
    render = project_block.setdefault("render", [])
    if not isinstance(render, list):
        render = list(render) if render else []
        project_block["render"] = render
    if "srv/scans/*" not in render:
        try:
            i = render.index("srv/*")
            render.insert(i + 1, "srv/scans/*")
        except ValueError:
            render.append("srv/scans/*")
        changed = True

    website = data.setdefault("website", {})
    navbar = website.setdefault("navbar", {})
    nav_left = navbar.setdefault("left", [])
    if not isinstance(nav_left, list):
        nav_left = list(nav_left) if nav_left else []
        navbar["left"] = nav_left
    if not any(
        isinstance(item, dict) and item.get("href") == "srv/scans.qmd"
        for item in nav_left
    ):
        insert_idx = len(nav_left)
        for i, item in enumerate(nav_left):
            if isinstance(item, dict) and item.get("href") == "srv/documentation.qmd":
                insert_idx = i + 1
                break
        nav_left.insert(insert_idx, {"href": "srv/scans.qmd", "text": "Scans"})
        changed = True

    if changed:
        with qy.open("w") as f:
            yaml.dump(data, f)
    return changed


def _escape_fence_body(text: str) -> str:
    return text.replace("```", "``\\`")


def _render_scan_qmd(
    sections: list[tuple[str, str, int]],
    project: Path,
    ts_iso: str,
) -> str:
    title_name = _read_build_config_name(project) or project.name
    summary_lines = []
    for sec_title, _body, rc in sections:
        status = "PASS" if rc == 0 else "FAIL"
        summary_lines.append(f"- {sec_title}: **{status}** (exit code {rc})")
    overall = "PASS" if all(rc == 0 for _, _, rc in sections) else "FAIL"
    summary = "\n".join(summary_lines)
    body_parts = [
        "---\n",
        f'title: "Security scan: {title_name}"\n',
        f"date: {ts_iso}\n",
        f"result: {overall}\n",
        "---\n\n",
        "## Summary\n\n",
        summary,
        "\n\n",
    ]
    for sec_title, body, rc in sections:
        body_parts.append(f"## {sec_title}\n\n")
        body_parts.append("```text\n")
        if body:
            body_parts.append(_escape_fence_body(body))
        elif rc == 0:
            body_parts.append("(no issues found)\n")
        else:
            body_parts.append("(no output)\n")
        body_parts.append("\n```\n\n")
    return "".join(body_parts)


def _write_scan_report(
    project: Path,
    sections: list[tuple[str, str, int]],
) -> Path:
    srv = project / "srv"
    srv.mkdir(parents=True, exist_ok=True)
    scans_dir = srv / "scans"
    scans_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ts_iso = datetime.now().isoformat(timespec="seconds")
    path = scans_dir / f"scan_{ts}.qmd"
    path.write_text(_render_scan_qmd(sections, project, ts_iso))
    _ensure_scans_index(srv)
    return path


def _scan_options(f):
    f = click.option(
        "--severity",
        default="HIGH,CRITICAL",
        help="Comma-separated severities passed to Trivy.",
    )(f)
    f = click.option(
        "--format",
        "fmt",
        type=click.Choice(["table", "json", "sarif"]),
        default="table",
        help="Trivy output format.",
    )(f)
    f = click.option(
        "--no-fail",
        is_flag=True,
        help="Always exit 0 from scanners even when findings match --severity.",
    )(f)
    f = click.option(
        "--skip-hadolint",
        is_flag=True,
        help="Skip Hadolint (Dockerfile lint).",
    )(f)
    f = click.option(
        "--report",
        is_flag=True,
        help="Write Quarto report under srv/scans/ and wire _quarto.yml.",
    )(f)
    f = click.option(
        "--no-wire",
        is_flag=True,
        help="With --report, skip _quarto.yml navbar/render edits.",
    )(f)
    return f


def _run_fs_scan(
    project: Path,
    *,
    severity: str,
    fmt: str,
    no_fail: bool,
    skip_hadolint: bool,
    capture: bool,
) -> tuple[int, list[tuple[str, str, int]]]:
    fail = not no_fail
    sections: list[tuple[str, str, int]] = []
    rc, out = _run_trivy_fs(project, severity, fmt, fail, capture=capture)
    sections.append(("Trivy filesystem scan", out, rc))
    agg = 0 if rc == 0 else 1
    if not skip_hadolint:
        for df in _find_dockerfiles(project):
            rel = df.relative_to(project)
            rc_h, out_h = _run_hadolint(df, capture=capture)
            sections.append((f"Hadolint ({rel})", out_h, rc_h))
            if rc_h != 0 and not no_fail:
                agg = 1
    return agg, sections


def _run_image_scan(
    project: Path,
    tag_override: str | None,
    *,
    severity: str,
    fmt: str,
    no_fail: bool,
    capture: bool,
    strict_missing: bool,
) -> tuple[int, list[tuple[str, str, int]]]:
    fail = not no_fail
    client = docker.from_env()
    tag = tag_override or _read_build_config_image_tag(project)
    if not tag:
        msg = "Could not determine image tag (missing build/BuildConfig or IMAGE_ROOT/PROJECT_NAME)."
        if strict_missing:
            raise click.ClickException(msg)
        click.echo(f"Warning: {msg} Skipping image scan.", err=True)
        return 0, [
            (
                "Trivy image scan",
                f"(skipped — {msg})",
                0,
            )
        ]
    try:
        client.images.get(tag)
    except docker.errors.ImageNotFound:
        msg = f"Image {tag!r} not found locally; build the project image first."
        if strict_missing:
            raise click.ClickException(msg)
        click.echo(f"Warning: {msg} Skipping image scan.", err=True)
        return 0, [("Trivy image scan", f"(skipped — {msg})", 0)]
    rc, out = _run_trivy_image(tag, severity, fmt, fail, capture=capture)
    agg = 0 if rc == 0 else 1
    return agg, [("Trivy image scan", out, rc)]


@click.group(
    name="scan",
    cls=_ScanGroup,
    invoke_without_command=True,
    help="Scan a katapult project for vulnerabilities (Trivy) and Dockerfile issues (Hadolint).",
)
@_scan_options
@click.pass_context
def scan(
    ctx: click.Context,
    severity: str,
    fmt: str,
    no_fail: bool,
    skip_hadolint: bool,
    report: bool,
    no_wire: bool,
) -> None:
    """Scan project for vulnerabilities and Dockerfile issues."""
    if ctx.invoked_subcommand is not None:
        return
    project = _resolve_scan_project_from_context(ctx)
    capture = report
    fs_rc, fs_sections = _run_fs_scan(
        project,
        severity=severity,
        fmt=fmt,
        no_fail=no_fail,
        skip_hadolint=skip_hadolint,
        capture=capture,
    )
    img_rc, img_sections = _run_image_scan(
        project,
        None,
        severity=severity,
        fmt=fmt,
        no_fail=no_fail,
        capture=capture,
        strict_missing=False,
    )
    all_sections = fs_sections + img_sections
    exit_rc = 1 if (fs_rc != 0 or img_rc != 0) else 0
    if report:
        _write_scan_report(project, all_sections)
        if not no_wire:
            if _ensure_quarto_wired(project):
                click.echo("Updated _quarto.yml (Scans navbar + srv/scans/* render).")
    if exit_rc != 0:
        raise click.ClickException("Scan completed with findings (non-zero exit).")


@scan.command("fs")
@_scan_options
@click.argument(
    "project_dir",
    type=click.Path(file_okay=False, exists=True),
    required=False,
)
@click.pass_context
def scan_fs(
    ctx: click.Context,
    project_dir: str | None,
    severity: str,
    fmt: str,
    no_fail: bool,
    skip_hadolint: bool,
    report: bool,
    no_wire: bool,
) -> None:
    """Filesystem scan only (Trivy fs + Hadolint)."""
    project = _resolve_scan_project(project_dir, implicit_first_arg=None)
    capture = report
    fs_rc, sections = _run_fs_scan(
        project,
        severity=severity,
        fmt=fmt,
        no_fail=no_fail,
        skip_hadolint=skip_hadolint,
        capture=capture,
    )
    if report:
        _write_scan_report(project, sections)
        if not no_wire:
            if _ensure_quarto_wired(project):
                click.echo("Updated _quarto.yml (Scans navbar + srv/scans/* render).")
    if fs_rc != 0:
        raise click.ClickException("Scan completed with findings (non-zero exit).")


@scan.command("image")
@_scan_options
@click.argument(
    "project_dir",
    type=click.Path(file_okay=False, exists=True),
    required=False,
)
@click.argument("tag", required=False)
@click.pass_context
def scan_image(
    _ctx: click.Context,
    project_dir: str | None,
    tag: str | None,
    severity: str,
    fmt: str,
    no_fail: bool,
    skip_hadolint: bool,
    report: bool,
    no_wire: bool,
) -> None:
    """Scan a built container image with Trivy."""
    _ = skip_hadolint  # accepted for option parity with other scan entry points
    project = _resolve_scan_project(project_dir, implicit_first_arg=None)
    capture = report
    img_rc, sections = _run_image_scan(
        project,
        tag,
        severity=severity,
        fmt=fmt,
        no_fail=no_fail,
        capture=capture,
        strict_missing=True,
    )
    if report:
        _write_scan_report(project, sections)
        if not no_wire:
            if _ensure_quarto_wired(project):
                click.echo("Updated _quarto.yml (Scans navbar + srv/scans/* render).")
    if img_rc != 0:
        raise click.ClickException("Scan completed with findings (non-zero exit).")