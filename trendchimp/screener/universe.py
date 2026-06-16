from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_CACHE_TTL_DAYS = 7

# Used only if the Wikipedia fetch fails and no cache exists.
_FALLBACK_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "BRK.B",
    "JPM", "UNH", "V", "XOM", "LLY", "JNJ", "MA", "PG", "AVGO", "HD", "CVX",
    "MRK", "ABBV", "KO", "PEP", "COST", "ADBE", "CRM", "AMD", "NFLX", "TMO",
]


class SymbolUniverse:
    """Provides the S&P 500 candidate universe, cached to disk for 7 days."""

    def get_sp500(self, cache_dir: str) -> list[str]:
        cache_path = os.path.join(cache_dir, "sp500.json")
        cached = self._read_cache(cache_path)
        if cached is not None:
            logger.info("Using cached S&P 500 universe (%d symbols)", len(cached))
            return cached

        try:
            symbols = self._fetch_sp500()
        except Exception:
            logger.exception("Failed to fetch S&P 500 list — using fallback symbols")
            return list(_FALLBACK_SYMBOLS)

        self._write_cache(cache_path, symbols)
        logger.info("Fetched S&P 500 universe (%d symbols)", len(symbols))
        return symbols

    def _fetch_sp500(self) -> list[str]:
        import io

        import pandas as pd
        import requests

        # Wikipedia 403s default urllib/pandas user-agents — send a real one.
        resp = requests.get(
            _SP500_URL,
            headers={"User-Agent": "trendchimp/0.1 (trading-bot screener)"},
            timeout=20,
        )
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text))
        df = tables[0]
        col = "Symbol" if "Symbol" in df.columns else df.columns[0]
        # Alpaca uses '.' for class shares (BRK.B); Wikipedia sometimes uses '-' or '.'.
        symbols = [str(s).strip().upper().replace("-", ".") for s in df[col].tolist()]
        symbols = [s for s in symbols if s and s.isascii()]
        if len(symbols) < 400:
            raise ValueError(f"Expected ~500 S&P symbols, got {len(symbols)}")
        return symbols

    def _read_cache(self, path: str) -> list[str] | None:
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
            fetched_at = datetime.fromisoformat(payload["fetched_at"])
            age_days = (datetime.now(tz=timezone.utc) - fetched_at).days
            if age_days >= _CACHE_TTL_DAYS:
                return None
            symbols = payload.get("symbols") or []
            return symbols or None
        except Exception:
            logger.warning("Could not read S&P 500 cache at %s — refetching", path)
            return None

    def _write_cache(self, path: str, symbols: list[str]) -> None:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(
                    {"symbols": symbols, "fetched_at": datetime.now(tz=timezone.utc).isoformat()},
                    fh,
                )
        except Exception:
            logger.warning("Could not write S&P 500 cache to %s", path)
