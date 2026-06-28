from __future__ import annotations

from trendchimp.data.feed import MarketDataFeed
from trendchimp.data.stream_manager import StreamManager


def _stream_manager(default_settings, on_reconnect=None):
    sm = StreamManager(
        feed=MarketDataFeed(), settings=default_settings,
        stock_stream=None, trading_stream=None, symbols=["AAPL"],
        on_reconnect=on_reconnect,
    )
    sm._reconnect_base_delay = 0.0  # don't actually sleep in tests
    return sm


async def test_reconnect_retries_until_stopping(default_settings):
    """A dropped stream is retried with the reconnect hook fired each time, until a
    stop is requested."""
    state = {"factory": 0, "hook": 0}
    sm = None

    async def factory():
        state["factory"] += 1
        if state["factory"] < 3:
            raise ConnectionError("dropped")
        sm.request_stop()  # third attempt succeeds and we ask the loop to exit

    async def hook():
        state["hook"] += 1

    sm = _stream_manager(default_settings, on_reconnect=hook)
    await sm._run_with_reconnect("test", factory)

    assert state["factory"] == 3  # initial + 2 reconnects
    assert state["hook"] == 2     # hook fired on each reconnect


async def test_no_reconnect_after_stop_requested(default_settings):
    """If the stream coroutine returns while a stop is in flight, don't reconnect."""
    state = {"factory": 0}
    sm = _stream_manager(default_settings)

    async def factory():
        state["factory"] += 1
        sm.request_stop()  # graceful shutdown — should not retry

    await sm._run_with_reconnect("test", factory)
    assert state["factory"] == 1
