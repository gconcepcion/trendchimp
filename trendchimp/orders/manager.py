from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from trendchimp.orders.models import ManagedOrder, OrderDecision
from trendchimp.signals.models import OrderSide, TradeIntent

if TYPE_CHECKING:
    from trendchimp.clients.trading import TradingClientWrapper
    from trendchimp.portfolio.state import PortfolioState

logger = logging.getLogger(__name__)
audit = logging.getLogger("trendchimp.audit")


def _enum_str(value: Any) -> str:
    """Normalize an alpaca-py enum (or plain string) to its lowercase value.

    Alpaca statuses/sides/types are str-enums whose ``str()`` is the *member*
    repr, not the value: ``str(OrderStatus.FILLED) == 'OrderStatus.FILLED'`` while
    ``OrderStatus.FILLED.value == 'filled'``. Comparing the former against
    lowercase literals (``== "filled"``, ``is_open()``) silently never matches,
    so every enum captured from the broker must pass through here first.
    """
    return str(getattr(value, "value", value)).lower()


class OrderManager:
    """Submits orders, attaches a 2N protective stop to every entry on fill, and
    tracks order lifecycle. Supports both long and short entries."""

    def __init__(
        self,
        trading_client: "TradingClientWrapper",
        portfolio_state: "PortfolioState",
        dry_run: bool = False,
    ) -> None:
        self._client = trading_client
        self._portfolio = portfolio_state
        self._dry_run = dry_run
        self._orders: dict[str, ManagedOrder] = {}

    def reconcile(self) -> None:
        """Fetch open orders from Alpaca and rebuild internal state."""
        open_orders = self._client.get_open_orders()
        for order in open_orders:
            managed = self._from_alpaca_order(order)
            self._orders[managed.order_id] = managed
        logger.info("Reconciled %d open orders from Alpaca", len(open_orders))

    def submit(self, decision: OrderDecision, attach_stop_bracket: bool = False) -> ManagedOrder | None:
        if self._dry_run:
            logger.info(
                "[DRY RUN] would %s %d %s (%s) stop=%s%s",
                decision.side.value, decision.qty, decision.symbol,
                decision.intent.value, decision.stop_price,
                " [OTO]" if attach_stop_bracket else "",
            )
            return None

        from alpaca.trading.enums import OrderSide as AlpacaSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        alpaca_side = AlpacaSide.BUY if decision.side == OrderSide.BUY else AlpacaSide.SELL
        # Batch (no-stream) mode attaches the protective stop server-side as an OTO leg
        # so the entry is protected on fill without a trade-update listener.
        order_kwargs: dict = {}
        if (attach_stop_bracket and decision.intent is not None
                and decision.intent.is_entry and decision.stop_price is not None):
            from alpaca.trading.enums import OrderClass
            from alpaca.trading.requests import StopLossRequest

            order_kwargs = {
                "order_class": OrderClass.OTO,
                "stop_loss": StopLossRequest(
                    stop_price=float(decision.stop_price.quantize(Decimal("0.01"))),
                ),
            }
        request = MarketOrderRequest(
            symbol=decision.symbol,
            qty=decision.qty,
            side=alpaca_side,
            time_in_force=TimeInForce.DAY,
            **order_kwargs,
        )

        try:
            raw = self._client.submit_order(request)
        except Exception:
            logger.exception("Failed to submit order for %s", decision.symbol)
            return None

        managed = ManagedOrder(
            order_id=str(raw.id),
            symbol=decision.symbol,
            side=decision.side,
            qty=Decimal(str(decision.qty)),
            status=_enum_str(raw.status),
            submitted_at=datetime.now(tz=timezone.utc),
            strategy_name=(
                decision.originating_signal.strategy_name
                if decision.originating_signal else ""
            ),
            intent=decision.intent,
            signal=decision.originating_signal,
            stop_price=decision.stop_price,
            raw=raw,
        )
        self._orders[managed.order_id] = managed
        audit.info(
            "ORDER_SUBMITTED",
            extra={
                "order_id": managed.order_id, "symbol": managed.symbol,
                "side": managed.side.value, "intent": decision.intent.value,
                "qty": str(decision.qty), "strategy": managed.strategy_name,
            },
        )
        logger.info("Submitted %s %d %s (%s) [%s]",
                    managed.side.value, decision.qty, managed.symbol,
                    decision.intent.value, managed.order_id)
        return managed

    async def handle_trade_update(self, update: Any) -> None:
        order = getattr(update, "order", None)
        if order is None:
            logger.warning("Trade update missing 'order' attribute")
            return

        order_id = str(order.id)
        managed = self._orders.get(order_id)
        if managed is None:
            logger.info("Trade update for untracked order %s — creating from stream", order_id)
            managed = self._from_alpaca_order(order)
            self._orders[order_id] = managed

        managed.status = _enum_str(order.status)
        managed.raw = order

        if managed.status == "filled":
            managed.filled_at = datetime.now(tz=timezone.utc)
            managed.filled_avg_price = Decimal(str(order.filled_avg_price or 0))
            managed.filled_qty = Decimal(str(order.filled_qty or managed.qty))
            await self._portfolio.on_fill(update)
            audit.info(
                "ORDER_FILLED",
                extra={
                    "order_id": order_id, "symbol": managed.symbol,
                    "side": managed.side.value,
                    "intent": managed.intent.value if managed.intent else "",
                    "qty": str(managed.filled_qty), "price": str(managed.filled_avg_price),
                },
            )
            logger.info("Filled %s %s x%s @ %s", managed.side.value, managed.symbol,
                        managed.filled_qty, managed.filled_avg_price)

            if managed.is_stop_order or self._dry_run:
                return
            if managed.intent is not None and managed.intent.is_entry:
                self._attach_protective_stop(managed)
            elif managed.intent is not None and managed.intent.is_exit:
                self._cancel_open_stops(managed.symbol)
        else:
            logger.info("Order %s status → %s", order_id, managed.status)

    def get_active_orders(self) -> list[ManagedOrder]:
        return [o for o in self._orders.values() if o.is_open()]

    def cancel_all(self) -> None:
        try:
            self._client.cancel_all_orders()
            logger.info("Cancelled all open orders")
        except Exception:
            logger.exception("Error cancelling all orders")

    # ------------------------------------------------------------ stop recovery
    def has_protective_stop(self, symbol: str, required_side: OrderSide, qty: int) -> bool:
        """True if an open tracked stop on `symbol` covers `qty` on the protective side."""
        symbol = symbol.upper()
        for o in self._orders.values():
            if (o.is_stop_order and o.symbol.upper() == symbol and o.is_open()
                    and o.side == required_side and o.qty >= Decimal(str(qty))):
                return True
        return False

    def has_trailing_stop(self, symbol: str, required_side: OrderSide, qty: int) -> bool:
        """True if an open tracked *trailing* stop on `symbol` covers `qty`."""
        symbol = symbol.upper()
        for o in self._orders.values():
            if (o.is_trailing and o.symbol.upper() == symbol and o.is_open()
                    and o.side == required_side and o.qty >= Decimal(str(qty))):
                return True
        return False

    def place_protective_stop(
        self, symbol: str, side: OrderSide, qty: int, stop_price: Decimal,
    ) -> ManagedOrder | None:
        """Place a GTC protective stop for an existing position (recovery path)."""
        stop_price = stop_price.quantize(Decimal("0.01"))
        if self._dry_run:
            logger.info("[DRY RUN] would recover %s stop %d %s @ %s",
                        side.value, qty, symbol, stop_price)
            return None

        from alpaca.trading.enums import OrderSide as AlpacaSide, TimeInForce
        from alpaca.trading.requests import StopOrderRequest

        alpaca_side = AlpacaSide.SELL if side == OrderSide.SELL else AlpacaSide.BUY
        request = StopOrderRequest(
            symbol=symbol, qty=qty, side=alpaca_side,
            stop_price=float(stop_price), time_in_force=TimeInForce.GTC,
        )
        try:
            raw = self._client.submit_order(request)
        except Exception:
            logger.exception("Failed to recover protective stop for %s", symbol)
            return None

        managed = ManagedOrder(
            order_id=str(raw.id), symbol=symbol.upper(), side=side,
            qty=Decimal(str(qty)), status=_enum_str(raw.status),
            submitted_at=datetime.now(tz=timezone.utc), strategy_name="",
            is_stop_order=True, stop_price=stop_price, raw=raw,
        )
        self._orders[managed.order_id] = managed
        audit.info("STOP_RECOVERED", extra={
            "order_id": managed.order_id, "symbol": symbol.upper(),
            "side": side.value, "qty": str(qty), "stop_price": str(stop_price),
        })
        logger.info("Recovered protective stop (%s) for %s @ %s [%s]",
                    side.value, symbol, stop_price, managed.order_id)
        return managed

    def place_trailing_stop(
        self, symbol: str, side: OrderSide, qty: int,
        *, trail_percent: float | None = None, trail_price: Decimal | None = None,
    ) -> ManagedOrder | None:
        """Place a GTC trailing stop for an existing position (orphan hand-off).

        Pass exactly one of `trail_price` (absolute dollar distance, e.g. 2N) or
        `trail_percent` (whole-number percent, 5.0 == 5%). Used when a held symbol
        drops out of the traded universe: the broker trails the stop without the bot
        watching the symbol."""
        if (trail_price is None) == (trail_percent is None):
            raise ValueError("pass exactly one of trail_price or trail_percent")
        if trail_price is not None:
            trail_price = trail_price.quantize(Decimal("0.01"))
        trail_desc = f"${trail_price}" if trail_price is not None else f"{trail_percent:.2f}%"

        if self._dry_run:
            logger.info("[DRY RUN] would place %s trailing stop (%s) %d %s",
                        side.value, trail_desc, qty, symbol)
            return None

        from alpaca.trading.enums import OrderSide as AlpacaSide, TimeInForce
        from alpaca.trading.requests import TrailingStopOrderRequest

        alpaca_side = AlpacaSide.SELL if side == OrderSide.SELL else AlpacaSide.BUY
        trail_kw = ({"trail_price": float(trail_price)} if trail_price is not None
                    else {"trail_percent": float(trail_percent)})
        request = TrailingStopOrderRequest(
            symbol=symbol, qty=qty, side=alpaca_side,
            time_in_force=TimeInForce.GTC, **trail_kw,
        )
        try:
            raw = self._client.submit_order(request)
        except Exception:
            logger.exception("Failed to place trailing stop for %s", symbol)
            return None

        managed = ManagedOrder(
            order_id=str(raw.id), symbol=symbol.upper(), side=side,
            qty=Decimal(str(qty)), status=_enum_str(raw.status),
            submitted_at=datetime.now(tz=timezone.utc), strategy_name="",
            is_stop_order=True, is_trailing=True, raw=raw,
        )
        self._orders[managed.order_id] = managed
        audit.info("TRAILING_STOP_PLACED", extra={
            "order_id": managed.order_id, "symbol": symbol.upper(),
            "side": side.value, "qty": str(qty),
            **{k: str(v) for k, v in trail_kw.items()},
        })
        logger.info("Trailing stop (%s, %s) placed for %s [%s]",
                    side.value, trail_desc, symbol, managed.order_id)
        return managed

    def cancel_open_stops(self, symbol: str) -> None:
        """Public wrapper: cancel any still-open protective/trailing stop for a symbol."""
        self._cancel_open_stops(symbol)

    def flatten_now(self, symbol: str, side: OrderSide, qty: int) -> ManagedOrder | None:
        """Market-exit a position whose protective stop is already breached."""
        if self._dry_run:
            logger.info("[DRY RUN] would flatten %s: market %s %d", symbol, side.value, qty)
            return None

        from alpaca.trading.enums import OrderSide as AlpacaSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        alpaca_side = AlpacaSide.SELL if side == OrderSide.SELL else AlpacaSide.BUY
        request = MarketOrderRequest(
            symbol=symbol, qty=qty, side=alpaca_side, time_in_force=TimeInForce.DAY,
        )
        try:
            raw = self._client.submit_order(request)
        except Exception:
            logger.exception("Failed to flatten %s on recovery", symbol)
            return None

        managed = ManagedOrder(
            order_id=str(raw.id), symbol=symbol.upper(), side=side,
            qty=Decimal(str(qty)), status=_enum_str(raw.status),
            submitted_at=datetime.now(tz=timezone.utc), strategy_name="", raw=raw,
        )
        self._orders[managed.order_id] = managed
        audit.info("POSITION_FLATTENED_ON_RECOVERY", extra={
            "order_id": managed.order_id, "symbol": symbol.upper(),
            "side": side.value, "qty": str(qty),
        })
        logger.warning("Flattened %s on recovery (stop already breached): %s %d",
                       symbol, side.value, qty)
        return managed

    def _attach_protective_stop(self, entry: ManagedOrder) -> None:
        from alpaca.trading.enums import OrderSide as AlpacaSide, TimeInForce
        from alpaca.trading.requests import StopOrderRequest

        if entry.stop_price is None:
            logger.warning("No stop price for %s entry — skipping protective stop", entry.symbol)
            return

        qty_whole = math.floor(float(entry.filled_qty or 0))
        if qty_whole < 1:
            logger.warning("Filled qty %s rounds to 0 — skipping stop for %s",
                           entry.filled_qty, entry.symbol)
            return

        # Opposite side of the entry: long entry -> SELL stop below; short -> BUY stop above.
        if entry.intent == TradeIntent.ENTER_LONG:
            stop_side = AlpacaSide.SELL
        else:
            stop_side = AlpacaSide.BUY
        stop_price = entry.stop_price.quantize(Decimal("0.01"))

        request = StopOrderRequest(
            symbol=entry.symbol,
            qty=qty_whole,
            side=stop_side,
            stop_price=float(stop_price),
            time_in_force=TimeInForce.GTC,
        )
        try:
            raw = self._client.submit_order(request)
        except Exception:
            logger.exception("Failed to submit protective stop for %s", entry.symbol)
            return

        stop_managed = ManagedOrder(
            order_id=str(raw.id),
            symbol=entry.symbol,
            side=OrderSide.SELL if stop_side == AlpacaSide.SELL else OrderSide.BUY,
            qty=Decimal(str(qty_whole)),
            status=_enum_str(raw.status),
            submitted_at=datetime.now(tz=timezone.utc),
            strategy_name=entry.strategy_name,
            is_stop_order=True,
            stop_price=stop_price,
            raw=raw,
        )
        self._orders[stop_managed.order_id] = stop_managed
        entry.entry_price = entry.filled_avg_price
        entry.stop_order_id = stop_managed.order_id

        audit.info(
            "STOP_SUBMITTED",
            extra={
                "order_id": stop_managed.order_id, "entry_order_id": entry.order_id,
                "symbol": entry.symbol, "side": stop_managed.side.value,
                "stop_price": str(stop_price),
            },
        )
        logger.info("Protective stop (%s) submitted for %s @ %s [%s]",
                    stop_managed.side.value, entry.symbol, stop_price, stop_managed.order_id)

    def _cancel_open_stops(self, symbol: str) -> None:
        """Cancel any still-open protective stop for a symbol after it is flattened."""
        symbol = symbol.upper()
        for managed in self._orders.values():
            if (managed.is_stop_order and managed.symbol.upper() == symbol
                    and managed.is_open()):
                try:
                    self._client.cancel_order(managed.order_id)
                    managed.status = "canceled"
                    audit.info("STOP_CANCELLED",
                               extra={"order_id": managed.order_id, "symbol": symbol})
                    logger.info("Cancelled orphaned stop %s for %s", managed.order_id, symbol)
                except Exception:
                    logger.exception("Failed to cancel stop %s for %s", managed.order_id, symbol)

    def _from_alpaca_order(self, order: Any) -> ManagedOrder:
        raw_side = _enum_str(getattr(order, "side", "buy"))
        side = OrderSide.BUY if raw_side == "buy" else OrderSide.SELL
        order_type = _enum_str(getattr(order, "type", ""))
        is_stop = "stop" in order_type  # "stop" / "stop_limit" / "trailing_stop"
        is_trailing = "trailing" in order_type
        stop_raw = getattr(order, "stop_price", None)
        return ManagedOrder(
            order_id=str(order.id),
            symbol=str(order.symbol).upper(),
            side=side,
            qty=Decimal(str(order.qty or 0)),
            status=_enum_str(order.status),
            submitted_at=getattr(order, "submitted_at", datetime.now(tz=timezone.utc)),
            strategy_name="",
            is_stop_order=is_stop,
            is_trailing=is_trailing,
            stop_price=Decimal(str(stop_raw)) if stop_raw is not None else None,
            raw=order,
        )
