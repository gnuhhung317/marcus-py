from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from typing import Any

import requests

from ..signing import generate_hmac_signature
from .backtest import BacktestReport


@dataclass(frozen=True, slots=True)
class BacktestUploadConfig:
    base_url: str
    bot_id: str
    api_key: str
    signer_secret: str | None = None
    run_name: str | None = None
    timeout_seconds: float = 30.0


class BacktestUploadClient:
    def __init__(self, config: BacktestUploadConfig, session: requests.Session | None = None) -> None:
        self._config = config
        self._session = session or requests.Session()

    def push_backtest_report(self, report: BacktestReport) -> dict[str, Any]:
        payload = self._payload(report)
        response = self._session.post(
            self._url("/backtest-results"),
            headers=self._headers(payload),
            data=self._body(payload),
            timeout=self._config.timeout_seconds,
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def _payload(self, report: BacktestReport) -> dict[str, Any]:
        return {
            "runName": self._config.run_name,
            "startedAt": report.equity_history[0].timestamp.isoformat() if report.equity_history else None,
            "endedAt": report.equity_history[-1].timestamp.isoformat() if report.equity_history else None,
            "metrics": asdict(report.metrics) if report.metrics is not None else {},
            "equityHistory": [
                {
                    "timestamp": point.timestamp.isoformat(),
                    "cash": point.cash,
                    "equity": point.equity,
                    "realizedPnl": point.realized_pnl,
                    "unrealizedPnl": point.unrealized_pnl,
                    "totalFees": point.total_fees,
                }
                for point in report.equity_history
            ],
            "closedTrades": [
                {
                    "symbol": trade.symbol,
                    "marketType": trade.market_type,
                    "side": trade.side,
                    "quantity": trade.quantity,
                    "entryPrice": trade.entry_price,
                    "exitPrice": trade.exit_price,
                    "pnl": trade.pnl,
                    "fees": trade.entry_fees + trade.exit_fees,
                    "entryTimestamp": trade.entry_timestamp.isoformat(),
                    "exitTimestamp": trade.exit_timestamp.isoformat(),
                    "durationSeconds": trade.duration_seconds,
                }
                for trade in report.closed_trades
            ],
        }

    def _url(self, suffix: str) -> str:
        return f"{self._config.base_url.rstrip('/')}/api/v1/bots/{self._config.bot_id}{suffix}"

    def _headers(self, payload: dict[str, Any]) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        headers = {
            "Content-Type": "application/json",
            "X-Bot-Api-Key": self._config.api_key,
            "X-Timestamp": timestamp,
        }
        if self._config.signer_secret:
            headers["X-Signature"] = generate_hmac_signature(payload, self._config.signer_secret, timestamp=timestamp)
        return headers

    def _body(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


__all__ = ["BacktestUploadConfig", "BacktestUploadClient"]
