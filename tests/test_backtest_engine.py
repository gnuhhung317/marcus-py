from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import math

from quant_signal_sdk.models import ExecutionPolicies, MarketType, OrderType, SignalAction, SignalPayload
from quant_signal_sdk.runtime.backtest import BacktestConfig, PortfolioBacktestRunner, PositionLot
from quant_signal_sdk.runtime.interfaces import BaseFeed, BaseStrategy, MarketEvent, PortfolioContext


@dataclass
class ListFeed(BaseFeed):
    events: list[MarketEvent]

    def stream(self):
        yield from self.events


class OneShotOrderStrategy(BaseStrategy):
    def __init__(self, signal: SignalPayload) -> None:
        self._signal = signal
        self._emitted = False

    def on_event(self, event: MarketEvent, context: PortfolioContext) -> list[SignalPayload]:
        if self._emitted:
            return []
        self._emitted = True
        return [self._signal]


class OpenThenCloseStrategy(BaseStrategy):
    def __init__(self, open_signal: SignalPayload, close_signal: SignalPayload) -> None:
        self._open_signal = open_signal
        self._close_signal = close_signal
        self._opened = False

    def on_event(self, event: MarketEvent, context: PortfolioContext) -> list[SignalPayload]:
        if not self._opened:
            self._opened = True
            return [self._open_signal]
        if context.positions:
            return [self._close_signal]
        return []


class SymbolAwareStrategy(BaseStrategy):
    def __init__(self, plans: dict[str, list[SignalPayload]]) -> None:
        self._plans = {key: list(value) for key, value in plans.items()}

    def on_event(self, event: MarketEvent, context: PortfolioContext) -> list[SignalPayload]:
        symbol = str(event.payload.get("symbol") or "")
        queue = self._plans.get(symbol)
        if not queue:
            return []
        return [queue.pop(0)]


class SequenceStrategy(BaseStrategy):
    def __init__(self, responses: list[list[SignalPayload]]) -> None:
        self._responses = responses
        self._index = 0

    def on_event(self, event: MarketEvent, context: PortfolioContext) -> list[SignalPayload]:
        if self._index >= len(self._responses):
            return []
        response = self._responses[self._index]
        self._index += 1
        return response


def _event(timestamp: datetime, open_: float, high: float, low: float, close: float, *, symbol: str | None = None) -> MarketEvent:
    payload = {"open": open_, "high": high, "low": low, "close": close, "volume": 100.0}
    if symbol is not None:
        payload["symbol"] = symbol
    return MarketEvent(timestamp=timestamp, payload=payload)


def _pair_event(
    timestamp: datetime,
    *,
    spot_symbol: str,
    spot_ohlc: tuple[float, float, float, float],
    futures_symbol: str,
    futures_ohlc: tuple[float, float, float, float],
) -> MarketEvent:
    return MarketEvent(
        timestamp=timestamp,
        payload={
            "spot_symbol": spot_symbol,
            "spot_open": spot_ohlc[0],
            "spot_high": spot_ohlc[1],
            "spot_low": spot_ohlc[2],
            "spot_close": spot_ohlc[3],
            "futures_symbol": futures_symbol,
            "futures_open": futures_ohlc[0],
            "futures_high": futures_ohlc[1],
            "futures_low": futures_ohlc[2],
            "futures_close": futures_ohlc[3],
            "funding_rate": 0.0,
        },
    )


def _signal(
    *,
    action: SignalAction,
    order_type: OrderType,
    entry: float,
    amount: float,
    timestamp: datetime,
    symbol: str = "BTCUSDT",
    market_type: MarketType = MarketType.SPOT,
    policies: ExecutionPolicies | None = None,
    timeframe: str | None = None,
) -> SignalPayload:
    return SignalPayload(
        action=action,
        symbol=symbol,
        market_type=market_type,
        order_type=order_type,
        entry=entry,
        amount=amount,
        generated_timestamp=timestamp,
        policies=policies,
        timeframe=timeframe,
    )


