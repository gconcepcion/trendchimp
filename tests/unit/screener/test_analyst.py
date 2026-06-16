from __future__ import annotations

import anthropic

from trendchimp.config.settings import ScreenerSettings
from trendchimp.screener.analyst import TrendAnalyst
from trendchimp.screener.models import BreakoutCandidate, FinalPick, UniverseSelection


def _cand(symbol, score):
    return BreakoutCandidate(
        symbol=symbol, price=100.0, score=score, pct_from_high_20=0.0,
        pct_from_high_55=0.0, pct_above_sma200=0.1, trend_aligned=True,
        adx=30.0, momentum_3m=0.2, atr_pct=0.02, dollar_volume=5e8,
    )


def _fake_anthropic(parsed):
    class _Resp:
        parsed_output = parsed

    class _Messages:
        def parse(self, **kwargs):
            return _Resp()

    class _Client:
        def __init__(self, *a, **k):
            self.messages = _Messages()

    return _Client


def test_selection_filters_hallucinated_and_clamps(monkeypatch):
    candidates = [_cand("AAA", 90), _cand("BBB", 80), _cand("CCC", 70)]
    parsed = UniverseSelection(picks=[
        FinalPick(symbol="aaa", conviction=9, rationale="r1"),   # clamp 9->5, lowercase->AAA
        FinalPick(symbol="ZZZZ", conviction=4, rationale="hallucinated"),  # not a candidate
        FinalPick(symbol="AAA", conviction=2, rationale="dup"),  # duplicate, dropped
        FinalPick(symbol="BBB", conviction=3, rationale="r2"),
    ])
    monkeypatch.setattr(anthropic, "Anthropic", _fake_anthropic(parsed))

    picks = TrendAnalyst().select(candidates, ScreenerSettings(anthropic_api_key="x", final_picks=5))
    symbols = [p.symbol for p in picks]
    assert symbols == ["AAA", "BBB"]
    assert picks[0].conviction == 5  # clamped


def test_api_error_falls_back_to_technical(monkeypatch):
    candidates = [_cand("LOW", 40), _cand("HIGH", 95), _cand("MID", 70)]

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("api down")

    monkeypatch.setattr(anthropic, "Anthropic", _Boom)

    picks = TrendAnalyst().select(candidates, ScreenerSettings(anthropic_api_key="x", final_picks=2))
    assert [p.symbol for p in picks] == ["HIGH", "MID"]  # top by score
    assert all("AI selection unavailable" in p.rationale for p in picks)


def test_empty_candidates_returns_empty():
    assert TrendAnalyst().select([], ScreenerSettings(anthropic_api_key="x")) == []
