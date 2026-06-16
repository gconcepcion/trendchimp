from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field


@dataclass
class BreakoutCandidate:
    """A symbol scored for trend/breakout fitness (carries raw metrics for the AI)."""

    symbol: str
    price: float
    score: float           # composite trend-fitness, 0–100
    pct_from_high_20: float  # (price / 20-day high) - 1; ~0 means at the breakout edge
    pct_from_high_55: float
    pct_above_sma200: float
    trend_aligned: bool      # price > SMA50 > SMA200
    adx: float               # Wilder ADX(14); trend strength
    momentum_3m: float       # 3-month return, fraction (0.18 = +18%)
    atr_pct: float           # ATR(14) / price
    dollar_volume: float     # 20-day avg daily dollar volume


class FinalPick(BaseModel):
    """One ticker Claude selected for the trading universe."""

    symbol: str = Field(description="Ticker symbol, exactly as given in the candidate list.")
    conviction: int = Field(description="Conviction from 1 (low) to 5 (high).")
    rationale: str = Field(description="One concise sentence on why this is a good breakout/trend candidate.")


class UniverseSelection(BaseModel):
    """Structured-output schema for Claude's universe pick."""

    picks: list[FinalPick]