def test_market_order_executes_on_next_open_only() -> None:
    ts1 = datetime(2026, 5, 28, 9, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 28, 9, 15, tzinfo=timezone.utc)
    feed = ListFeed([
        _event(ts1, 100, 101, 99, 100),
        _event(ts2, 102, 103, 101, 102),
    ])
    strategy = OneShotOrderStrategy(
        _signal(action=SignalAction.OPEN_LONG, order_type=OrderType.MARKET, entry=100, amount=1, timestamp=ts1)
    )

    report = PortfolioBacktestRunner(feed=feed, strategy=strategy, config=BacktestConfig(initial_cash=1000.0)).run()

    assert report.fills[0].price == 102
    assert report.fills[0].fee_type == "TAKER"
    assert report.context.positions["SPOT:BTCUSDT"]["average_entry_price"] == 102


def test_limit_order_fills_on_wick_touch() -> None:
    ts1 = datetime(2026, 5, 28, 10, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 28, 10, 15, tzinfo=timezone.utc)
    feed = ListFeed([
        _event(ts1, 100, 100, 100, 100),
        _event(ts2, 100, 110, 90, 105),
    ])
    strategy = OneShotOrderStrategy(
        _signal(action=SignalAction.OPEN_LONG, order_type=OrderType.LIMIT, entry=95, amount=1, timestamp=ts1)
    )

    report = PortfolioBacktestRunner(feed=feed, strategy=strategy, config=BacktestConfig(initial_cash=1000.0)).run()

    assert report.fills[0].price == 95
    assert report.fills[0].fee_type == "MAKER"
    assert report.context.positions["SPOT:BTCUSDT"]["quantity"] == 1


def test_interleaved_multi_symbol_events_do_not_cross_fill_or_reprice() -> None:
    ts1 = datetime(2026, 5, 28, 9, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 28, 9, 15, tzinfo=timezone.utc)
    ts3 = datetime(2026, 5, 28, 9, 30, tzinfo=timezone.utc)
    ts4 = datetime(2026, 5, 28, 9, 45, tzinfo=timezone.utc)
    ts5 = datetime(2026, 5, 28, 10, 0, tzinfo=timezone.utc)
    feed = ListFeed(
        [
            _event(ts1, 100, 101, 99, 100, symbol="BTCUSDT"),
            _event(ts2, 200, 201, 199, 200, symbol="ETHUSDT"),
            _event(ts3, 105, 106, 104, 105, symbol="BTCUSDT"),
            _event(ts4, 210, 211, 209, 210, symbol="ETHUSDT"),
            _event(ts5, 110, 111, 109, 110, symbol="BTCUSDT"),
        ]
    )
    strategy = SymbolAwareStrategy(
        {
            "BTCUSDT": [
                _signal(action=SignalAction.OPEN_LONG, order_type=OrderType.MARKET, entry=100, amount=1, timestamp=ts1),
            ]
        }
    )

    report = PortfolioBacktestRunner(feed=feed, strategy=strategy, config=BacktestConfig(initial_cash=1000.0)).run()

    assert len(report.fills) == 1
    assert report.fills[0].symbol == "BTCUSDT"
    assert report.fills[0].price == 105
    assert report.equity_history[1].equity == 1000.0
    assert report.equity_history[3].equity == report.equity_history[2].equity
    assert report.equity_history[4].equity == 1005.0


def test_open_long_equity_includes_marked_position_value() -> None:
    ts1 = datetime(2026, 5, 28, 10, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 28, 10, 15, tzinfo=timezone.utc)
    ts3 = datetime(2026, 5, 28, 10, 30, tzinfo=timezone.utc)
    feed = ListFeed(
        [
            _event(ts1, 100, 100, 100, 100, symbol="BTCUSDT"),
            _event(ts2, 105, 105, 105, 105, symbol="BTCUSDT"),
            _event(ts3, 110, 110, 110, 110, symbol="BTCUSDT"),
        ]
    )
    strategy = OneShotOrderStrategy(
        _signal(action=SignalAction.OPEN_LONG, order_type=OrderType.MARKET, entry=100, amount=1, timestamp=ts1)
    )

    report = PortfolioBacktestRunner(feed=feed, strategy=strategy, config=BacktestConfig(initial_cash=1000.0)).run()

    assert report.fills[0].price == 105
    assert report.equity_history[1].equity == 1000.0
    assert report.equity_history[2].equity == 1005.0
    assert report.context.unrealized_pnl == 5.0


