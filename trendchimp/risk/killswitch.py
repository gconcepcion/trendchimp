from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trendchimp.portfolio.state import PortfolioState

logger = logging.getLogger(__name__)
audit = logging.getLogger("trendchimp.audit")


class KillSwitch:
    """Latched daily-loss / drawdown halt.

    The runner polls ``check()`` each bar; once tripped it stays tripped for the
    session (no flapping) and the bot shuts down. Separate from the per-signal
    RiskManager so it can halt the whole process, not just block one order.
    """

    def __init__(self, daily_loss_limit_pct: float, max_drawdown_pct: float) -> None:
        self._daily_loss_limit_pct = Decimal(str(daily_loss_limit_pct))
        self._max_drawdown_pct = Decimal(str(max_drawdown_pct))
        self._tripped = False
        self._reason: str | None = None

    @property
    def tripped(self) -> bool:
        return self._tripped

    @property
    def reason(self) -> str | None:
        return self._reason

    def check(self, portfolio: "PortfolioState") -> bool:
        if self._tripped:
            return True

        account = portfolio.get_account()
        if account is None:
            return False

        equity = portfolio.get_equity_estimate()
        if equity <= 0:
            return False

        daily_pnl = portfolio.get_daily_pnl()
        if daily_pnl <= -(equity * self._daily_loss_limit_pct):
            self._trip("daily_loss_limit", daily_pnl=daily_pnl, equity=equity)
            return True

        peak = portfolio.get_peak_equity()
        if peak > 0:
            drawdown = (peak - equity) / peak
            if drawdown >= self._max_drawdown_pct:
                self._trip("max_drawdown", drawdown=drawdown, peak=peak, equity=equity)
                return True

        return False

    def _trip(self, reason: str, **context) -> None:
        self._tripped = True
        self._reason = reason
        logger.error("KILL-SWITCH TRIPPED: %s %s", reason, context)
        audit.info("KILLSWITCH_TRIPPED", extra={"reason": reason, **{k: str(v) for k, v in context.items()}})
