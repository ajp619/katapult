import textwrap
from pathlib import Path

import click
import docker
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


@click.command()
def init():
    """Initialize a new Katapult application."""
    current_file_path = Path(__file__)
    cookiecutter(str(current_file_path.parent / "project_template"))


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
                "traefik:v3.4",
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
        # Section added by katapult to dynamically add katx to path based on project
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
        # f"""
        # {marker}
        # # Intended to be added to .bashrc
        # #
        # RAW_PATH="$PATH"
        # LAST_WD=`pwd`

        # augment_path() {{
        #     target=".katapult"
        #     if [ "$PWD" = "$LAST_WD" ]; then return 0; fi;
        #     PATH_ADDITION=""
        #     scandir="$PWD"
        #     until [ "$scandir" = "" ]; do
        #     resolved_target="$scandir"/"$target"
        #     if [ -d "$resolved_target" ]; then
        #         PATH_ADDITION="$PATH_ADDITION:$resolved_target"
        #     fi
        #     scandir="${{scandir%/*}}"
        #     done
        #     PATH="$PATH_ADDITION:$RAW_PATH"
        #     LAST_WD=`pwd`
        # }}

        # if [ -z ${{PROMPT_COMMAND+x}} ]; then
        #     # prompt not found
        #     PROMPT_COMMAND="augment_path"
        # else
        #     # prompt found
        #     PROMPT_COMMAND="$PROMPT_COMMAND; augment_path"
        # fi
        # #
        # # End of section generated by katapult
        # """
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
