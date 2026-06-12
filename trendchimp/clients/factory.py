from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trendchimp.config.settings import TrendChimpSettings


class ClientFactory:
    """Sole place in the codebase that imports and constructs alpaca-py clients."""

    def __init__(self, settings: "TrendChimpSettings") -> None:
        self._settings = settings

    def _auth(self) -> dict[str, str]:
        return {
            "api_key": self._settings.alpaca.api_key,
            "secret_key": self._settings.alpaca.secret_key,
        }

    def make_trading_client(self) -> Any:
        from alpaca.trading.client import TradingClient

        return TradingClient(**self._auth(), paper=self._settings.alpaca.paper)

    def make_stock_historical_client(self) -> Any:
        from alpaca.data.historical import StockHistoricalDataClient

        return StockHistoricalDataClient(**self._auth())

    def make_stock_stream(self) -> Any:
        from alpaca.data.live import StockDataStream

        return StockDataStream(**self._auth())

    def make_trading_stream(self) -> Any:
        from alpaca.trading.stream import TradingStream

        return TradingStream(**self._auth(), paper=self._settings.alpaca.paper)
