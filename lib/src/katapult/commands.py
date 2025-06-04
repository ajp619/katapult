from pathlib import Path

import docker
import click
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
        if click.confirm("Docker network 'katapult' does not exist. Create it?", default=True):
            client.networks.create("katapult")
            click.echo("Created 'katapult' network.")
        else:
            click.echo("Aborting: 'katapult' network is required.")
            return

    # Check if Traefik container is running
    traefik_containers = [
        c for c in client.containers.list(all=True)
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
                volumes={"/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"}},
                command=["--api.insecure=true", "--providers.docker"],
                name="katapult-traefik",
                restart_policy={"Name": "always"}
            )
            click.echo("Launched Traefik container.")
        else:
            click.echo("Aborting: Traefik container is required.")