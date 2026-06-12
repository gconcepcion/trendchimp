from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from enum import Enum
from typing import Any, Callable, Coroutine

from trendchimp.data.models import Bar, Quote, Trade

logger = logging.getLogger(__name__)

DataEvent = Bar | Quote | Trade
HandlerFunc = Callable[[DataEvent], Coroutine[Any, Any, Any]]


class DataType(str, Enum):
    BAR = "bar"
    QUOTE = "quote"
    TRADE = "trade"


_EVENT_TYPE_MAP: dict[type, DataType] = {
    Bar: DataType.BAR,
    Quote: DataType.QUOTE,
    Trade: DataType.TRADE,
}


class MarketDataFeed:
    """Async pub/sub hub routing market data events to registered handlers."""

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, DataType], list[HandlerFunc]] = defaultdict(list)

    def subscribe(self, symbol: str, data_type: DataType, handler: HandlerFunc) -> None:
        key = (symbol.upper(), data_type)
        if handler not in self._handlers[key]:
            self._handlers[key].append(handler)
            logger.debug("Subscribed handler to %s %s", symbol, data_type)

    async def publish(self, event: DataEvent) -> None:
        data_type = _EVENT_TYPE_MAP.get(type(event))
        if data_type is None:
            logger.warning("Unknown event type: %s", type(event))
            return

        handlers = self._handlers.get((event.symbol.upper(), data_type), [])
        if not handlers:
            return

        tasks = [asyncio.create_task(handler(event)) for handler in handlers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.error("Handler error during publish: %s", result, exc_info=result)