def test_open_short_equity_subtracts_marked_position_liability() -> None:
    ts1 = datetime(2026, 5, 28, 10, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 28, 10, 15, tzinfo=timezone.utc)
    ts3 = datetime(2026, 5, 28, 10, 30, tzinfo=timezone.utc)
    feed = ListFeed(
        [
            _event(ts1, 100, 100, 100, 100, symbol="BTCUSDT"),
            _event(ts2, 100, 100, 100, 100, symbol="BTCUSDT"),
            _event(ts3, 90, 90, 90, 90, symbol="BTCUSDT"),
        ]
    )
    strategy = OneShotOrderStrategy(
        _signal(action=SignalAction.OPEN_SHORT, order_type=OrderType.MARKET, entry=100, amount=1, timestamp=ts1)
    )

    report = PortfolioBacktestRunner(feed=feed, strategy=strategy, config=BacktestConfig(initial_cash=1000.0)).run()

    assert report.fills[0].price == 100
    assert report.equity_history[1].equity == 1000.0
    assert report.equity_history[2].equity == 1010.0
    assert report.context.unrealized_pnl == 10.0


def test_composite_pair_snapshot_executes_each_leg_on_its_own_quote() -> None:
    ts1 = datetime(2026, 5, 28, 11, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 28, 11, 15, tzinfo=timezone.utc)
    feed = ListFeed(
        [
            _pair_event(ts1, spot_symbol="BTCUSDT", spot_ohlc=(100, 101, 99, 100), futures_symbol="ETHUSDT", futures_ohlc=(200, 201, 199, 200)),
            _pair_event(ts2, spot_symbol="BTCUSDT", spot_ohlc=(101, 102, 100, 101), futures_symbol="ETHUSDT", futures_ohlc=(205, 206, 204, 205)),
        ]
    )
    class PairStrategy(BaseStrategy):
        def __init__(self) -> None:
            self._done = False

        def on_event(self, event: MarketEvent, context: PortfolioContext) -> list[SignalPayload]:
            if self._done:
                return []
            self._done = True
            return [
                _signal(action=SignalAction.OPEN_LONG, order_type=OrderType.MARKET, entry=100, amount=1, timestamp=ts1, symbol="BTCUSDT"),
                _signal(
                    action=SignalAction.OPEN_SHORT,
                    order_type=OrderType.MARKET,
                    entry=200,
                    amount=1,
                    timestamp=ts1,
                    symbol="ETHUSDT",
                    market_type=MarketType.FUTURE,
                ),
            ]

    report = PortfolioBacktestRunner(feed=feed, strategy=PairStrategy(), config=BacktestConfig(initial_cash=1000.0)).run()

    fills = {(fill.market_type, fill.symbol): fill.price for fill in report.fills}
    assert fills[("SPOT", "BTCUSDT")] == 101
    assert fills[("FUTURE", "ETHUSDT")] == 205
    assert report.context.positions["SPOT:BTCUSDT"]["average_entry_price"] == 101
    assert report.context.positions["FUTURE:ETHUSDT"]["average_entry_price"] == 205


