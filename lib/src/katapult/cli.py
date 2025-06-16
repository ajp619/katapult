import click

from katapult import commands


@click.group()
def main():
    """Katapult CLI - A command line interface for managing Katapult applications."""
    pass


# main.add_command(commands.rich)
main.add_command(commands.init)
main.add_command(commands.hub)
main.add_command(commands.config)
