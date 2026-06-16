from __future__ import annotations

import click


@click.command()
@click.option("--env-file", default=".env", show_default=True,
              help="Path to the .env file to load.")
@click.option("--once", "once", is_flag=True, default=False,
              help="Batch mode: act on the latest completed daily bar and exit (no live "
                   "stream). Run a few minutes after the open; cron 1-2x/day.")
def run(env_file: str, once: bool) -> None:
    """Start the paper-trading bot (always-on), or run a single batch pass with --once."""
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

    bot = TradingBot(settings)
    if once:
        bot.run_once()
    else:
        bot.start()
