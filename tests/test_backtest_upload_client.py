from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from quant_signal_sdk.runtime.backtest import BacktestMetrics, BacktestReport, ClosedTrade, EquityPoint
from quant_signal_sdk.runtime.backtest_upload import BacktestUploadClient, BacktestUploadConfig
from quant_signal_sdk.runtime.interfaces import PortfolioContext


class FakeResponse:
    content = b'{"runId":"bt_1"}'

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return {"runId": "bt_1"}


@dataclass
class FakeSession:
    def __post_init__(self) -> None:
        self.calls = []

    def post(self, url, **kwargs):
        kwargs["url"] = url
        self.calls.append(kwargs)
        return FakeResponse()


def test_backtest_upload_client_posts_batch_report_with_canonical_body() -> None:
    session = FakeSession()
    client = BacktestUploadClient(
        BacktestUploadConfig(
            base_url="http://api",
            bot_id="bot_1",
            api_key="ak_1",
            signer_secret="secret",
            run_name="baseline",
        ),
        session=session,  # type: ignore[arg-type]
    )
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    report = BacktestReport(
        context=PortfolioContext(),
        equity_history=[EquityPoint(timestamp, 1000.0, 0.0, 0.0, 0.0, 1000.0)],
        closed_trades=[
            ClosedTrade(
                symbol="BTCUSDT",
                market_type="SPOT",
                side="LONG",
                entry_timestamp=timestamp,
                exit_timestamp=timestamp,
                quantity=1.0,
                entry_price=100.0,
                exit_price=110.0,
                entry_fees=0.1,
                exit_fees=0.1,
                pnl=9.8,
                duration_seconds=0.0,
            )
        ],
        metrics=BacktestMetrics(0.1, 0.1, 1.0, 1.0, 0.0, 1.0, 2.0, 0.5, 1, 1, 0, 9.8, 0.0, 1100.0),
    )

    result = client.push_backtest_report(report)

    assert result == {"runId": "bt_1"}
    call = session.calls[0]
    assert call["url"] == "http://api/api/v1/bots/bot_1/backtest-results"
    assert call["headers"]["X-Bot-Api-Key"] == "ak_1"
    assert "X-Signature" in call["headers"]
    decompressed = gzip.decompress(call["data"]).decode("utf-8")
    assert '"startedAt":"2026-01-01T00:00:00"' in decompressed
    assert '"timestamp":"2026-01-01T00:00:00"' in decompressed
    assert "+00:00" not in decompressed
    assert '"equityHistory":[{"cash":1000.0' in decompressed


def test_backtest_upload_client_collapses_duplicate_equity_timestamps() -> None:
    session = FakeSession()
    client = BacktestUploadClient(
        BacktestUploadConfig(
            base_url="http://api",
            bot_id="bot_1",
            api_key="ak_1",
            signer_secret="secret",
            run_name="baseline",
        ),
        session=session,  # type: ignore[arg-type]
    )
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    report = BacktestReport(
        context=PortfolioContext(),
        equity_history=[
            EquityPoint(timestamp, 1000.0, 0.0, 0.0, 0.0, 1000.0),
            EquityPoint(timestamp, 1001.0, 0.0, 1.0, 0.0, 1001.0),
            EquityPoint(timestamp, 1002.0, 0.0, 2.0, 0.0, 1002.0),
        ],
        closed_trades=[],
        metrics=BacktestMetrics(0.1, 0.1, 1.0, 1.0, 0.0, 1.0, 2.0, 0.5, 1, 1, 0, 9.8, 0.0, 1100.0),
    )

    client.push_backtest_report(report)

    payload = json.loads(gzip.decompress(session.calls[0]["data"]).decode("utf-8"))
    assert payload["startedAt"] == "2026-01-01T00:00:00"
    assert payload["endedAt"] == "2026-01-01T00:00:00"
    assert len(payload["equityHistory"]) == 1
    assert payload["equityHistory"][0]["equity"] == 1002.0