def test_partial_closes_prorate_fees_and_keep_realized_pnl_consistent() -> None:
    ts1 = datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 28, 12, 15, tzinfo=timezone.utc)
    ts3 = datetime(2026, 5, 28, 12, 30, tzinfo=timezone.utc)
    ts4 = datetime(2026, 5, 28, 12, 45, tzinfo=timezone.utc)
    feed = ListFeed(
        [
            _event(ts1, 100, 100, 100, 100, symbol="BTCUSDT"),
            _event(ts2, 100, 110, 100, 110, symbol="BTCUSDT"),
            _event(ts3, 110, 120, 110, 120, symbol="BTCUSDT"),
            _event(ts4, 120, 120, 120, 120, symbol="BTCUSDT"),
        ]
    )
    strategy = SequenceStrategy(
        [
            [_signal(action=SignalAction.OPEN_LONG, order_type=OrderType.MARKET, entry=100, amount=2, timestamp=ts1)],
            [_signal(action=SignalAction.CLOSE_LONG, order_type=OrderType.MARKET, entry=110, amount=1, timestamp=ts2)],
            [_signal(action=SignalAction.CLOSE_LONG, order_type=OrderType.MARKET, entry=120, amount=1, timestamp=ts3)],
            [],
        ]
    )

    report = PortfolioBacktestRunner(
        feed=feed,
        strategy=strategy,
        config=BacktestConfig(initial_cash=1000.0, maker_fee_rate=0.01, taker_fee_rate=0.01),
    ).run()

    closed_trade_pnl = sum(trade.pnl for trade in report.closed_trades)
    assert report.context.positions == {}
    assert math.isclose(report.context.realized_pnl, closed_trade_pnl)
    assert math.isclose(report.context.realized_pnl, 25.7)


def test_partial_close_replaces_fifo_lot_instead_of_mutating_in_place() -> None:
    ts1 = datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 28, 12, 15, tzinfo=timezone.utc)
    ts3 = datetime(2026, 5, 28, 12, 30, tzinfo=timezone.utc)
    strategy = SequenceStrategy(
        [
            [_signal(action=SignalAction.OPEN_LONG, order_type=OrderType.MARKET, entry=100, amount=2, timestamp=ts1)],
            [_signal(action=SignalAction.CLOSE_LONG, order_type=OrderType.MARKET, entry=110, amount=1, timestamp=ts2)],
            [],
        ]
    )
    runner = PortfolioBacktestRunner(
        feed=ListFeed(
            [
                _event(ts1, 100, 100, 100, 100, symbol="BTCUSDT"),
                _event(ts2, 100, 110, 100, 110, symbol="BTCUSDT"),
                _event(ts3, 110, 110, 110, 110, symbol="BTCUSDT"),
            ]
        ),
        strategy=strategy,
        config=BacktestConfig(initial_cash=1000.0),
    )

    runner._process_event(_event(ts1, 100, 100, 100, 100, symbol="BTCUSDT"))
    runner._process_event(_event(ts2, 100, 110, 100, 110, symbol="BTCUSDT"))
    first_lot = runner.context.positions["SPOT:BTCUSDT"]["lots"][0]
    runner._process_event(_event(ts3, 110, 110, 110, 110, symbol="BTCUSDT"))
    replacement_lot = runner.context.positions["SPOT:BTCUSDT"]["lots"][0]

    assert isinstance(first_lot, PositionLot)
    assert isinstance(replacement_lot, PositionLot)
    assert first_lot.quantity == 2.0
    assert replacement_lot.quantity == 1.0
    assert replacement_lot is not first_lot


def test_reversal_closes_then_opens_residual_quantity() -> None:
    ts1 = datetime(2026, 5, 28, 13, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 28, 13, 15, tzinfo=timezone.utc)
    ts3 = datetime(2026, 5, 28, 13, 30, tzinfo=timezone.utc)
    feed = ListFeed(
        [
            _event(ts1, 100, 100, 100, 100, symbol="BTCUSDT"),
            _event(ts2, 100, 100, 100, 100, symbol="BTCUSDT"),
            _event(ts3, 90, 90, 90, 90, symbol="BTCUSDT"),
        ]
    )
    strategy = SequenceStrategy(
        [
            [_signal(action=SignalAction.OPEN_LONG, order_type=OrderType.MARKET, entry=100, amount=1, timestamp=ts1)],
            [_signal(action=SignalAction.OPEN_SHORT, order_type=OrderType.MARKET, entry=90, amount=2, timestamp=ts2)],
            [],
        ]
    )

    report = PortfolioBacktestRunner(feed=feed, strategy=strategy, config=BacktestConfig(initial_cash=1000.0)).run()

    position = report.context.positions["SPOT:BTCUSDT"]
    assert position["side"] == "SHORT"
    assert position["net_quantity"] == -1.0
    assert report.context.realized_pnl == -10.0
    assert len(report.closed_trades) == 1
    assert report.closed_trades[0].quantity == 1.0


