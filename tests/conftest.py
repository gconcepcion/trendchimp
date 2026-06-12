from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable
from unittest.mock import MagicMock

import pytest

from trendchimp.config.settings import (
    AlpacaSettings,
    LoggingSettings,
    RiskSettings,
    StrategySettings,
    TradingSettings,
    TrendChimpSettings,
)
from trendchimp.data.models import Bar, InternalAccount, InternalPosition
from trendchimp.portfolio.state import PortfolioState

_BASE = datetime(2024, 1, 1, tzinfo=timezone.utc)


# ----------------------------------------------------------------- bar builders
def make_bar(
    symbol: str = "AAPL",
    close: float = 100.0,
    high: float | None = None,
    low: float | None = None,
    open_: float | None = None,
    volume: float = 1000.0,
    timestamp: datetime | None = None,
) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=timestamp or _BASE,
        open=open_ if open_ is not None else close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=volume,
    )


def make_bar_sequence(symbol: str, rows: Iterable) -> list[Bar]:
    """Build a bar sequence. Each row is either a close float or an (high, low, close) tuple."""
    bars: list[Bar] = []
    for i, row in enumerate(rows):
        ts = _BASE + timedelta(days=i)
        if isinstance(row, (int, float)):
            bars.append(make_bar(symbol, close=float(row), timestamp=ts))
        else:
            high, low, close = row
            bars.append(make_bar(symbol, close=close, high=high, low=low, timestamp=ts))
    return bars


# ----------------------------------------------------------- alpaca-ish mocks
def make_trade_update(
    symbol: str = "AAPL",
    side: str = "buy",
    qty: str = "10",
    price: str = "100.0",
    status: str = "filled",
    order_id: str = "ord-1",
    is_stop: bool = False,
):
    order = MagicMock()
    order.id = order_id
    order.symbol = symbol
    order.side = side
    order.qty = qty
    order.status = status
    order.filled_qty = qty if status == "filled" else None
    order.filled_avg_price = price if status == "filled" else None
    order.submitted_at = datetime.now(tz=timezone.utc)
    update = MagicMock()
    update.order = order
    update.event = status
    return update


@pytest.fixture
def mock_trading_client():
    client = MagicMock()
    client.get_account.return_value = InternalAccount(
        buying_power=Decimal("100000"),
        cash=Decimal("100000"),
        portfolio_value=Decimal("100000"),
        equity=Decimal("100000"),
    )
    client.get_positions.return_value = []
    client.get_open_orders.return_value = []

    _ids = itertools.count(1)

    def _submit(_request):
        raw = MagicMock()
        raw.id = f"ord-{next(_ids)}"
        raw.status = "new"
        return raw

    client.submit_order.side_effect = _submit
    client.cancel_order.return_value = None
    client.cancel_all_orders.return_value = None
    return client


@pytest.fixture
def portfolio_state(mock_trading_client) -> PortfolioState:
    state = PortfolioState()
    state.reconcile(mock_trading_client)
    return state


@pytest.fixture
def default_settings() -> TrendChimpSettings:
    return TrendChimpSettings(
        alpaca=AlpacaSettings(api_key="test_key", secret_key="test_secret", paper=True),
        trading=TradingSettings(symbols=["AAPL"], timeframe="1Day", dry_run=False),
        strategy=StrategySettings(name="donchian_breakout", params={}),
        risk=RiskSettings(),
        logging=LoggingSettings(level="WARNING", format="console", audit_log_path=""),
    )


def make_position(symbol="AAPL", qty="10", avg="100", side=None) -> InternalPosition:
    q = Decimal(str(qty))
    return InternalPosition(
        symbol=symbol, qty=q, avg_entry_price=Decimal(str(avg)),
        market_value=q * Decimal(str(avg)), unrealized_pl=Decimal("0"),
        unrealized_plpc=Decimal("0"), side=side or ("short" if q < 0 else "long"),
    )
