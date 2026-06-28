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
    from trendchimp.safety import SafetyController

logger = logging.getLogger(__name__)
audit = logging.getLogger("trendchimp.audit")


def recover_protective_stops(
    order_manager: "OrderManager",
    portfolio: "PortfolioState",
    market_data: "MarketDataClient",
    settings: "TrendChimpSettings",
    dry_run: bool = False,
    safety: "SafetyController | None" = None,
    market_open: bool = True,
) -> None:
    """On (re)start, guarantee every open position has a live protective stop.

    Closes the crash/disconnect window where an entry filled but its stop was
    never placed. Idempotent: positions already protected are left untouched.

    ``market_open`` controls how an already-breached position is handled: when the
    market is open it is flattened at market; when closed a DAY market order would
    just be rejected, so a resting stop is placed to resolve at the open instead.
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

        # Only protect the *uncovered* remainder: summed existing stops may already
        # cover some or all of the position (partial fill / reconciled bracket leg).
        # Topping up the remainder avoids both an under-covered position and a
        # duplicate over-size stop (which would go naked-reverse once one fills).
        covered = order_manager.open_stop_qty(symbol, stop_side)
        uncovered = qty - covered
        if uncovered <= 0:
            protected += 1
            continue
        qty = uncovered

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
        if breached and not market_open:
            # Can't market-flatten a closed market; rest a stop so it resolves at the
            # open instead of firing a DAY market order the broker will reject.
            logger.warning(
                "Stop recovery: %s breached but market is closed — placing a resting "
                "stop to trigger at the open instead of a rejected market flatten.", symbol)
            if order_manager.place_protective_stop(symbol, stop_side, qty, stop_price) is not None or dry_run:
                recovered += 1
            else:
                failed += 1
                audit.error("STOP_RECOVERY_FAILED", extra={
                    "symbol": symbol, "side": stop_side.value,
                    "qty": str(qty), "stop_price": str(stop_price),
                })
                logger.critical(
                    "Stop recovery FAILED for %s (market closed) — position is unprotected "
                    "(%d shares, stop %s). Place a stop manually.", symbol, qty, stop_price)
                if safety is not None:
                    safety.position_unprotected(symbol, stop_side, qty, reason="stop_recovery_failed_closed")
            continue

        if breached:
            if order_manager.flatten_now(symbol, stop_side, qty) is not None or dry_run:
                flattened += 1
            else:
                # The emergency exit failed to submit: the position is breached,
                # unprotected, and still open. Surface it loudly rather than
                # silently counting it as flattened.
                failed += 1
                audit.error("FLATTEN_ON_RECOVERY_FAILED", extra={
                    "symbol": symbol, "side": stop_side.value, "qty": str(qty),
                })
                logger.critical(
                    "Emergency flatten FAILED for %s — breached position is unprotected "
                    "and still open (%d shares). Flatten manually now.", symbol, qty,
                )
                if safety is not None:
                    safety.position_unprotected(symbol, stop_side, qty, reason="flatten_failed")
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
            if safety is not None:
                safety.position_unprotected(symbol, stop_side, qty, reason="stop_recovery_failed")

    log = logger.error if failed else logger.info
    log("Stop recovery complete: %d recovered, %d flattened, %d already protected, %d FAILED",
        recovered, flattened, protected, failed)


def convert_orphans_to_trailing_stops(
    order_manager: "OrderManager",
    portfolio: "PortfolioState",
    universe_symbols: set[str],
    market_data: "MarketDataClient",
    settings: "TrendChimpSettings",
    dry_run: bool = False,
) -> None:
    """Hand off positions whose symbol has dropped out of the traded universe.

    Such a position is no longer watched by the strategy, so its Donchian exit can
    never fire. Replace its static protective stop with a broker GTC trailing stop
    that trails ``orphan_trailing_atr_mult × N`` (ATR), falling back to a flat percent
    only when ATR can't be computed. Idempotent: a position already on a trailing stop
    is left alone.
    """
    positions = portfolio.get_all_positions()
    universe = {s.upper() for s in universe_symbols}
    atr_mult = Decimal(str(settings.risk.orphan_trailing_atr_mult))
    fallback_pct = float(settings.risk.orphan_trailing_stop_pct) * 100.0

    converted = failed = 0
    for pos in positions:
        symbol = pos.symbol.upper()
        if symbol in universe:
            continue  # still strategy-managed
        qty = int(abs(pos.qty))
        if qty < 1:
            continue
        stop_side = OrderSide.SELL if pos.qty > 0 else OrderSide.BUY

        if order_manager.has_trailing_stop(symbol, stop_side, qty):
            continue  # already handed off on a prior restart

        # Trail atr_mult × N where N is available; otherwise fall back to a flat percent.
        n, _ = _atr_and_last_close(market_data, symbol, settings)
        trail_kwargs: dict
        if n is not None and n > 0:
            trail_price = (atr_mult * Decimal(str(n))).quantize(Decimal("0.01"))
            trail_kwargs = {"trail_price": trail_price}
            logger.info("Converting orphan %s (left the universe) to a %sN ($%s) trailing stop",
                        symbol, atr_mult, trail_price)
        else:
            trail_kwargs = {"trail_percent": fallback_pct}
            logger.warning("Converting orphan %s — no ATR, using %.1f%% fallback trailing stop",
                           symbol, fallback_pct)

        # Place the trailing stop FIRST, then cancel the static one. A brief
        # double-stop is harmless; a brief no-stop is not. If the trailing stop
        # can't be placed, keep the static stop so the position stays protected.
        if order_manager.place_trailing_stop(symbol, stop_side, qty, **trail_kwargs) is not None or dry_run:
            order_manager.cancel_open_stops(symbol)
            converted += 1
        else:
            failed += 1
            audit.error("ORPHAN_TRAIL_FAILED", extra={
                "symbol": symbol, "side": stop_side.value, "qty": str(qty),
            })
            logger.error(
                "Orphan trailing-stop FAILED for %s — kept the existing static stop so "
                "the position stays protected; it just won't trail. Will retry on the "
                "next pass.", symbol,
            )

    if converted or failed:
        log = logger.error if failed else logger.info
        log("Orphan hand-off complete: %d converted to trailing stops, %d FAILED",
            converted, failed)


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
