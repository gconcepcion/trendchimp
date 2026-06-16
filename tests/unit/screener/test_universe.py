from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from trendchimp.screener.universe import _FALLBACK_SYMBOLS, SymbolUniverse


def _write_cache(cache_dir, symbols, age_days):
    os.makedirs(cache_dir, exist_ok=True)
    fetched_at = datetime.now(tz=timezone.utc) - timedelta(days=age_days)
    with open(os.path.join(cache_dir, "sp500.json"), "w", encoding="utf-8") as fh:
        json.dump({"symbols": symbols, "fetched_at": fetched_at.isoformat()}, fh)


def test_fresh_cache_is_used_without_fetching(tmp_path, monkeypatch):
    _write_cache(str(tmp_path), ["AAA", "BBB"], age_days=1)
    uni = SymbolUniverse()
    # If it tries to fetch, fail loudly — a fresh cache must avoid the network.
    monkeypatch.setattr(uni, "_fetch_sp500", lambda: (_ for _ in ()).throw(AssertionError("fetched")))
    assert uni.get_sp500(str(tmp_path)) == ["AAA", "BBB"]


def test_stale_cache_triggers_fetch(tmp_path, monkeypatch):
    _write_cache(str(tmp_path), ["OLD"], age_days=10)
    uni = SymbolUniverse()
    monkeypatch.setattr(uni, "_fetch_sp500", lambda: ["NEW1", "NEW2"])
    assert uni.get_sp500(str(tmp_path)) == ["NEW1", "NEW2"]


def test_fetch_failure_falls_back(tmp_path, monkeypatch):
    uni = SymbolUniverse()
    monkeypatch.setattr(uni, "_fetch_sp500", lambda: (_ for _ in ()).throw(RuntimeError("no net")))
    assert uni.get_sp500(str(tmp_path)) == _FALLBACK_SYMBOLS
