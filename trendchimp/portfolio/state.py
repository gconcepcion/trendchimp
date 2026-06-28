from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from trendchimp.data.models import InternalAccount, InternalPosition

if TYPE_CHECKING:
    from trendchimp.clients.trading import TradingClientWrapper

logger = logging.getLogger(__name__)


class PortfolioState:
    """In-memory account/position snapshot supporting long AND short positions.

    Positions carry a SIGNED qty (positive long, negative short). Tracks realized
    P&L from closed exposure, a live equity estimate (start equity + realized +
    unrealized from last-seen marks), and the peak of that estimate for the
    drawdown kill-switch.
    """

    def __init__(self) -> None:
        self._positions: dict[str, InternalPosition] = {}
        self._account: InternalAccount | None = None
        self._reconciled_at: datetime | None = None
        self._start_equity: Decimal = Decimal("0")
        self._realized_pnl_today: Decimal = Decimal("0")
        self._peak_equity: Decimal = Decimal("0")
        self._marks: dict[str, Decimal] = {}

    # ------------------------------------------------------------------ startup
    def reconcile(self, client: "TradingClientWrapper") -> None:
        self._account = client.get_account()
        positions = client.get_positions()
        self._positions = {p.symbol.upper(): self._normalize(p) for p in positions}
        for p in self._positions.values():
            self._marks[p.symbol.upper()] = p.avg_entry_price
        self._reconciled_at = datetime.now(tz=timezone.utc)
        self._start_equity = self._account.equity
        self._peak_equity = self._account.equity
        logger.info(
            "Portfolio reconciled: %d positions, equity=%s",
            len(self._positions), self._account.equity,
        )

    @staticmethod
    def _normalize(p: InternalPosition) -> InternalPosition:
        """Ensure qty sign matches side (Alpaca reports short qty as negative already)."""
        side = "short" if p.qty < 0 else "long"
        return InternalPosition(
            symbol=p.symbol.upper(), qty=p.qty, avg_entry_price=p.avg_entry_price,
            market_value=p.market_value, unrealized_pl=p.unrealized_pl,
            unrealized_plpc=p.unrealized_plpc, side=side,
        )

    # -------------------------------------------------------------------- fills
    async def on_fill(self, update: Any) -> None:
        order = getattr(update, "order", None)
        if order is None:
            return

        symbol = str(order.symbol).upper()
        filled_qty = Decimal(str(order.filled_qty or 0))
        filled_price = Decimal(str(order.filled_avg_price or 0))
        side = str(getattr(order, "side", "buy")).lower()
        if filled_qty <= 0:
            return

        delta = filled_qty if side == "buy" else -filled_qty
        self._apply_position_delta(symbol, delta, filled_price)
        self._marks[symbol] = filled_price
        self._recompute_peak()

    def _apply_position_delta(self, symbol: str, delta: Decimal, price: Decimal) -> None:
        existing = self._positions.get(symbol)
        old_qty = existing.qty if existing else Decimal("0")
        old_avg = existing.avg_entry_price if existing else Decimal("0")
        new_qty = old_qty + delta

        if old_qty == 0 or (old_qty > 0) == (delta > 0):
            # Opening or extending in the same direction: weighted-average entry.
            total_abs = abs(old_qty) + abs(delta)
            new_avg = ((old_avg * abs(old_qty)) + (price * abs(delta))) / total_abs if total_abs else price
        else:
            # Reducing / closing / flipping: realize P&L on the closed portion.
            closed = min(abs(old_qty), abs(delta))
            direction = Decimal("1") if old_qty > 0 else Decimal("-1")
            self._realized_pnl_today += (price - old_avg) * closed * direction
            if abs(delta) < abs(old_qty):
                new_avg = old_avg                  # partial close, entry unchanged
            elif abs(delta) == abs(old_qty):
                new_avg = Decimal("0")             # fully flat
            else:
                new_avg = price                    # flipped: remainder opens at fill price

        if new_qty == 0:
            self._positions.pop(symbol, None)
            return

        self._positions[symbol] = InternalPosition(
            symbol=symbol,
            qty=new_qty,
            avg_entry_price=new_avg,
            market_value=new_qty * price,
            unrealized_pl=Decimal("0"),
            unrealized_plpc=Decimal("0"),
            side="long" if new_qty > 0 else "short",
        )

    # -------------------------------------------------------------------- marks
    def update_mark(self, symbol: str, price: float | Decimal) -> None:
        """Record the latest price for a symbol (drives the live equity estimate)."""
        self._marks[symbol.upper()] = Decimal(str(price))
        self._recompute_peak()

    def _recompute_peak(self) -> None:
        equity = self.get_equity_estimate()
        if equity > self._peak_equity:
            self._peak_equity = equity

    # ----------------------------------------------------------------- queries
    def get_position(self, symbol: str) -> InternalPosition | None:
        return self._positions.get(symbol.upper())

    def get_all_positions(self) -> list[InternalPosition]:
        return list(self._positions.values())

    def get_account(self) -> InternalAccount | None:
        return self._account

    def get_unrealized_pnl(self) -> Decimal:
        total = Decimal("0")
        for pos in self._positions.values():
            mark = self._marks.get(pos.symbol.upper(), pos.avg_entry_price)
            # Signed qty makes this correct for shorts: short qty<0, price down -> profit.
            total += (mark - pos.avg_entry_price) * pos.qty
        return total

    def get_gross_exposure(self) -> Decimal:
        """Total open notional (longs + |shorts|) valued at last-seen marks, entry
        price otherwise. Drives the aggregate exposure cap in sizing."""
        total = Decimal("0")
        for pos in self._positions.values():
            mark = self._marks.get(pos.symbol.upper(), pos.avg_entry_price)
            total += abs(pos.qty) * mark
        return total

    def get_daily_pnl(self) -> Decimal:
        return self._realized_pnl_today + self.get_unrealized_pnl()

    def get_equity_estimate(self) -> Decimal:
        return self._start_equity + self.get_daily_pnl()

    def get_peak_equity(self) -> Decimal:
        return self._peak_equity
