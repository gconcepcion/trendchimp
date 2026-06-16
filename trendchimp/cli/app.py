from __future__ import annotations

import click

from trendchimp.cli.commands.positions import positions
from trendchimp.cli.commands.run import run
from trendchimp.cli.commands.screen import screen
from trendchimp.cli.commands.status import status


@click.group()
@click.version_option(package_name="trendchimp")
def cli() -> None:
    """trendchimp — Donchian/Turtle breakout trading bot for Alpaca."""


cli.add_command(run)
cli.add_command(status)
cli.add_command(positions)
cli.add_command(screen)


if __name__ == "__main__":
    cli()
