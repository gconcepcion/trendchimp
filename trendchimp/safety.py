"""Cross-cutting safety state: the fail-safe halt latch and alert dispatch.

A single ``SafetyController`` is shared by the order manager, risk manager, recovery,
and the runner so every "position unprotected" path behaves uniformly:

  * **keep** the position (never auto-flatten),
  * **alert** the operator (email/SMTP via the notifier), and
  * **halt** all new entries until the bot is restarted / cleared.

The risk manager consults ``halt_entries`` as an additional entry gate, alongside the
kill-switch.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trendchimp.notify import Notifier
    from trendchimp.signals.models import OrderSide

logger = logging.getLogger(__name__)
audit = logging.getLogger("trendchimp.audit")


class SafetyController:
    def __init__(self, notifier: "Notifier | None" = None, halt_on_unprotected: bool = True) -> None:
        if notifier is None:
            from trendchimp.notify import NullNotifier

            notifier = NullNotifier()
        self._notifier = notifier
        self._halt_on_unprotected = halt_on_unprotected
        self._halt_entries = False
        self._halt_reason: str | None = None
        self._alerted: set[tuple[str, str]] = set()

    @property
    def halt_entries(self) -> bool:
        return self._halt_entries

    @property
    def halt_reason(self) -> str | None:
        return self._halt_reason

    def position_unprotected(
        self, symbol: str, side: "OrderSide", qty: int, reason: str = "",
    ) -> None:
        """Fail-safe entry point: a live position could not be protected.

        Per policy: keep the position, alert, and latch a halt on new entries.
        """
        symbol = symbol.upper()
        side_str = getattr(side, "value", str(side))
        if self._halt_on_unprotected and not self._halt_entries:
            self._halt_entries = True
            self._halt_reason = f"unprotected position {symbol}"
            audit.error("ENTRIES_HALTED", extra={"symbol": symbol, "reason": reason})
            logger.critical("New entries HALTED — %s is unprotected (%s).", symbol, reason)
        # De-dupe alerts: the watchdog re-runs recovery every interval, so a position
        # that stays unprotected would otherwise email on every cycle. Alert once per
        # (symbol, reason); the latched halt and audit log already track the state.
        if (symbol, reason) in self._alerted:
            return
        self._alerted.add((symbol, reason))
        self._notifier.alert(
            subject=f"[trendchimp] UNPROTECTED position: {symbol}",
            body=(
                f"{symbol} {side_str} x{qty} could not be protected ({reason}).\n\n"
                f"The position is still OPEN. New entries are halted"
                f"{' (halt disabled in config)' if not self._halt_on_unprotected else ''}. "
                f"Place a protective stop manually now."
            ),
        )

    def notify(self, subject: str, body: str) -> None:
        """Best-effort alert for non-halting events (e.g. kill-switch trip)."""
        self._notifier.alert(subject=subject, body=body)
