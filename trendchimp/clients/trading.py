from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from trendchimp.data.models import InternalAccount, InternalPosition

logger = logging.getLogger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    """Retry on network errors and HTTP 429/5xx responses."""
    try:
        import requests

        if isinstance(exc, requests.exceptions.RequestException):
            return True
    except ImportError:
        pass
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in (429, 500, 502, 503, 504)


_retry = retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=15),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)


class TradingClientWrapper:
    """Thin façade over alpaca-py TradingClient with retry and type conversion."""

    def __init__(self, client: Any) -> None:
        self._client = client

    @_retry
    def get_account(self) -> InternalAccount:
        acct = self._client.get_account()
        return InternalAccount(
            buying_power=Decimal(str(acct.buying_power)),
            cash=Decimal(str(acct.cash)),
            portfolio_value=Decimal(str(acct.portfolio_value)),
            equity=Decimal(str(acct.equity)),
            currency=getattr(acct, "currency", "USD"),
        )

    @_retry
    def get_positions(self) -> list[InternalPosition]:
        return [self._convert_position(p) for p in self._client.get_all_positions()]

    @_retry
    def get_open_orders(self) -> list[Any]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        request = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        return self._client.get_orders(filter=request)

    @_retry
    def submit_order(self, request: Any) -> Any:
        return self._client.submit_order(order_data=request)

    @_retry
    def cancel_order(self, order_id: str) -> None:
        self._client.cancel_order_by_id(order_id)

    @_retry
    def cancel_all_orders(self) -> None:
        self._client.cancel_orders()

    @_retry
    def get_clock(self) -> Any:
        return self._client.get_clock()

    def _convert_position(self, pos: Any) -> InternalPosition:
        qty = Decimal(str(pos.qty))
        return InternalPosition(
            symbol=str(pos.symbol).upper(),
            qty=qty,
            avg_entry_price=Decimal(str(pos.avg_entry_price)),
            market_value=Decimal(str(pos.market_value)),
            unrealized_pl=Decimal(str(pos.unrealized_pl)),
            unrealized_plpc=Decimal(str(pos.unrealized_plpc)),
            side=str(getattr(pos, "side", "long")),
        )