def test_max_size_percent_clamps_opening_quantity_and_tracks_metrics(caplog) -> None:
    ts1 = datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 28, 14, 15, tzinfo=timezone.utc)
    policies = ExecutionPolicies(max_size_percent=0.1)
    feed = ListFeed(
        [
            _event(ts1, 100, 100, 100, 100, symbol="BTCUSDT"),
            _event(ts2, 100, 100, 100, 100, symbol="BTCUSDT"),
        ]
    )
    strategy = OneShotOrderStrategy(
        _signal(action=SignalAction.OPEN_LONG, order_type=OrderType.MARKET, entry=100, amount=2, timestamp=ts1, policies=policies)
    )

    with caplog.at_level(logging.WARNING):
        report = PortfolioBacktestRunner(feed=feed, strategy=strategy, config=BacktestConfig(initial_cash=1000.0)).run()

    assert len(report.fills) == 1
    assert report.fills[0].quantity == 1.0
    assert report.orders[0].quantity == 1.0
    assert report.metrics is not None
    assert report.metrics.clamped_orders == 1
    assert any("clamping order due to max_size_percent" in message for message in caplog.messages)


def test_pure_close_still_executes_when_cash_is_zero() -> None:
    ts1 = datetime(2026, 5, 28, 15, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 28, 15, 15, tzinfo=timezone.utc)
    ts3 = datetime(2026, 5, 28, 15, 30, tzinfo=timezone.utc)
    policies = ExecutionPolicies(max_size_percent=1.0)
    feed = ListFeed(
        [
            _event(ts1, 100, 100, 100, 100, symbol="BTCUSDT"),
            _event(ts2, 100, 100, 100, 100, symbol="BTCUSDT"),
            _event(ts3, 110, 110, 110, 110, symbol="BTCUSDT"),
        ]
    )
    strategy = SequenceStrategy(
        [
            [_signal(action=SignalAction.OPEN_LONG, order_type=OrderType.MARKET, entry=100, amount=10, timestamp=ts1, policies=policies)],
            [_signal(action=SignalAction.CLOSE_LONG, order_type=OrderType.MARKET, entry=110, amount=10, timestamp=ts2, policies=policies)],
            [],
        ]
    )

    report = PortfolioBacktestRunner(feed=feed, strategy=strategy, config=BacktestConfig(initial_cash=1000.0)).run()

    assert report.context.positions == {}
    assert report.context.cash == 1100.0
    assert len(report.fills) == 2


def test_timeout_sweep_cancels_before_matching() -> None:
    ts1 = datetime(2026, 5, 28, 16, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 28, 16, 15, tzinfo=timezone.utc)
    policies = ExecutionPolicies(cancel_order_after=int((ts1 + timedelta(minutes=10)).timestamp()))
    feed = ListFeed(
        [
            _event(ts1, 100, 100, 100, 100, symbol="BTCUSDT"),
            _event(ts2, 100, 110, 90, 105, symbol="BTCUSDT"),
        ]
    )
    strategy = OneShotOrderStrategy(
        _signal(action=SignalAction.OPEN_LONG, order_type=OrderType.LIMIT, entry=95, amount=1, timestamp=ts1, policies=policies)
    )

    report = PortfolioBacktestRunner(feed=feed, strategy=strategy, config=BacktestConfig(initial_cash=1000.0)).run()

    assert report.fills == []
    assert report.context.positions == {}
    assert report.orders[0].status == "CANCELED"


