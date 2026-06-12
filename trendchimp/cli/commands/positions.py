from __future__ import annotations

import click


@click.command()
@click.option("--env-file", default=".env", show_default=True,
              help="Path to the .env file to load.")
def positions(env_file: str) -> None:
    """List open positions (long and short)."""
    from dotenv import load_dotenv

    load_dotenv(env_file)

    from trendchimp.cli.formatting import render_positions
    from trendchimp.clients.factory import ClientFactory
    from trendchimp.clients.trading import TradingClientWrapper
    from trendchimp.config.loader import ConfigurationError, load_settings

    try:
        settings = load_settings()
    except ConfigurationError as exc:
        raise click.ClickException(str(exc))

    client = TradingClientWrapper(ClientFactory(settings).make_trading_client())
    render_positions(client.get_positions())
