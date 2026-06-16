from __future__ import annotations

import json
import os

from trendchimp.screener.models import FinalPick
from trendchimp.screener.writer import load_universe_symbols, write_universe_file


def test_write_and_load_roundtrip(tmp_path):
    path = os.path.join(str(tmp_path), "universe.json")
    picks = [
        FinalPick(symbol="NVDA", conviction=5, rationale="strong uptrend"),
        FinalPick(symbol="AMD", conviction=3, rationale="breaking out"),
    ]
    write_universe_file(path, picks)

    payload = json.loads(open(path, encoding="utf-8").read())
    assert payload["symbols"] == ["NVDA", "AMD"]
    assert payload["strategy"] == "donchian_breakout"
    assert "generated_at" in payload
    assert payload["picks"][0] == {"symbol": "NVDA", "conviction": 5, "rationale": "strong uptrend"}

    assert load_universe_symbols(path) == ["NVDA", "AMD"]


def test_write_creates_parent_dir(tmp_path):
    path = os.path.join(str(tmp_path), "nested", "dir", "universe.json")
    write_universe_file(path, [FinalPick(symbol="SPY", conviction=4, rationale="x")])
    assert os.path.exists(path)
