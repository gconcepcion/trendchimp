from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from trendchimp.orders.models import OrderDecision
from trendchimp.signals.models import OrderSide, Signal, TradeIntent
from trendchimp.risk.sizing import TurtleUnitSizer

if TYPE_CHECKING:
    from trendchimp.config.settings import RiskSettings
    from trendchimp.portfolio.state import PortfolioState
    from trendchimp.risk.killswitch import KillSwitch

logger = logging.getLogger(__name__)


class RiskManager:
    """Gate-based evaluation: turns a Signal into a sized, stop-protected OrderDecision."""

    def __init__(
        self,
        settings: "RiskSettings",
        sizer: TurtleUnitSizer,
        killswitch: "KillSwitch | None" = None,
    ) -> None:
        self._settings = settings
        self._sizer = sizer
        self._killswitch = killswitch

    def evaluate(self, signal: Signal, portfolio: "PortfolioState") -> OrderDecision | None:
        account = portfolio.get_account()
        if account is None:
            logger.warning("No account data available, skipping signal for %s", signal.symbol)
            return None

        # Exit intents are always honoured (even while the kill-switch is tripped)
        # so the bot can flatten on the way down.
        if signal.intent.is_exit:
            return self._build_exit(signal, portfolio)

        # --- Entry gates ---
        if self._killswitch is not None and self._killswitch.check(portfolio):
            logger.warning("Kill-switch tripped (%s) — blocking entry for %s",
                           self._killswitch.reason, signal.symbol)
            return None

        if signal.intent == TradeIntent.ENTER_SHORT and not self._settings.allow_short:
            logger.info("Shorting disabled — blocking ENTER_SHORT for %s", signal.symbol)
            return None

        if self._would_duplicate(signal, portfolio):
            logger.debug("Already positioned in %s for %s — blocking entry",
                         signal.intent.value, signal.symbol)
            return None

        if len(portfolio.get_all_positions()) >= self._settings.max_open_positions:
            logger.info("Max open positions (%d) reached — blocking entry for %s",
                        self._settings.max_open_positions, signal.symbol)
            return None

        entry_price = self._as_decimal(signal.metadata.get("entry_price"))
        n = self._as_decimal(signal.metadata.get("atr"))
        if entry_price is None or n is None:
            logger.warning("Entry signal for %s missing entry_price/atr metadata", signal.symbol)
            return None

        qty = self._sizer.size(entry_price, n, account.equity, account.buying_power)
        if qty < 1:
            logger.debug("Computed qty<1 for %s — skipping", signal.symbol)
            return None

        stop_price = self._as_decimal(signal.metadata.get("stop_price"))
        if stop_price is None:
            stop_price = self._fallback_stop(signal.intent, entry_price, n)

        return OrderDecision(
            symbol=signal.symbol,
            side=signal.side,
            qty=qty,
            intent=signal.intent,
            stop_price=stop_price,
            originating_signal=signal,
        )

    def _build_exit(self, signal: Signal, portfolio: "PortfolioState") -> OrderDecision | None:
        position = portfolio.get_position(signal.symbol)
        if position is None or position.qty == 0:
            logger.info("No position to exit for %s — skipping %s", signal.symbol, signal.intent.value)
            return None
        qty = int(abs(position.qty))
        if qty < 1:
            return None
        return OrderDecision(
            symbol=signal.symbol,
            side=signal.side,
            qty=qty,
            intent=signal.intent,
            stop_price=None,
            originating_signal=signal,
        )

    def _would_duplicate(self, signal: Signal, portfolio: "PortfolioState") -> bool:
        existing = portfolio.get_position(signal.symbol)
        if existing is None or existing.qty == 0:
            return False
        # Any existing position blocks a fresh entry (v1: no pyramiding, and the
        # strategy is expected to exit before reversing).
        return True

    def _fallback_stop(self, intent: TradeIntent, entry_price: Decimal, n: Decimal) -> Decimal:
        distance = Decimal(str(self._settings.atr_stop_mult)) * n
        if intent == TradeIntent.ENTER_LONG:
            return (entry_price - distance).quantize(Decimal("0.01"))
        return (entry_price + distance).quantize(Decimal("0.01"))

    @staticmethod
    def _as_decimal(value) -> Decimal | None:
        if value is None:
            return None
        return value if isinstance(value, Decimal) else Decimal(str(value))