def test_close_position_after_forces_close_and_cancels_same_symbol_orders(caplog) -> None:
    ts1 = datetime(2026, 5, 28, 17, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 28, 17, 30, tzinfo=timezone.utc)
    ts3 = datetime(2026, 5, 28, 18, 0, tzinfo=timezone.utc)
    ts4 = datetime(2026, 5, 28, 20, 0, tzinfo=timezone.utc)
    close_deadline = int((ts1 + timedelta(minutes=45)).timestamp())
    open_signal = _signal(
        action=SignalAction.OPEN_LONG,
        order_type=OrderType.MARKET,
        entry=100,
        amount=1,
        timestamp=ts1,
        policies=ExecutionPolicies(close_position_after=close_deadline),
        timeframe="1h",
    )
    hanging_limit = _signal(
        action=SignalAction.OPEN_LONG,
        order_type=OrderType.LIMIT,
        entry=80,
        amount=1,
        timestamp=ts2,
    )
    feed = ListFeed(
        [
            _event(ts1, 100, 100, 100, 100, symbol="BTCUSDT"),
            _event(ts2, 101, 101, 101, 101, symbol="BTCUSDT"),
            _event(ts3, 200, 200, 200, 200, symbol="ETHUSDT"),
            _event(ts4, 95, 95, 95, 95, symbol="BTCUSDT"),
        ]
    )
    strategy = SequenceStrategy([[open_signal], [hanging_limit], [], []])

    with caplog.at_level(logging.WARNING):
        report = PortfolioBacktestRunner(feed=feed, strategy=strategy, config=BacktestConfig(initial_cash=1000.0)).run()

    assert report.context.positions == {}
    assert len(report.fills) == 2
    assert report.fills[-1].action == SignalAction.CLOSE_LONG
    assert report.orders[1].status == "CANCELED"
    assert any("close_position_after executed with lag" in message for message in caplog.messages)


def test_quote_cadence_tracking_is_pruned_after_symbol_leaves_active_universe() -> None:
    ts1 = datetime(2026, 5, 28, 18, 30, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 28, 18, 45, tzinfo=timezone.utc)
    ts3 = datetime(2026, 5, 28, 19, 0, tzinfo=timezone.utc)
    strategy = SequenceStrategy(
        [
            [_signal(action=SignalAction.OPEN_LONG, order_type=OrderType.MARKET, entry=100, amount=1, timestamp=ts1)],
            [_signal(action=SignalAction.CLOSE_LONG, order_type=OrderType.MARKET, entry=101, amount=1, timestamp=ts2)],
            [],
        ]
    )
    runner = PortfolioBacktestRunner(
        feed=ListFeed([]),
        strategy=strategy,
        config=BacktestConfig(initial_cash=1000.0),
    )

    runner._process_event(_event(ts1, 100, 100, 100, 100, symbol="BTCUSDT"))
    runner._process_event(_event(ts2, 101, 101, 101, 101, symbol="BTCUSDT"))
    assert "SPOT:BTCUSDT" in runner._quote_cadence_seconds
    assert "SPOT:BTCUSDT" in runner._last_quote_timestamp
    runner._process_event(_event(ts3, 102, 102, 102, 102, symbol="BTCUSDT"))

    assert runner.context.positions == {}
    assert runner._quote_cadence_seconds == {}
    assert runner._last_quote_timestamp == {}


def test_cash_and_position_state_stays_consistent_on_roundtrip() -> None:
    ts1 = datetime(2026, 5, 28, 19, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 28, 19, 15, tzinfo=timezone.utc)
    ts3 = datetime(2026, 5, 28, 19, 30, tzinfo=timezone.utc)
    open_signal = _signal(action=SignalAction.OPEN_LONG, order_type=OrderType.MARKET, entry=100, amount=1, timestamp=ts1)
    close_signal = _signal(action=SignalAction.CLOSE_LONG, order_type=OrderType.MARKET, entry=110, amount=1, timestamp=ts2)
    feed = ListFeed(
        [
            _event(ts1, 100, 100, 100, 100, symbol="BTCUSDT"),
            _event(ts2, 100, 110, 100, 110, symbol="BTCUSDT"),
            _event(ts3, 110, 110, 110, 110, symbol="BTCUSDT"),
        ]
    )
    strategy = OpenThenCloseStrategy(open_signal=open_signal, close_signal=close_signal)

    report = PortfolioBacktestRunner(feed=feed, strategy=strategy, config=BacktestConfig(initial_cash=1000.0)).run()

    assert report.context.positions == {}
    assert report.context.cash == 1010.0
    assert report.context.realized_pnl == 10.0
    assert report.metrics is not None
    assert report.metrics.total_trades == 1
    assert report.metrics.win_rate == 1.0
    assert report.metrics.profit_factor == float("inf")
