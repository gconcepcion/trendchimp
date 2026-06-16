from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from trendchimp.screener.models import FinalPick

logger = logging.getLogger(__name__)


def write_universe_file(
    path: str,
    picks: list[FinalPick],
    strategy: str = "donchian_breakout",
) -> None:
    """Write the chosen universe to a JSON file the bot reads on startup."""
    payload = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "strategy": strategy,
        "symbols": [p.symbol for p in picks],
        "picks": [
            {"symbol": p.symbol, "conviction": p.conviction, "rationale": p.rationale}
            for p in picks
        ],
    }
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    logger.info("Wrote %d symbols to universe file %s", len(picks), path)


def load_universe_symbols(path: str) -> list[str]:
    """Read the symbol list from a universe file. Raises on missing/corrupt file."""
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    symbols = payload.get("symbols") or []
    if not isinstance(symbols, list) or not all(isinstance(s, str) for s in symbols):
        raise ValueError(f"universe file {path} has an invalid 'symbols' field")
    return [s.upper() for s in symbols]
