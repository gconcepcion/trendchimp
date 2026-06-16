# trendchimp

A Donchian Channel / Turtle breakout trend-following trading bot for [Alpaca](https://alpaca.markets/),
specialized for **US equities, long and short**. Paper-trading first, with a strict risk
guardrail layer and an optional AI-assisted universe screener.

## Architecture

```
[ Alpaca minute stream ] -> [ DailyBarAggregator ] -> [ DonchianBreakoutStrategy ] -> [ RiskManager + KillSwitch ] -> [ OrderManager ]
        (1-min bars)          (rolls up to daily)        (channel + ATR)               (Turtle N-unit sizing,          (2N protective stop
                                                                                        daily-loss kill-switch)          on every entry)
```

- **Data** (`data/aggregator.py`): Alpaca's live websocket only streams **1-minute** bars,
  so `DailyBarAggregator` rolls them up into completed daily OHLCV bars (regular hours only).
  Marks and the kill-switch update every minute; the strategy only acts on a completed daily
  bar, so decisions land at the next session's open (classic next-bar Turtle entry).
- **Strategy** (`strategies/donchian.py`): breakout above the prior `entry_channel`-bar high
  goes long, below the low goes short; opposite breakout of the shorter `exit_channel` exits.
  Indicators are computed incrementally against *prior* bars only (no look-ahead).
- **Risk** (`risk/`): ATR/"N-unit" position sizing (risk a fixed % of equity per trade), a
  latched daily-loss / drawdown **kill-switch** that shuts the bot down, and gate-based
  entry checks (max positions, duplicate/shorting guards).
- **Execution** (`orders/manager.py`): submits whole-share market orders and attaches a 2N
  protective stop to **every** entry on fill — a SELL stop below a long, a BUY-to-cover stop
  above a short.
- **Startup recovery** (`runner/recovery.py`): on every (re)start the bot guarantees each open
  position has a live protective stop, healing the crash/disconnect window where an entry
  filled but its stop was never placed. If a position has already gapped past where its stop
  should sit, it is flattened at market instead; a stop it cannot place is escalated loudly.
  A held position whose symbol has **dropped out of the current universe** is no longer
  strategy-managed, so its static stop is replaced with a broker **trailing stop** that
  trails `risk.orphan_trailing_atr_mult × N` (ATR; default 2N), falling back to a flat
  percent (`risk.orphan_trailing_stop_pct`) only when ATR can't be computed.

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
trendchimp screen      # screen the S&P 500 -> write the trading universe (see below)
trendchimp run         # start the paper bot (always-on)
trendchimp run --once  # batch: act on the latest completed daily bar, then exit

# Full pipeline without sending orders:
TRENDCHIMP_TRADING__DRY_RUN=true trendchimp run
```

Live trading requires a deliberate double-lock: both `TRENDCHIMP_ALPACA__PAPER=false`
and `TRENDCHIMP_LIVE_TRADING_CONFIRMED=true`.

### Two ways to operate it

It's a **daily** system, so you can run it either way:

- **Always-on** (`trendchimp run`): stays connected, aggregates the minute stream into
  daily bars, and trades across the open. Only this mode runs the **intraday kill-switch**
  (live daily-loss / drawdown halt). Holds the single Alpaca data connection while up.
- **Batch** (`trendchimp run --once`): on each run it reconciles state, refreshes
  protective stops, acts on the latest **completed** daily bar, and exits — no live
  connection. New entries carry a server-side (OTO) stop so they're protected on fill
  without a listener. Best run a few minutes after the open and cron'd 1–2×/day:

  ```cron
  # ~5 min after the US open (9:35 ET), Mon–Fri
  35 13 * * 1-5  cd /path/to/trendchimp && ./venv/bin/trendchimp run --once >> logs/once.log 2>&1
  ```

  Protective/trailing stops are broker-side (GTC), so positions stay protected between
  runs even though nothing is connected. Re-running the same day is idempotent (it won't
  double-enter). The only thing batch mode gives up vs. always-on is the intraday
  kill-switch.

## Running it day-to-day (manual)

There is no service/scheduler — the bot is launched by hand each session. It reads
`./.env` (paper keys, `PAPER=true`) and runs in the foreground.

```bash
cd /home/gconcepcion/sandbox/trendchimp
source venv/bin/activate
trendchimp status        # optional pre-flight: account summary (read-only)
trendchimp positions     # optional pre-flight: open positions (read-only)
trendchimp run           # start the bot (loads ./.env; override with --env-file)
```

To also keep a console log on disk, tee stdout (the app itself only writes the audit
JSONL at `logs/trades.jsonl`, not the console file):

```bash
trendchimp run 2>&1 | tee -a logs/bot.console.log
```

**Stop** with Ctrl-C (SIGINT). The bot traps SIGINT/SIGTERM and closes the websocket
cleanly — important because of the single-connection limit below.

Operational notes:

- **One data connection only.** Never run two instances. If you see `connection limit
  exceeded`, kill every instance and wait ~2–5 min for Alpaca to drop the stale socket
  before restarting.
- **Stops self-heal on start.** Startup recovery places a protective stop for any open
  position missing one — so just starting the bot protects naked positions (watch for
  `STOP_RECOVERED` in `logs/trades.jsonl`).
- **Daily cadence.** Decisions land at the next session's open, so "no trades today" is
  expected, not a bug.
- **Symbols** come from `./universe.json` when present (`TRENDCHIMP_TRADING__UNIVERSE_FILE`);
  refresh it with `trendchimp screen`. Re-screening daily is fine: a held name that's
  re-picked stays strategy-managed, while one that drops out is handed off to a trailing
  stop (above) rather than being left on a static stop.

## Universe screener

`trendchimp screen` builds the list of symbols the bot trades. It pulls the S&P 500
(cached to `screener.cache_dir` for 7 days), scores every name for breakout/trend fitness
(proximity to the 20-day high, SMA trend alignment, ADX, momentum, with liquidity and
volatility gates), then asks Claude to pick the final universe from that shortlist. The
result is written to a JSON universe file.

```bash
trendchimp screen                 # technical scoring + Claude selection
trendchimp screen --no-ai         # technical scoring only (no API key needed)
trendchimp screen --top-n 40 --picks 12 --output universe.json
```

Point the bot at the file with `TRENDCHIMP_TRADING__UNIVERSE_FILE`. When set and present,
`run` trades those symbols instead of `TRENDCHIMP_TRADING__SYMBOLS`; otherwise it falls back
to the hand-configured list. The AI step needs `TRENDCHIMP_SCREENER__ANTHROPIC_API_KEY`
(model defaults to `claude-opus-4-8`); `--no-ai` skips it and writes the top technical picks.

### Small accounts: price-filtered universe

Sizing is whole-share with a 1% risk budget and a 20% per-position cap, so a **$1–2K**
account rounds most S&P names to **0 shares** (it can't afford a share of a $300+ stock
within the cap). Screen a cheaper universe so it can actually take positions:

```bash
trendchimp screen --max-price 50 --min-price 5 --output universe.json
```

`--min-price`/`--max-price` (or `TRENDCHIMP_SCREENER__MIN_PRICE`/`MAX_PRICE`) default to
`0` = no limit, so the standard run is unchanged. For a small live account also bump
`RISK_PER_TRADE_PCT`/`MAX_POSITION_PCT` and disable shorting — see the commented
**small-account preset** in `.env.example`. (At $10–20K the defaults are fine; leave the
price band at 0.)

## Tests

```bash
pytest -q
pytest --cov=trendchimp --cov-report=term-missing
```

## Not yet implemented (fast-follow)

- Backtest engine (the strategy is broker-agnostic, so it slots in via `on_bar` replay).
- Turtle pyramiding (`max_units_per_symbol > 1`, add units at ½N intervals).
- Crypto support.
