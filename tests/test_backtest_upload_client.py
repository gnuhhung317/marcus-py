from __future__ import annotations

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
    assert '"equityHistory":[{"cash":1000.0' in call["data"]
