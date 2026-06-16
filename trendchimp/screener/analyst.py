from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from trendchimp.screener.models import BreakoutCandidate, FinalPick, UniverseSelection

if TYPE_CHECKING:
    from trendchimp.config.settings import ScreenerSettings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a trend-following analyst selecting a trading universe for a Donchian "
    "channel breakout bot. The bot goes long on upside breakouts and short on downside "
    "breakouts, cutting losers fast and riding winners. From the candidate list below, "
    "choose the strongest, most liquid names that are in clean, established uptrends and "
    "sitting near a breakout — these give the breakout system the best follow-through. "
    "Prefer durable trends with solid trend strength (ADX) over barely-trending names. "
    "Avoid extended/parabolic names that have already run too far, and avoid choppy, "
    "directionless names. Only pick from the provided candidates, and use each symbol "
    "exactly as written. Assign conviction 1 (low) to 5 (high)."
)


class TrendAnalyst:
    """Uses Claude to pick the final trading universe from a breakout shortlist."""

    def select(
        self,
        candidates: list[BreakoutCandidate],
        settings: "ScreenerSettings",
    ) -> list[FinalPick]:
        if not candidates:
            return []
        try:
            return self._select_with_claude(candidates, settings)
        except Exception:
            logger.exception("Claude selection failed — falling back to top technical picks")
            return self.technical_fallback(candidates, settings.final_picks)

    def _select_with_claude(
        self, candidates: list[BreakoutCandidate], settings: "ScreenerSettings"
    ) -> list[FinalPick]:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        prompt = self._build_prompt(candidates, settings.final_picks)

        response = client.messages.parse(
            model=settings.model,
            max_tokens=4096,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            system=[{"type": "text", "text": _SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
            output_format=UniverseSelection,
        )
        selection = response.parsed_output
        if selection is None:
            logger.warning("Claude returned no parsed selection — using technical fallback")
            return self.technical_fallback(candidates, settings.final_picks)

        valid_symbols = {c.symbol for c in candidates}
        picks: list[FinalPick] = []
        seen: set[str] = set()
        for pick in selection.picks:
            sym = pick.symbol.strip().upper()
            if sym not in valid_symbols or sym in seen:
                continue  # ignore hallucinated or duplicate symbols
            seen.add(sym)
            picks.append(FinalPick(
                symbol=sym,
                conviction=max(1, min(5, int(pick.conviction))),
                rationale=pick.rationale.strip(),
            ))
            if len(picks) >= settings.final_picks:
                break

        if not picks:
            return self.technical_fallback(candidates, settings.final_picks)
        logger.info("Claude selected %d symbols", len(picks))
        return picks

    def _build_prompt(self, candidates: list[BreakoutCandidate], final_picks: int) -> str:
        lines = [
            f"Select up to {final_picks} tickers for the breakout universe from these "
            f"{len(candidates)} candidates (already pre-filtered for trend fitness).",
            "",
            "Metrics per candidate:",
            "  from20high = price vs the prior 20-day high (>=0 means breaking out)",
            "  aboveSMA200 = percent above the 200-day average",
            "  trendAligned = price > SMA50 > SMA200",
            "  ADX = trend strength (higher = stronger trend)",
            "  mom3m = 3-month return",
            "  ATR% = daily volatility as a fraction of price",
            "",
        ]
        for c in candidates:
            lines.append(
                f"- {c.symbol}: score={c.score} from20high={c.pct_from_high_20:+.1%} "
                f"aboveSMA200={c.pct_above_sma200:+.1%} trendAligned={c.trend_aligned} "
                f"ADX={c.adx} mom3m={c.momentum_3m:+.1%} ATR%={c.atr_pct:.1%} "
                f"price=${c.price}"
            )
        return "\n".join(lines)

    @staticmethod
    def technical_fallback(
        candidates: list[BreakoutCandidate], final_picks: int
    ) -> list[FinalPick]:
        """Top-`final_picks` by technical score — used offline (`--no-ai`) or on AI failure."""
        top = sorted(candidates, key=lambda c: c.score, reverse=True)[:final_picks]
        return [
            FinalPick(
                symbol=c.symbol,
                conviction=max(1, min(5, round(c.score / 20.0))),
                rationale="Top technical breakout candidate (AI selection unavailable).",
            )
            for c in top
        ]
