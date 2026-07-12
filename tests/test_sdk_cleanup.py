from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from quant_signal_sdk import BaseStrategy, MarketType, OrderType, QuantSignalClient, SignalAction, SignalPayload
from quant_signal_sdk.runtime.backtest import BacktestMetrics, BacktestReport, EquityPoint
from quant_signal_sdk.runtime.backtest_upload import BacktestUploadClient, BacktestUploadConfig
from quant_signal_sdk.runtime.dry_run import DryRunSyncClient, DryRunSyncConfig
from quant_signal_sdk.runtime.dry_run_store import DryRunPortfolioSnapshot, DryRunStateSnapshot
from quant_signal_sdk.runtime.interfaces import BaseDispatcher, BaseFeed, MarketEvent, PortfolioContext
from quant_signal_sdk.runtime.runner import Runner
from quant_signal_sdk.runtime.sync import create_dry_run_syncer
from quant_signal_sdk.runtime.telemetry import TelemetryClient, TelemetryConfig


ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(self, payload: dict[str, Any] | None = None, *, status_code: int = 200, content: bytes | None = None) -> None:
        self._payload = payload or {"ok": True}
        self.status_code = status_code
        self.content = content if content is not None else b'{"ok":true}'

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        kwargs["method"] = "GET"
        kwargs["url"] = url
        self.calls.append(kwargs)
        return FakeResponse(status_code=204, content=b"")

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        kwargs["method"] = "POST"
        kwargs["url"] = url
        self.calls.append(kwargs)
        return FakeResponse()


class DummyNetworkClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        json_body: dict[str, Any],
        timeout_seconds: float,
    ) -> FakeResponse:
        self.calls.append({"url": url, "headers": headers, "json_body": json_body, "timeout_seconds": timeout_seconds})
        return FakeResponse()

    def post_bytes(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> FakeResponse:
        import json
        json_body = json.loads(body.decode("utf-8")) if body else {}
        self.calls.append({
            "url": url,
            "headers": headers,
            "json": json_body,
            "json_body": json_body,
            "timeout": timeout_seconds,
            "timeout_seconds": timeout_seconds,
            "body": body
        })
        return FakeResponse()


def test_package_root_exposes_runtime_strategy_contract() -> None:
    assert hasattr(BaseStrategy, "on_event")
    assert not hasattr(BaseStrategy, "on_market_data")


def test_core_import_does_not_require_heavy_optional_dependencies() -> None:
    code = """
import importlib.abc
import sys

class BlockHeavy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'pandas', 'numpy', 'ccxt'}:
            raise ImportError(f'blocked optional dependency: {fullname}')
        return None

sys.meta_path.insert(0, BlockHeavy())
import quant_signal_sdk
assert hasattr(quant_signal_sdk, 'QuantSignalClient')
assert hasattr(quant_signal_sdk, 'BaseStrategy')
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_readme_quickstart_payload_validates() -> None:
    signal = SignalPayload(
        action=SignalAction.OPEN_LONG,
        symbol="BTCUSDT",
        market_type=MarketType.SPOT,
        order_type=OrderType.MARKET,
        entry=70000,
        amount=0.01,
        stop_loss=68500,
        take_profit=72000,
        metadata={"strategy": "trend_v1", "confidence_score": 0.84},
    )

    assert signal.symbol == "BTCUSDT"
    assert signal.market_type == MarketType.SPOT
    assert signal.order_type == OrderType.MARKET


class OneEventFeed(BaseFeed):
    def stream(self):
        yield MarketEvent(timestamp=datetime(2026, 1, 1, 0, 0, 0), payload={})


class OpenOnlyStrategy(BaseStrategy):
    def on_event(self, event: MarketEvent, context: PortfolioContext) -> list[SignalPayload]:
        return [
            SignalPayload(
                signal_id="sig_open",
                bot_id="bot_1",
                action=SignalAction.OPEN_LONG,
                symbol="BTCUSDT",
                market_type=MarketType.SPOT,
                order_type=OrderType.MARKET,
                entry=65000,
                amount=0.1,
                generated_timestamp=event.timestamp,
            )
        ]


class NoopDispatcher(BaseDispatcher):
    def dispatch(self, signal: SignalPayload) -> None:
        return None


def test_dry_run_factory_persists_open_position(tmp_path: Path) -> None:
    session = FakeSession()
    dry_run = create_dry_run_syncer(
        DryRunSyncConfig(
            base_url="http://api",
            bot_id="bot_1",
            api_key="ak_1",
            signer_secret="secret",
            sqlite_path=str(tmp_path / "dry_run.sqlite3"),
            sync_interval_seconds=0,
        ),
        session=session,
    )
    runner = Runner(
        OneEventFeed(),
        OpenOnlyStrategy(),
        NoopDispatcher(),
        after_signal_applied=dry_run.after_signal_applied,
        state_syncer=dry_run.state_syncer,
    )

    runner.run()

    state = dry_run.tracker.store.load_state()
    assert state is not None
    assert len(state.positions) == 1
    assert state.positions[0].position_id == "SPOT:BTCUSDT"


def test_endpoint_clients_share_auth_header_shape() -> None:
    signal_network = DummyNetworkClient()
    signal_client = QuantSignalClient(
        base_url="http://api",
        api_key="ak_1",
        signer_secret="secret",
        network_client=signal_network,
    )
    signal_client.send_payload({"x": 1})

    telemetry_session = FakeSession()
    telemetry_client = TelemetryClient(
        TelemetryConfig(base_url="http://api", bot_id="bot_1", api_key="ak_1", signer_secret="secret"),
        session=telemetry_session,  # type: ignore[arg-type]
    )
    telemetry_client.push_telemetry(equity=1.0)

    dry_run_session = FakeSession()
    dry_run_client = DryRunSyncClient(
        DryRunSyncConfig(base_url="http://api", bot_id="bot_1", api_key="ak_1", signer_secret="secret"),
        session=dry_run_session,  # type: ignore[arg-type]
    )
    dry_run_client.push_snapshot(
        DryRunStateSnapshot(
            portfolio=DryRunPortfolioSnapshot(
                timestamp=datetime(2026, 1, 1, 0, 0, 0),
                cash=1.0,
                equity=1.0,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                total_fees=0.0,
            ),
            positions=[],
            closed_trades=[],
        )
    )

    backtest_session = FakeSession()
    backtest_client = BacktestUploadClient(
        BacktestUploadConfig(base_url="http://api", bot_id="bot_1", api_key="ak_1", signer_secret="secret"),
        session=backtest_session,  # type: ignore[arg-type]
    )
    timestamp = datetime(2026, 1, 1, 0, 0, 0)
    backtest_client.push_backtest_report(
        BacktestReport(
            context=PortfolioContext(),
            equity_history=[EquityPoint(timestamp, 1.0, 0.0, 0.0, 0.0, 1.0)],
            metrics=BacktestMetrics(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0, 0.0, 0.0, 1.0),
        )
    )

    headers = [
        signal_network.calls[0]["headers"],
        telemetry_session.calls[0]["headers"],
        dry_run_session.calls[0]["headers"],
        backtest_session.calls[0]["headers"],
    ]
    for header in headers:
        assert header["Content-Type"] == "application/json"
        assert header["X-Bot-Api-Key"] == "ak_1"
        assert "X-Timestamp" in header
        assert "X-Signature" in header
    assert backtest_session.calls[0]["headers"]["Content-Encoding"] == "gzip"
