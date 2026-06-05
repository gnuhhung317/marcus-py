from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_signal_sdk.models import MarketType, OrderType, SignalAction, SignalPayload
from quant_signal_sdk.runtime.dry_run_store import DryRunClosedTradeSnapshot, DryRunPortfolioSnapshot, DryRunPositionSnapshot, DryRunStateSnapshot, SQLiteDryRunStore
from quant_signal_sdk.runtime.interfaces import BaseDispatcher, BaseFeed, BaseStrategy, MarketEvent, PortfolioContext
from quant_signal_sdk.runtime.runner import Runner
from quant_signal_sdk.runtime.sync import DryRunStateTracker, HttpDryRunSyncer, HttpTelemetrySyncer, NoopTelemetrySyncer


class TwoEventFeed(BaseFeed):
    def stream(self):
        yield MarketEvent(timestamp=datetime(2026, 1, 1, 0, 0, 0), payload={})
        yield MarketEvent(timestamp=datetime(2026, 1, 1, 1, 0, 0), payload={})


class OpenThenCloseStrategy(BaseStrategy):
    def __init__(self) -> None:
        self._calls = 0

    def on_event(self, event: MarketEvent, context: PortfolioContext) -> list[SignalPayload]:
        self._calls += 1
        if self._calls == 1:
            return [
                SignalPayload(
                    signalId="sig_123",
                    botId="bot_1",
                    action=SignalAction.OPEN_LONG,
                    symbol="BTCUSDT",
                    marketType=MarketType.SPOT,
                    orderType=OrderType.MARKET,
                    entry=65000,
                    amount=0.1,
                    generatedTimestamp=datetime(2026, 1, 1, 0, 0, 0),
                )
            ]
        return [
            SignalPayload(
                signalId="sig_456",
                botId="bot_1",
                action=SignalAction.CLOSE_LONG,
                symbol="BTCUSDT",
                marketType=MarketType.SPOT,
                orderType=OrderType.MARKET,
                entry=66800,
                amount=0.1,
                generatedTimestamp=datetime(2026, 1, 1, 1, 0, 0),
            )
        ]


class NoopDispatcher(BaseDispatcher):
    def dispatch(self, signal: SignalPayload) -> None:
        return None


@dataclass
class FakeDryRunClient:
    latest: DryRunStateSnapshot | None = None
    fail_push: bool = False

    def __post_init__(self) -> None:
        self.pushed: list[DryRunStateSnapshot] = []
        self.sqlite_path = Path("test-runtime.sqlite3")

    def fetch_latest(self):
        return self.latest

    def push_snapshot(self, state: DryRunStateSnapshot):
        if self.fail_push:
            raise RuntimeError("network down")
        self.pushed.append(state)
        return {"ok": True}


def test_runner_recovers_context_from_backend_latest_when_local_store_empty(tmp_path):
    telemetry = FakeDryRunClient(
        latest=DryRunStateSnapshot(
            portfolio=DryRunPortfolioSnapshot(
                timestamp=datetime(2026, 1, 1, 0, 0, 0),
                cash=1000.0,
                equity=1234.5,
                realized_pnl=12.5,
                unrealized_pnl=-3.0,
                total_fees=1.5,
            ),
            positions=[
                DryRunPositionSnapshot(
                    position_id="SPOT:BTCUSDT",
                    symbol="BTCUSDT",
                    market_type="SPOT",
                    side="LONG",
                    quantity=0.1,
                    entry_price=65000.0,
                    current_price=65200.0,
                    unrealized_pnl=20.0,
                    opened_at=datetime(2025, 12, 31, 23, 0, 0),
                    source_signal_id="sig_prev",
                )
            ],
            closed_trades=[],
        )
    )
    telemetry.sqlite_path = tmp_path / "dry-run.sqlite3"
    tracker = DryRunStateTracker(SQLiteDryRunStore(telemetry.sqlite_path))
    runner = Runner(
        TwoEventFeed(),
        OpenThenCloseStrategy(),
        NoopDispatcher(),
        after_signal_applied=tracker.on_signal_applied,
        state_syncer=HttpDryRunSyncer(telemetry, tracker, interval=0),  # type: ignore[arg-type]
    )

    context = runner.run()

    assert context.equity == 1234.5
    assert context.realized_pnl == 12.5
    assert len(telemetry.pushed) >= 1


