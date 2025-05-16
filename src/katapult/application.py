# library imports
import argparse
import rich
from rich.table import Table
from cookiecutter.main import cookiecutter
from pathlib import Path

# local imports
from .console import console


def init():
    current_file_path = Path(__file__)
    cookiecutter(str(current_file_path.parent / "project_template"))


def debug():
    table = Table(title="Star Wars Movies")

    table.add_column("Released", justify="right", style="cyan", no_wrap=True)
    table.add_column("Title", style="magenta")
    table.add_column("Box Office", justify="right", style="green")

    table.add_row("Dec 20, 2019", "Star Wars: The Rise of Skywalker", "$952,110,690")
    table.add_row("May 25, 2018", "Solo: A Star Wars Story", "$393,151,347")
    table.add_row("Dec 15, 2017", "Star Wars Ep. V111: The Last Jedi", "$1,332,539,889")
    table.add_row("Dec 16, 2016", "Rogue One: A Star Wars Story", "$1,332,439,889")

    console.print(table)


def main():
    global_parser = argparse.ArgumentParser(prog="katalyst")

    subparsers = global_parser.add_subparsers(
        title="subcommands", help="commands", dest="command"
    )

    init_parser = subparsers.add_parser("init", help="initialize a project")
    init_parser.set_defaults(func=init)

    #debug_parser = subparsers.add_parser("debug", help="test functionality")
    #debug_parser.set_defaults(func=debug)

    args = global_parser.parse_args()

    args.func()
