from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trendchimp.config.settings import TrendChimpSettings

logger = logging.getLogger(__name__)


class TradingBot:
    """Top-level orchestrator: wires feed -> strategy -> risk -> execution."""

    def __init__(self, settings: "TrendChimpSettings") -> None:
        self._settings = settings
        self._running = False

    def start(self) -> None:
        asyncio.run(self._run())

    async def _run(self) -> None:
        from trendchimp.clients.factory import ClientFactory
        from trendchimp.clients.market_data import MarketDataClient
        from trendchimp.clients.trading import TradingClientWrapper
        from trendchimp.data.feed import DataType, MarketDataFeed
        from trendchimp.data.stream_manager import StreamManager
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
        stock_stream = factory.make_stock_stream()
        trading_stream = factory.make_trading_stream()

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
        await self._warmup(strategy, market_data, portfolio, symbols)

        # Guarantee every open position has a live protective stop (heals the
        # crash/disconnect window where an entry filled but its stop was never placed).
        from trendchimp.runner.recovery import recover_protective_stops
        recover_protective_stops(
            order_manager, portfolio, market_data, self._settings,
            dry_run=self._settings.trading.dry_run,
        )

        feed = MarketDataFeed()
        stop_event = asyncio.Event()

        # The live websocket only emits 1-minute bars; roll them up into daily
        # bars so the Turtle strategy runs once per completed session.
        from trendchimp.data.aggregator import DailyBarAggregator
        aggregator = DailyBarAggregator()

        async def _on_bar(bar):
            # Every intraday bar refreshes the mark so the kill-switch and
            # protective stops see live prices; the strategy itself only acts on
            # completed daily bars emitted by the aggregator.
            portfolio.update_mark(bar.symbol, bar.close)
            if killswitch.check(portfolio):
                logger.error("Kill-switch tripped (%s) — initiating shutdown",
                             killswitch.reason)
                stop_event.set()
                return
            daily = aggregator.add(bar)
            if daily is None:
                return
            for sig in await strategy.on_bar(daily):
                decision = risk_manager.evaluate(sig, portfolio)
                if decision:
                    order_manager.submit(decision)

        for symbol in symbols:
            feed.subscribe(symbol, DataType.BAR, _on_bar)

        stream_manager = StreamManager(
            feed=feed, settings=self._settings,
            stock_stream=stock_stream, trading_stream=trading_stream,
            symbols=symbols,
        )
        stream_manager.add_trade_update_handler(order_manager.handle_trade_update)

        loop = asyncio.get_running_loop()

        def _signal_handler():
            logger.info("Shutdown signal received")
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

        self._running = True
        await stream_manager.start()
        logger.info("Streams connected. Watching %s", symbols)

        try:
            await stop_event.wait()
        finally:
            logger.info("Shutting down...")
            if killswitch.tripped and not self._settings.trading.dry_run:
                # Cancel pending entry orders but leave protective stops in place.
                order_manager.cancel_all()
            await stream_manager.stop()
            self._running = False
            logger.info("trendchimp stopped")

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
