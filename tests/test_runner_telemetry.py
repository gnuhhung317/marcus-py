from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_signal_sdk.models import MarketType, OrderType, SignalAction, SignalPayload
from quant_signal_sdk.runtime.interfaces import BaseDispatcher, BaseFeed, BaseStrategy, MarketEvent, PortfolioContext
from quant_signal_sdk.runtime.runner import Runner
from quant_signal_sdk.runtime.telemetry import TelemetryConfig


class OneEventFeed(BaseFeed):
    def stream(self):
        yield MarketEvent(timestamp=datetime(2026, 1, 1, 0, 0, 0), payload={})


class OneSignalStrategy(BaseStrategy):
    def on_event(self, event: MarketEvent, context: PortfolioContext) -> list[SignalPayload]:
        return [
            SignalPayload(
                bot_id="bot_1",
                action=SignalAction.OPEN_LONG,
                symbol="BTCUSDT",
                market_type=MarketType.SPOT,
                order_type=OrderType.MARKET,
            )
        ]


class NoopDispatcher(BaseDispatcher):
    def dispatch(self, signal: SignalPayload) -> None:
        return None


@dataclass
class FakeTelemetryClient:
    latest: dict | None = None
    fail_push: bool = False

    def __post_init__(self) -> None:
        self.pushed: list[PortfolioContext] = []

    def fetch_latest(self):
        return self.latest

    def push_context(self, context: PortfolioContext):
        if self.fail_push:
            raise RuntimeError("network down")
        self.pushed.append(context)
        return {"ok": True}


def test_runner_recovers_context_from_latest_telemetry():
    telemetry = FakeTelemetryClient(
        latest={
            "timestamp": "2026-01-01T00:00:00",
            "equity": 1234.5,
            "realizedPnl": 12.5,
            "unrealizedPnl": -3.0,
        }
    )
    runner = Runner(
        OneEventFeed(),
        OneSignalStrategy(),
        NoopDispatcher(),
        telemetry_config=TelemetryConfig(base_url="http://api", bot_id="bot_1", api_key="key", sync_interval_seconds=0),
        telemetry_client=telemetry,  # type: ignore[arg-type]
    )

    context = runner.run()

    assert context.equity == 1234.5
    assert context.realized_pnl == 12.5
    assert context.unrealized_pnl == -3.0
    assert len(telemetry.pushed) >= 1


def test_runner_does_not_recover_over_explicit_initial_context():
    telemetry = FakeTelemetryClient(latest={"equity": 9999, "realizedPnl": 1, "unrealizedPnl": 1})
    runner = Runner(
        OneEventFeed(),
        OneSignalStrategy(),
        NoopDispatcher(),
        initial_context=PortfolioContext(equity=100.0),
        telemetry_config=TelemetryConfig(base_url="http://api", bot_id="bot_1", api_key="key", sync_interval_seconds=0),
        telemetry_client=telemetry,  # type: ignore[arg-type]
    )

    assert runner.run().equity == 100.0


def test_runner_logs_and_continues_when_telemetry_push_fails():
    telemetry = FakeTelemetryClient(fail_push=True)
    runner = Runner(
        OneEventFeed(),
        OneSignalStrategy(),
        NoopDispatcher(),
        telemetry_config=TelemetryConfig(base_url="http://api", bot_id="bot_1", api_key="key", sync_interval_seconds=0),
        telemetry_client=telemetry,  # type: ignore[arg-type]
    )

    context = runner.run()

    assert "SPOT:BTCUSDT" in context.positions
