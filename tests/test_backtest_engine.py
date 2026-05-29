from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import logging

from quant_signal_sdk.models import ExecutionPolicies, MarketType, OrderType, SignalAction, SignalPayload
from quant_signal_sdk.runtime.backtest import BacktestConfig, PortfolioBacktestRunner
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


def _event(timestamp: datetime, open_: float, high: float, low: float, close: float) -> MarketEvent:
    return MarketEvent(
        timestamp=timestamp,
        payload={"open": open_, "high": high, "low": low, "close": close, "volume": 100.0},
    )


def _signal(*, action: SignalAction, order_type: OrderType, entry: float, amount: float, timestamp: datetime, policies: ExecutionPolicies | None = None) -> SignalPayload:
    return SignalPayload(
        action=action,
        symbol="BTCUSDT",
        market_type=MarketType.SPOT,
        order_type=order_type,
        entry=entry,
        amount=amount,
        generated_timestamp=timestamp,
        policies=policies,
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


def test_timeout_sweep_cancels_before_matching() -> None:
    ts1 = datetime(2026, 5, 28, 11, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 28, 11, 15, tzinfo=timezone.utc)
    policies = ExecutionPolicies(cancel_order_after=int((ts1 + timedelta(minutes=10)).timestamp()))
    feed = ListFeed([
        _event(ts1, 100, 100, 100, 100),
        _event(ts2, 100, 110, 90, 105),
    ])
    strategy = OneShotOrderStrategy(
        _signal(action=SignalAction.OPEN_LONG, order_type=OrderType.LIMIT, entry=95, amount=1, timestamp=ts1, policies=policies)
    )

    report = PortfolioBacktestRunner(feed=feed, strategy=strategy, config=BacktestConfig(initial_cash=1000.0)).run()

    assert report.fills == []
    assert report.context.positions == {}


def test_max_size_percent_rejects_signal_immediately(caplog) -> None:
    ts1 = datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc)
    feed = ListFeed([_event(ts1, 100, 100, 100, 100)])
    policies = ExecutionPolicies(max_size_percent=0.1)
    strategy = OneShotOrderStrategy(
        _signal(action=SignalAction.OPEN_LONG, order_type=OrderType.MARKET, entry=500, amount=1, timestamp=ts1, policies=policies)
    )

    with caplog.at_level(logging.WARNING):
        report = PortfolioBacktestRunner(feed=feed, strategy=strategy, config=BacktestConfig(initial_cash=1000.0)).run()

    assert report.fills == []
    assert report.context.positions == {}
    assert any("max_size_percent" in message for message in caplog.messages)


def test_cash_and_position_state_stays_consistent_on_roundtrip() -> None:
    ts1 = datetime(2026, 5, 28, 13, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 28, 13, 15, tzinfo=timezone.utc)
    ts3 = datetime(2026, 5, 28, 13, 30, tzinfo=timezone.utc)
    open_signal = _signal(action=SignalAction.OPEN_LONG, order_type=OrderType.MARKET, entry=100, amount=1, timestamp=ts1)
    close_signal = _signal(action=SignalAction.CLOSE_LONG, order_type=OrderType.MARKET, entry=110, amount=1, timestamp=ts2)
    feed = ListFeed([
        _event(ts1, 100, 100, 100, 100),
        _event(ts2, 100, 110, 100, 110),
        _event(ts3, 110, 110, 110, 110),
    ])
    strategy = OpenThenCloseStrategy(open_signal=open_signal, close_signal=close_signal)

    report = PortfolioBacktestRunner(feed=feed, strategy=strategy, config=BacktestConfig(initial_cash=1000.0)).run()

    assert report.context.positions == {}
    assert report.context.cash == 1010.0
    assert report.context.realized_pnl == 10.0
    assert report.metrics is not None
    assert report.metrics.total_trades == 1
    assert report.metrics.win_rate == 1.0
    assert report.metrics.profit_factor == float("inf")