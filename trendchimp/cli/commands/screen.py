from __future__ import annotations

import click


@click.command()
@click.option("--top-n", default=None, type=int, help="Override screener.top_n_technical.")
@click.option("--picks", default=None, type=int, help="Override screener.final_picks.")
@click.option("--min-price", default=None, type=float, help="Drop candidates priced below this (override screener.min_price).")
@click.option("--max-price", default=None, type=float, help="Drop candidates priced above this (override screener.max_price). Use for small accounts, e.g. --max-price 50.")
@click.option("--output", default=None, help="Universe file to write (default: trading.universe_file or ./universe.json).")
@click.option("--no-ai", is_flag=True, default=False, help="Skip Claude; write the top technical picks.")
@click.option("--env-file", default=".env", show_default=True, help="Path to the .env file to load.")
def screen(top_n, picks, min_price, max_price, output, no_ai, env_file) -> None:
    """Screen the S&P 500 for breakout candidates and write the trading universe."""
    from dotenv import load_dotenv

    load_dotenv(env_file)

    from trendchimp.cli.formatting import render_picks
    from trendchimp.clients.factory import ClientFactory
    from trendchimp.clients.market_data import MarketDataClient
    from trendchimp.config.loader import ConfigurationError, load_settings
    from trendchimp.logging.setup import configure_logging
    from trendchimp.screener.analyst import TrendAnalyst
    from trendchimp.screener.technicals import TrendScorer
    from trendchimp.screener.universe import SymbolUniverse
    from trendchimp.screener.writer import write_universe_file

    try:
        settings = load_settings()
    except ConfigurationError as exc:
        raise click.ClickException(str(exc))

    configure_logging(settings.logging)
    screener = settings.screener
    top_n_val = top_n if top_n is not None else screener.top_n_technical
    picks_val = picks if picks is not None else screener.final_picks
    min_price_val = min_price if min_price is not None else screener.min_price
    max_price_val = max_price if max_price is not None else screener.max_price
    out_path = output or settings.trading.universe_file or "./universe.json"

    if not no_ai and not screener.anthropic_api_key:
        raise click.ClickException(
            "No Anthropic API key. Set TRENDCHIMP_SCREENER__ANTHROPIC_API_KEY, "
            "or run with --no-ai to use technical scoring only."
        )

    market_data = MarketDataClient(
        stock_client=ClientFactory(settings).make_stock_historical_client(),
        feed=settings.alpaca.data_feed,
    )

    click.echo("Fetching S&P 500 universe...")
    universe = SymbolUniverse().get_sp500(screener.cache_dir)

    band = ""
    if min_price_val or max_price_val:
        lo = f"${min_price_val:g}" if min_price_val else "$0"
        hi = f"${max_price_val:g}" if max_price_val else "∞"
        band = f" (price {lo}–{hi})"
    click.echo(f"Scoring {len(universe)} symbols for breakout fitness{band}...")
    candidates = TrendScorer().score_all(
        universe, market_data, lookback_days=screener.lookback_days, top_n=top_n_val,
        min_price=min_price_val, max_price=max_price_val,
    )
    if not candidates:
        raise click.ClickException(
            "No candidates passed the technical filters"
            + (f" within the {band.strip()}." if band else ".")
        )

    if no_ai:
        final = TrendAnalyst.technical_fallback(candidates, picks_val)
    else:
        click.echo(f"Asking {screener.model} to pick the final {picks_val}...")
        # final_picks override flows through the settings object.
        screener_for_call = screener.model_copy(update={"final_picks": picks_val})
        final = TrendAnalyst().select(candidates, screener_for_call)

    write_universe_file(out_path, final)
    render_picks(final, out_path)
