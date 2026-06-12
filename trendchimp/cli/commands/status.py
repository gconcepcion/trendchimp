from __future__ import annotations

import click


@click.command()
@click.option("--env-file", default=".env", show_default=True,
              help="Path to the .env file to load.")
def status(env_file: str) -> None:
    """Show a read-only account summary."""
    from dotenv import load_dotenv

    load_dotenv(env_file)

    from trendchimp.cli.formatting import render_account
    from trendchimp.clients.factory import ClientFactory
    from trendchimp.clients.trading import TradingClientWrapper
    from trendchimp.config.loader import ConfigurationError, load_settings

    try:
        settings = load_settings()
    except ConfigurationError as exc:
        raise click.ClickException(str(exc))

    client = TradingClientWrapper(ClientFactory(settings).make_trading_client())
    render_account(client.get_account())
