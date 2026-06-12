from __future__ import annotations

from collections import deque


class DonchianChannel:
    """Rolling N-bar high/low channel.

    Holds the highs and lows of the most recent ``length`` bars. The breakout
    test for the current bar must be made against ``upper()`` / ``lower()``
    BEFORE the current bar is pushed, so the channel reflects prior bars only
    (no look-ahead).
    """

    def __init__(self, length: int) -> None:
        if length < 1:
            raise ValueError("Donchian channel length must be >= 1")
        self.length = length
        self._highs: deque[float] = deque(maxlen=length)
        self._lows: deque[float] = deque(maxlen=length)

    @property
    def ready(self) -> bool:
        return len(self._highs) == self.length

    def upper(self) -> float | None:
        return max(self._highs) if self.ready else None

    def lower(self) -> float | None:
        return min(self._lows) if self.ready else None

    def push(self, high: float, low: float) -> None:
        self._highs.append(high)
        self._lows.append(low)


class WilderATR:
    """Average True Range using Wilder's smoothing.

    Call ``current()`` to read the ATR through the prior bar, then ``update()``
    with the current bar's H/L/C. Seeds with a simple average of the first
    ``period`` true ranges, then applies Wilder smoothing.
    """

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError("ATR period must be >= 1")
        self.period = period
        self._prev_close: float | None = None
        self._seed_trs: list[float] = []
        self._atr: float | None = None

    @property
    def ready(self) -> bool:
        return self._atr is not None

    def current(self) -> float | None:
        return self._atr

    def update(self, high: float, low: float, close: float) -> None:
        if self._prev_close is None:
            # First bar: no prior close, true range is just the bar range.
            tr = high - low
        else:
            tr = max(
                high - low,
                abs(high - self._prev_close),
                abs(low - self._prev_close),
            )

        if self._atr is None:
            self._seed_trs.append(tr)
            if len(self._seed_trs) == self.period:
                self._atr = sum(self._seed_trs) / self.period
        else:
            self._atr = (self._atr * (self.period - 1) + tr) / self.period

        self._prev_close = close
