from __future__ import annotations

import click


@click.command()
@click.option("--env-file", default=".env", show_default=True,
              help="Path to the .env file to load.")
def run(env_file: str) -> None:
    """Start the paper-trading bot."""
    from dotenv import load_dotenv

    load_dotenv(env_file)

    from trendchimp.config.loader import ConfigurationError, load_settings
    from trendchimp.runner.bot import TradingBot

    try:
        settings = load_settings()
    except ConfigurationError as exc:
        raise click.ClickException(str(exc))

    if not settings.alpaca.paper:
        click.confirm(
            click.style("LIVE TRADING is enabled. Continue?", fg="red", bold=True),
            abort=True,
        )

    TradingBot(settings).start()
