from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from trendchimp.data.models import Bar
from trendchimp.indicators.rolling import DonchianChannel, WilderATR
from trendchimp.signals.models import OrderSide, Signal, TradeIntent
from trendchimp.strategies.base import BaseStrategy
from trendchimp.strategies.registry import StrategyRegistry

logger = logging.getLogger(__name__)


@StrategyRegistry.register
class DonchianBreakoutStrategy(BaseStrategy):
    """Turtle-style Donchian channel breakout (long + short).

    Entry: breakout above the highest high of the prior ``entry_channel`` bars
    (go long) or below the lowest low (go short). Exit: opposite breakout of the
    shorter ``exit_channel`` channel. Protective stop: ``atr_stop_mult`` × ATR
    (the Turtle "2N" stop) from the entry price.

    System 1 (20/10) is the default; System 2 (55/20) is just different params.
    """

    name = "donchian_breakout"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        super().__init__(params)
        self.entry_channel = int(self.parameters.get("entry_channel", 20))
        self.exit_channel = int(self.parameters.get("exit_channel", 10))
        self.atr_period = int(self.parameters.get("atr_period", 20))
        self.atr_stop_mult = float(self.parameters.get("atr_stop_mult", 2.0))
        self.allow_short = bool(self.parameters.get("allow_short", True))
        self.breakout_on = str(self.parameters.get("breakout_on", "high_low"))
        if self.breakout_on not in ("high_low", "close"):
            raise ValueError("breakout_on must be 'high_low' or 'close'")
        if self.entry_channel < 1 or self.exit_channel < 1:
            raise ValueError("entry_channel and exit_channel must be >= 1")

        # Per-symbol incremental state so one instance can serve many symbols.
        self._entry: dict[str, DonchianChannel] = {}
        self._exit: dict[str, DonchianChannel] = {}
        self._atr: dict[str, WilderATR] = {}
        self._pos_state: dict[str, int] = {}  # -1 short, 0 flat, +1 long (strategy's view)

    def get_required_history(self) -> int:
        # +1 because the channel/ATR must reflect the bar BEFORE the one being tested.
        return max(self.entry_channel, self.exit_channel, self.atr_period) + 1

    def seed_position(self, symbol: str, qty: float) -> None:
        sym = symbol.upper()
        if qty > 0:
            self._pos_state[sym] = 1
        elif qty < 0:
            self._pos_state[sym] = -1
        else:
            self._pos_state[sym] = 0

    async def on_bar(self, bar: Bar) -> list[Signal]:
        sym = bar.symbol.upper()
        entry = self._entry.setdefault(sym, DonchianChannel(self.entry_channel))
        exit_ch = self._exit.setdefault(sym, DonchianChannel(self.exit_channel))
        atr = self._atr.setdefault(sym, WilderATR(self.atr_period))
        pos = self._pos_state.setdefault(sym, 0)

        # 1) Read prior-bar values BEFORE ingesting this bar (no look-ahead).
        entry_up = entry.upper()
        entry_lo = entry.lower()
        exit_up = exit_ch.upper()
        exit_lo = exit_ch.lower()
        n = atr.current()

        signals: list[Signal] = []
        # 2) Detect breakouts only once everything is primed.
        if None not in (entry_up, entry_lo, exit_up, exit_lo, n):
            trigger_high = bar.high if self.breakout_on == "high_low" else bar.close
            trigger_low = bar.low if self.breakout_on == "high_low" else bar.close

            if pos == 1:
                # Long exit takes precedence: breakout below the exit channel.
                if trigger_low < exit_lo:
                    signals.append(self._exit_signal(bar, TradeIntent.EXIT_LONG, OrderSide.SELL))
                    self._pos_state[sym] = 0
            elif pos == -1:
                # Short exit (cover): breakout above the exit channel.
                if trigger_high > exit_up:
                    signals.append(self._exit_signal(bar, TradeIntent.EXIT_SHORT, OrderSide.BUY))
                    self._pos_state[sym] = 0
            else:
                # Flat: look for an entry breakout. Long takes precedence over short
                # in the (degenerate) case both trigger on the same bar.
                if trigger_high > entry_up:
                    signals.append(
                        self._entry_signal(bar, TradeIntent.ENTER_LONG, OrderSide.BUY,
                                           n, entry_up, entry_lo, exit_up, exit_lo)
                    )
                    self._pos_state[sym] = 1
                elif self.allow_short and trigger_low < entry_lo:
                    signals.append(
                        self._entry_signal(bar, TradeIntent.ENTER_SHORT, OrderSide.SELL,
                                           n, entry_up, entry_lo, exit_up, exit_lo)
                    )
                    self._pos_state[sym] = -1

        # 3) Ingest the current bar AFTER detection (preserves prior-bar semantics).
        atr.update(bar.high, bar.low, bar.close)
        entry.push(bar.high, bar.low)
        exit_ch.push(bar.high, bar.low)

        return signals

    def _entry_signal(
        self,
        bar: Bar,
        intent: TradeIntent,
        side: OrderSide,
        n: float,
        entry_up: float,
        entry_lo: float,
        exit_up: float,
        exit_lo: float,
    ) -> Signal:
        entry_price = bar.close
        stop_distance = self.atr_stop_mult * n
        if intent == TradeIntent.ENTER_LONG:
            stop_price = entry_price - stop_distance
        else:
            stop_price = entry_price + stop_distance
        logger.info(
            "%s breakout on %s: close=%.4f N=%.4f stop=%.4f (entry_ch %d, exit_ch %d)",
            intent.value, bar.symbol, entry_price, n, stop_price,
            self.entry_channel, self.exit_channel,
        )
        return Signal(
            symbol=bar.symbol,
            side=side,
            strategy_name=self.name,
            timestamp=bar.timestamp,
            intent=intent,
            strength=1.0,
            metadata={
                "entry_price": Decimal(str(entry_price)),
                "atr": Decimal(str(n)),
                "stop_price": Decimal(str(round(stop_price, 4))),
                "channel_high": entry_up,
                "channel_low": entry_lo,
                "exit_channel_high": exit_up,
                "exit_channel_low": exit_lo,
                "entry_channel": self.entry_channel,
                "exit_channel": self.exit_channel,
            },
        )

    def _exit_signal(self, bar: Bar, intent: TradeIntent, side: OrderSide) -> Signal:
        logger.info("%s breakout on %s: close=%.4f", intent.value, bar.symbol, bar.close)
        return Signal(
            symbol=bar.symbol,
            side=side,
            strategy_name=self.name,
            timestamp=bar.timestamp,
            intent=intent,
            strength=1.0,
            metadata={"exit_price": Decimal(str(bar.close))},
        )
