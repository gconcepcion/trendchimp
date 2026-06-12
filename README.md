# trendchimp

A Donchian Channel / Turtle breakout trend-following trading bot for [Alpaca](https://alpaca.markets/),
specialized for **US equities, long and short**. Paper-trading first, with a strict risk
guardrail layer.

## Architecture

```
[ Alpaca market data ] -> [ DonchianBreakoutStrategy ] -> [ RiskManager + KillSwitch ] -> [ OrderManager ]
        (streams)              (channel + ATR)              (Turtle N-unit sizing,          (2N bracket stop
                                                             daily-loss kill-switch)          on every entry)
```

- **Strategy** (`strategies/donchian.py`): breakout above the prior `entry_channel`-bar high
  goes long, below the low goes short; opposite breakout of the shorter `exit_channel` exits.
  Indicators are computed incrementally against *prior* bars only (no look-ahead).
- **Risk** (`risk/`): ATR/"N-unit" position sizing (risk a fixed % of equity per trade), a
  latched daily-loss / drawdown **kill-switch** that shuts the bot down, and gate-based
  entry checks (max positions, duplicate/shorting guards).
- **Execution** (`orders/manager.py`): submits whole-share market orders and attaches a 2N
  protective stop to **every** entry on fill — a SELL stop below a long, a BUY-to-cover stop
  above a short.

## Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # paste your Alpaca PAPER keys; keep PAPER=true
```

## Usage

```bash
trendchimp status      # read-only account summary
trendchimp positions   # open positions (long & short)
trendchimp run         # start the paper bot

# Full pipeline without sending orders:
TRENDCHIMP_TRADING__DRY_RUN=true trendchimp run
```

Live trading requires a deliberate double-lock: both `TRENDCHIMP_ALPACA__PAPER=false`
and `TRENDCHIMP_LIVE_TRADING_CONFIRMED=true`.

## Tests

```bash
pytest -q
pytest --cov=trendchimp --cov-report=term-missing
```

## Not yet implemented (fast-follow)

- Backtest engine (the strategy is broker-agnostic, so it slots in via `on_bar` replay).
- Turtle pyramiding (`max_units_per_symbol > 1`, add units at ½N intervals).
- Crypto support.
