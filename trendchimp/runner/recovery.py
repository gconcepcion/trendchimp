from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

import numpy as np

from trendchimp.risk.sizing import protective_stop_price
from trendchimp.screener.technicals import compute_atr
from trendchimp.signals.models import OrderSide

if TYPE_CHECKING:
    from trendchimp.clients.market_data import MarketDataClient
    from trendchimp.config.settings import TrendChimpSettings
    from trendchimp.orders.manager import OrderManager
    from trendchimp.portfolio.state import PortfolioState

logger = logging.getLogger(__name__)
audit = logging.getLogger("trendchimp.audit")


def recover_protective_stops(
    order_manager: "OrderManager",
    portfolio: "PortfolioState",
    market_data: "MarketDataClient",
    settings: "TrendChimpSettings",
    dry_run: bool = False,
) -> None:
    """On (re)start, guarantee every open position has a live protective stop.

    Closes the crash/disconnect window where an entry filled but its stop was
    never placed. Idempotent: positions already protected are left untouched.
    """
    positions = portfolio.get_all_positions()
    if not positions:
        logger.info("Stop recovery: no open positions")
        return

    atr_mult = Decimal(str(settings.risk.atr_stop_mult))
    fallback_pct = Decimal(str(settings.risk.recovery_fallback_stop_pct))
    recovered = flattened = protected = failed = 0

    for pos in positions:
        symbol = pos.symbol.upper()
        qty = int(abs(pos.qty))
        if qty < 1:
            continue
        is_long = pos.qty > 0
        stop_side = OrderSide.SELL if is_long else OrderSide.BUY

        if order_manager.has_protective_stop(symbol, stop_side, qty):
            protected += 1
            continue

        n, last_close = _atr_and_last_close(market_data, symbol, settings)
        entry = pos.avg_entry_price

        if n is not None and n > 0:
            distance = atr_mult * Decimal(str(n))
        else:
            logger.warning("Stop recovery: no ATR for %s — using %.0f%% fallback stop",
                           symbol, float(fallback_pct) * 100)
            distance = entry * fallback_pct

        stop_price = protective_stop_price(entry, distance, is_long)

        # Test against a live price (falling back to the last close) so an overnight
        # gap past the stop is caught: a stop placed below the current price would
        # trigger instantly at a poor fill / be rejected, so exit at market instead.
        ref_price = market_data.get_latest_price(symbol) or last_close
        breached = (
            ref_price is not None
            and ((is_long and ref_price <= stop_price) or (not is_long and ref_price >= stop_price))
        )
        if breached:
            order_manager.flatten_now(symbol, stop_side, qty)
            flattened += 1
            continue

        if order_manager.place_protective_stop(symbol, stop_side, qty, stop_price) is not None or dry_run:
            recovered += 1
        else:
            # The stop submission failed (logged inside the order manager). The
            # position is live and UNPROTECTED — surface it loudly rather than
            # letting the bot trade on with a silent gap.
            failed += 1
            audit.error("STOP_RECOVERY_FAILED", extra={
                "symbol": symbol, "side": stop_side.value,
                "qty": str(qty), "stop_price": str(stop_price),
            })
            logger.critical(
                "Stop recovery FAILED for %s — position is unprotected (%d shares, stop %s). "
                "Place a stop manually or restart.", symbol, qty, stop_price,
            )

    log = logger.error if failed else logger.info
    log("Stop recovery complete: %d recovered, %d flattened, %d already protected, %d FAILED",
        recovered, flattened, protected, failed)


def _atr_and_last_close(
    market_data: "MarketDataClient", symbol: str, settings: "TrendChimpSettings",
) -> tuple[float | None, Decimal | None]:
    """Compute ATR(14) and the latest close from recent daily bars."""
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=80)  # ~40 trading bars, enough for ATR(14)
    try:
        bars = market_data.get_bars(symbol, "1Day", start, end)
    except Exception:
        logger.exception("Stop recovery: bar fetch failed for %s", symbol)
        return None, None
    if not bars:
        return None, None
    highs = np.array([b.high for b in bars], dtype=float)
    lows = np.array([b.low for b in bars], dtype=float)
    closes = np.array([b.close for b in bars], dtype=float)
    last_close = Decimal(str(closes[-1]))
    if len(closes) < 16:
        return None, last_close
    atr = compute_atr(highs, lows, closes, 14)
    return (atr if atr == atr else None), last_close