def test_runner_does_not_recover_over_explicit_initial_context(tmp_path):
    telemetry = FakeDryRunClient(
        latest=DryRunStateSnapshot(
            portfolio=DryRunPortfolioSnapshot(
                timestamp=datetime(2026, 1, 1, 0, 0, 0),
                cash=1000.0,
                equity=9999.0,
                realized_pnl=1.0,
                unrealized_pnl=1.0,
                total_fees=0.0,
            ),
            positions=[],
            closed_trades=[],
        )
    )
    telemetry.sqlite_path = tmp_path / "dry-run.sqlite3"
    tracker = DryRunStateTracker(SQLiteDryRunStore(telemetry.sqlite_path))
    runner = Runner(
        TwoEventFeed(),
        OpenThenCloseStrategy(),
        NoopDispatcher(),
        initial_context=PortfolioContext(equity=100.0),
        after_signal_applied=tracker.on_signal_applied,
        state_syncer=HttpDryRunSyncer(telemetry, tracker, interval=0),  # type: ignore[arg-type]
    )

    assert runner.run().equity == 100.0


def test_runner_creates_deterministic_closed_trade_and_persists_local_state(tmp_path):
    sqlite_path = tmp_path / "dry-run.sqlite3"
    telemetry = FakeDryRunClient()
    telemetry.sqlite_path = sqlite_path
    tracker = DryRunStateTracker(SQLiteDryRunStore(sqlite_path))
    runner = Runner(
        TwoEventFeed(),
        OpenThenCloseStrategy(),
        NoopDispatcher(),
        after_signal_applied=tracker.on_signal_applied,
        state_syncer=HttpDryRunSyncer(telemetry, tracker, interval=0),  # type: ignore[arg-type]
    )

    runner.run()

    store = SQLiteDryRunStore(sqlite_path)
    state = store.load_state()
    assert state is not None
    assert state.positions == []
    assert len(state.closed_trades) == 1
    assert state.closed_trades[0].trade_id == "trade_sig_123_sig_456"


def test_runner_logs_and_continues_when_sync_fails(tmp_path):
    sqlite_path = tmp_path / "dry-run.sqlite3"
    telemetry = FakeDryRunClient(fail_push=True)
    telemetry.sqlite_path = sqlite_path
    tracker = DryRunStateTracker(SQLiteDryRunStore(sqlite_path))
    runner = Runner(
        TwoEventFeed(),
        OpenThenCloseStrategy(),
        NoopDispatcher(),
        after_signal_applied=tracker.on_signal_applied,
        state_syncer=HttpDryRunSyncer(telemetry, tracker, interval=0),  # type: ignore[arg-type]
    )

    context = runner.run()

    assert context.positions == {}


class FakeTelemetryClient:
    def __init__(self) -> None:
        self.pushed: list[dict[str, Any]] = []

    def push_telemetry(
        self,
        equity: float,
        realized_pnl: float = 0.0,
        unrealized_pnl: float = 0.0,
        metrics: dict[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        self.pushed.append({
            "equity": equity,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "metrics": metrics or {},
            "timestamp": timestamp,
        })
        return {"status": "ok"}


def test_runner_triggers_telemetry_syncer_during_run() -> None:
    client = FakeTelemetryClient()
    telemetry_syncer = HttpTelemetrySyncer(client, interval=0.0)  # interval=0 means every report triggers push

    runner = Runner(
        TwoEventFeed(),
        OpenThenCloseStrategy(),
        NoopDispatcher(),
        initial_context=PortfolioContext(equity=10000.0, cash=10000.0, realized_pnl=50.0, unrealized_pnl=-10.0),
        telemetry_syncer=telemetry_syncer,  # type: ignore[arg-type]
    )

    runner.run()

    # Two events in stream:
    # Event 1: strategy returns Open long signal -> applied -> report called -> client receives push
    # Event 2: strategy returns Close long signal -> applied -> report called -> client receives push
    # End of run: report called with force=True -> client receives push
    assert len(client.pushed) == 3

    # Verify values pushed in final force push
    final_push = client.pushed[-1]
    assert final_push["equity"] == 10000.0
    assert final_push["realized_pnl"] == 50.0
    assert final_push["unrealized_pnl"] == -10.0


def test_http_telemetry_syncer_throttles_reports() -> None:
    client = FakeTelemetryClient()
    # High interval means report will only push once on the first call (or when forced)
    telemetry_syncer = HttpTelemetrySyncer(client, interval=3600.0)

    runner = Runner(
        TwoEventFeed(),
        OpenThenCloseStrategy(),
        NoopDispatcher(),
        initial_context=PortfolioContext(equity=10000.0),
        telemetry_syncer=telemetry_syncer,  # type: ignore[arg-type]
    )

    runner.run()

    # Event 1: report called -> time since last report > interval -> pushed (1)
    # Event 2: report called -> throttled -> not pushed
    # End of run: report called with force=True -> pushed (2)
    assert len(client.pushed) == 2

