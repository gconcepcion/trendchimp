from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from trendchimp.config.settings import TrendChimpSettings

logger = logging.getLogger(__name__)

_MARKET_TZ = ZoneInfo("America/New_York")


class TradingBot:
    """Top-level orchestrator: wires feed -> strategy -> risk -> execution.

    Two run modes share the same wiring (`_assemble`) and startup protection
    (`_apply_startup_stops`):
      * `start()`  — always-on: streams minute bars, aggregates to daily, trades
                     across the open; the kill-switch sees live marks every minute.
      * `run_once()` — batch: act on the latest *completed* daily bar and exit, for
                     cron'ing 1-2x/day. No stream; new entries carry a server-side
                     (OTO) stop so they're protected without a fill listener.
    """

    def __init__(self, settings: "TrendChimpSettings") -> None:
        self._settings = settings
        self._running = False

    # ----------------------------------------------------------------- live mode
    def start(self) -> None:
        asyncio.run(self._run())

    async def _run(self) -> None:
        from trendchimp.data.aggregator import DailyBarAggregator
        from trendchimp.data.feed import DataType, MarketDataFeed
        from trendchimp.data.stream_manager import StreamManager

        c = self._assemble()
        await self._warmup(c.strategy, c.market_data, c.portfolio, c.symbols)
        self._apply_startup_stops(c)

        stock_stream = c.factory.make_stock_stream()
        trading_stream = c.factory.make_trading_stream()

        feed = MarketDataFeed()
        stop_event = asyncio.Event()

        # The live websocket only emits 1-minute bars; roll them up into daily
        # bars so the Turtle strategy runs once per completed session.
        aggregator = DailyBarAggregator()

        async def _on_bar(bar):
            # Every intraday bar refreshes the mark so the kill-switch and
            # protective stops see live prices; the strategy itself only acts on
            # completed daily bars emitted by the aggregator.
            c.portfolio.update_mark(bar.symbol, bar.close)
            if c.killswitch.check(c.portfolio):
                logger.error("Kill-switch tripped (%s) — initiating shutdown",
                             c.killswitch.reason)
                stop_event.set()
                return
            daily = aggregator.add(bar)
            if daily is None:
                return
            for sig in await c.strategy.on_bar(daily):
                decision = c.risk_manager.evaluate(sig, c.portfolio)
                if decision:
                    c.order_manager.submit(decision)

        for symbol in c.symbols:
            feed.subscribe(symbol, DataType.BAR, _on_bar)

        stream_manager = StreamManager(
            feed=feed, settings=self._settings,
            stock_stream=stock_stream, trading_stream=trading_stream,
            symbols=c.symbols,
        )
        stream_manager.add_trade_update_handler(c.order_manager.handle_trade_update)

        loop = asyncio.get_running_loop()

        def _signal_handler():
            logger.info("Shutdown signal received")
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

        self._running = True
        await stream_manager.start()
        logger.info("Streams connected. Watching %s", c.symbols)

        try:
            await stop_event.wait()
        finally:
            logger.info("Shutting down...")
            if c.killswitch.tripped and not self._settings.trading.dry_run:
                # Cancel pending entry orders but leave protective stops in place.
                c.order_manager.cancel_all()
            await stream_manager.stop()
            self._running = False
            logger.info("trendchimp stopped")

    # ---------------------------------------------------------------- batch mode
    def run_once(self) -> None:
        asyncio.run(self._run_once())

    async def _run_once(self) -> None:
        c = self._assemble()
        self._apply_startup_stops(c)
        logger.info("Run-once (batch): acting on the latest completed daily bar for %d symbols",
                    len(c.symbols))
        entries = exits = 0
        for symbol in c.symbols:
            e, x = await self._decide_once(c, symbol)
            entries += e
            exits += x
        logger.info("Run-once complete: %d entries, %d exits submitted (no stream opened)",
                    entries, exits)

    async def _decide_once(self, c: SimpleNamespace, symbol: str) -> tuple[int, int]:
        """Decide and act on the latest completed daily bar for one symbol."""
        needed = c.strategy.get_required_history()
        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(days=needed * 3 + 30)
        try:
            bars = c.market_data.get_bars(symbol, self._settings.trading.timeframe, start, end)
        except Exception:
            logger.exception("Run-once: bar fetch failed for %s — skipping", symbol)
            return 0, 0

        completed = self._completed_sessions(bars)
        if len(completed) <= needed:
            logger.info("Run-once: %s has too little history (%d completed bars) — skipping",
                        symbol, len(completed))
            return 0, 0

        # Prime channels/ATR on every bar except the latest completed one, without
        # acting (mirrors warmup); mute the per-signal logs so it isn't noisy.
        strat_logger = logging.getLogger("trendchimp.strategies")
        prev_level = strat_logger.level
        strat_logger.setLevel(logging.WARNING)
        try:
            for bar in completed[:-1]:
                await c.strategy.on_bar(bar)
        finally:
            strat_logger.setLevel(prev_level)

        # Align the strategy's directional state to the real broker position so we
        # look for the right action (exit if positioned, entry if flat).
        position = c.portfolio.get_position(symbol)
        c.strategy.seed_position(symbol, float(position.qty) if position else 0.0)

        entries = exits = 0
        for sig in await c.strategy.on_bar(completed[-1]):
            if sig.intent.is_entry and self._has_open_entry(c.order_manager, symbol):
                logger.info("Run-once: %s already has an open entry order — skipping duplicate",
                            symbol)
                continue
            decision = c.risk_manager.evaluate(sig, c.portfolio)
            if decision is None:
                continue
            # Entries carry a server-side OTO stop so they're protected on fill
            # without a stream; exits are plain market orders.
            placed = c.order_manager.submit(decision, attach_stop_bracket=True)
            if placed is not None or self._settings.trading.dry_run:
                if sig.intent.is_entry:
                    entries += 1
                else:
                    exits += 1
        return entries, exits

    @staticmethod
    def _completed_sessions(bars: list) -> list:
        """Drop a still-forming 'today' daily bar so decisions use only closed sessions."""
        if not bars:
            return []
        today = datetime.now(tz=_MARKET_TZ).date()
        completed = []
        for b in bars:
            ts = b.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts.astimezone(_MARKET_TZ).date() < today:
                completed.append(b)
        return completed

    @staticmethod
    def _has_open_entry(order_manager, symbol: str) -> bool:
        """True if there's already an open (non-stop) order for the symbol — guards a
        same-day re-run from double-submitting an entry."""
        symbol = symbol.upper()
        return any(o.symbol.upper() == symbol and not o.is_stop_order
                   for o in order_manager.get_active_orders())

    # ------------------------------------------------------------- shared wiring
    def _assemble(self) -> SimpleNamespace:
        """Build the trading components shared by live and batch mode (everything
        except the live streams)."""
        from trendchimp.clients.factory import ClientFactory
        from trendchimp.clients.market_data import MarketDataClient
        from trendchimp.clients.trading import TradingClientWrapper
        from trendchimp.logging.setup import configure_logging
        from trendchimp.orders.manager import OrderManager
        from trendchimp.portfolio.state import PortfolioState
        from trendchimp.risk.killswitch import KillSwitch
        from trendchimp.risk.manager import RiskManager
        from trendchimp.risk.sizing import TurtleUnitSizer
        from trendchimp.strategies.registry import StrategyRegistry

        import trendchimp.strategies.donchian  # noqa: F401  (register strategy)

        configure_logging(self._settings.logging)
        logger.info("trendchimp starting up (paper=%s, dry_run=%s)",
                    self._settings.alpaca.paper, self._settings.trading.dry_run)

        factory = ClientFactory(self._settings)
        trading_client = TradingClientWrapper(factory.make_trading_client())
        market_data = MarketDataClient(
            stock_client=factory.make_stock_historical_client(),
            feed=self._settings.alpaca.data_feed,
        )

        portfolio = PortfolioState()
        portfolio.reconcile(trading_client)

        order_manager = OrderManager(
            trading_client=trading_client,
            portfolio_state=portfolio,
            dry_run=self._settings.trading.dry_run,
        )
        order_manager.reconcile()

        sizer = TurtleUnitSizer(
            risk_per_trade_pct=self._settings.risk.risk_per_trade_pct,
            atr_stop_mult=self._settings.risk.atr_stop_mult,
            max_position_pct=self._settings.risk.max_position_pct,
            max_gross_exposure_pct=self._settings.risk.max_gross_exposure_pct,
        )
        killswitch = KillSwitch(
            daily_loss_limit_pct=self._settings.risk.daily_loss_limit_pct,
            max_drawdown_pct=self._settings.risk.max_drawdown_pct,
        )
        risk_manager = RiskManager(self._settings.risk, sizer, killswitch)

        strategy = StrategyRegistry.get(
            self._settings.strategy.name, params=self._settings.strategy.params,
        )
        logger.info("Strategy: %s with params %s", strategy.name, strategy.parameters)

        symbols = self._resolve_symbols()
        return SimpleNamespace(
            factory=factory, trading_client=trading_client, market_data=market_data,
            portfolio=portfolio, order_manager=order_manager, killswitch=killswitch,
            risk_manager=risk_manager, strategy=strategy, symbols=symbols,
        )

    def _apply_startup_stops(self, c: SimpleNamespace) -> None:
        """Guarantee open positions stay protected, in both run modes: recover any
        missing protective stop, then hand positions that dropped out of the universe
        to a trailing stop."""
        from trendchimp.runner.recovery import (
            convert_orphans_to_trailing_stops,
            recover_protective_stops,
        )
        recover_protective_stops(
            c.order_manager, c.portfolio, c.market_data, self._settings,
            dry_run=self._settings.trading.dry_run,
        )
        convert_orphans_to_trailing_stops(
            c.order_manager, c.portfolio, set(c.symbols), c.market_data, self._settings,
            dry_run=self._settings.trading.dry_run,
        )

    def _resolve_symbols(self) -> list[str]:
        """Trade the AI-screened universe file if configured and present; else the
        hand-configured symbol list."""
        universe_file = self._settings.trading.universe_file
        if universe_file and os.path.exists(universe_file):
            try:
                from trendchimp.screener.writer import load_universe_symbols

                symbols = load_universe_symbols(universe_file)
                if symbols:
                    logger.info("Loaded %d symbols from universe file %s",
                                len(symbols), universe_file)
                    return symbols
                logger.warning("Universe file %s has no symbols — using configured symbols",
                               universe_file)
            except Exception:
                logger.exception("Failed to read universe file %s — using configured symbols",
                                 universe_file)
        elif universe_file:
            logger.warning("Universe file %s not found — using configured symbols", universe_file)
        return [s.upper() for s in self._settings.trading.symbols]

    async def _warmup(self, strategy, market_data, portfolio, symbols) -> None:
        """Replay recent history through the strategy to prime channels/ATR, and
        seed directional state from any existing position so a restart doesn't
        double-enter."""
        needed = strategy.get_required_history()
        if needed <= 0:
            return
        # Generous lookback: daily bars need calendar days >> trading days.
        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(days=needed * 3 + 30)
        # Replaying history fires the strategy's per-signal INFO logs even though
        # we discard the signals here; mute them so warmup doesn't look like a
        # flurry of real trades.
        strat_logger = logging.getLogger("trendchimp.strategies")
        prev_level = strat_logger.level
        strat_logger.setLevel(logging.WARNING)
        try:
            for symbol in symbols:
                try:
                    bars = market_data.get_bars(symbol, self._settings.trading.timeframe, start, end)
                except Exception:
                    logger.exception("Warmup fetch failed for %s — continuing", symbol)
                    continue
                for bar in bars[:-1] if bars else []:
                    # Prime indicators without acting on signals.
                    await strategy.on_bar(bar)
                position = portfolio.get_position(symbol)
                strategy.seed_position(symbol, float(position.qty) if position else 0.0)
                logger.info("Warmed up %s with %d bars (pos=%s)",
                            symbol, max(0, len(bars) - 1) if bars else 0,
                            position.qty if position else 0)
        finally:
            strat_logger.setLevel(prev_level)
