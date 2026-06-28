from __future__ import annotations

from unittest.mock import MagicMock

from trendchimp.safety import SafetyController
from trendchimp.signals.models import OrderSide


def test_default_does_not_halt():
    sc = SafetyController(notifier=MagicMock())
    assert sc.halt_entries is False


def test_position_unprotected_latches_halt_and_alerts():
    notifier = MagicMock()
    sc = SafetyController(notifier=notifier, halt_on_unprotected=True)
    sc.position_unprotected("AAPL", OrderSide.SELL, 10, reason="stop_attach_failed")
    assert sc.halt_entries is True
    assert "AAPL" in sc.halt_reason
    notifier.alert.assert_called_once()
    subject = notifier.alert.call_args.kwargs.get("subject", "")
    assert "AAPL" in subject


def test_halt_disabled_still_alerts_but_does_not_latch():
    notifier = MagicMock()
    sc = SafetyController(notifier=notifier, halt_on_unprotected=False)
    sc.position_unprotected("AAPL", OrderSide.SELL, 10)
    assert sc.halt_entries is False
    notifier.alert.assert_called_once()


def test_repeated_unprotected_alert_is_deduped():
    """The watchdog re-runs recovery every interval; a persistently unprotected
    position must not email on every cycle — alert once per (symbol, reason)."""
    notifier = MagicMock()
    sc = SafetyController(notifier=notifier, halt_on_unprotected=True)
    sc.position_unprotected("AAPL", OrderSide.SELL, 10, reason="stop_recovery_failed")
    sc.position_unprotected("AAPL", OrderSide.SELL, 10, reason="stop_recovery_failed")
    assert notifier.alert.call_count == 1


def test_distinct_unprotected_events_each_alert():
    notifier = MagicMock()
    sc = SafetyController(notifier=notifier, halt_on_unprotected=True)
    sc.position_unprotected("AAPL", OrderSide.SELL, 10, reason="stop_recovery_failed")
    sc.position_unprotected("MSFT", OrderSide.SELL, 5, reason="stop_recovery_failed")
    assert notifier.alert.call_count == 2


def test_notify_passes_through_to_notifier():
    notifier = MagicMock()
    sc = SafetyController(notifier=notifier)
    sc.notify(subject="kill-switch", body="tripped")
    notifier.alert.assert_called_once_with(subject="kill-switch", body="tripped")
