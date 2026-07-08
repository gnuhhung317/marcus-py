from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from quant_signal_sdk.cli import export_backtest_results
from quant_signal_sdk.models import MarketType, OrderType, SignalAction, SignalPayload
from quant_signal_sdk.runtime.backtest import BacktestConfig, BacktestMetrics, PortfolioBacktestRunner
from quant_signal_sdk.runtime.interfaces import BaseFeed, BaseStrategy, MarketEvent, PortfolioContext


@dataclass
class ListFeed(BaseFeed):
    events: list[MarketEvent]

    def stream(self):
        yield from self.events


class OneShotStrategy(BaseStrategy):
    def __init__(self, signal: SignalPayload) -> None:
        self._signal = signal
        self._emitted = False

    def on_event(self, event: MarketEvent, context: PortfolioContext) -> list[SignalPayload]:
        if self._emitted:
            return []
        self._emitted = True
        return [self._signal]


def _event(timestamp: datetime, open_: float, high: float, low: float, close: float) -> MarketEvent:
    return MarketEvent(timestamp=timestamp, payload={"open": open_, "high": high, "low": low, "close": close, "volume": 100.0})


def test_export_backtest_results_writes_expected_files(tmp_path) -> None:
    ts1 = datetime(2026, 5, 28, 9, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 28, 9, 15, tzinfo=timezone.utc)
    feed = ListFeed([
        _event(ts1, 100, 101, 99, 100),
        _event(ts2, 102, 103, 101, 102),
    ])
    signal = SignalPayload(
        action=SignalAction.OPEN_LONG,
        symbol="BTCUSDT",
        market_type=MarketType.SPOT,
        order_type=OrderType.MARKET,
        entry=100,
        amount=1,
        generated_timestamp=ts1,
    )
    report = PortfolioBacktestRunner(feed=feed, strategy=OneShotStrategy(signal), config=BacktestConfig(initial_cash=1000.0)).run()

    export_backtest_results(report, output_dir=str(tmp_path), export_html=True)

    assert (tmp_path / "trades.csv").exists()
    assert (tmp_path / "orders.csv").exists()
    assert (tmp_path / "equity_curve.csv").exists()
    assert (tmp_path / "closed_trades.csv").exists()
    assert (tmp_path / "metrics.json").exists()
    assert (tmp_path / "tearsheet.html").exists()

    metrics = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert "max_drawdown" in metrics
    assert "profit_factor" in metrics
    assert "clamped_orders" in metrics
    assert metrics["final_equity"] >= 0
    assert BacktestMetrics(**metrics).clamped_orders == metrics["clamped_orders"]
