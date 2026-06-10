from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

import requests

from ..signing import canonical_json_bytes, generate_hmac_signature_bytes, gzip_bytes
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
        timestamp = str(int(time.time() * 1000))
        body = self._body(payload)
        response = self._session.post(
            self._url("/backtest-results"),
            headers=self._headers(payload, timestamp=timestamp, body=body),
            data=body,
            timeout=self._config.timeout_seconds,
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def _payload(self, report: BacktestReport) -> dict[str, Any]:
        equity_history = self._dedupe_equity_history(report.equity_history)
        return {
            "runName": self._config.run_name,
            "startedAt": self._format_timestamp(equity_history[0].timestamp) if equity_history else None,
            "endedAt": self._format_timestamp(equity_history[-1].timestamp) if equity_history else None,
            "metrics": asdict(report.metrics) if report.metrics is not None else {},
            "equityHistory": [
                {
                    "timestamp": self._format_timestamp(point.timestamp),
                    "cash": point.cash,
                    "equity": point.equity,
                    "realizedPnl": point.realized_pnl,
                    "unrealizedPnl": point.unrealized_pnl,
                    "totalFees": point.total_fees,
                }
                for point in equity_history
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
                    "entryTimestamp": self._format_timestamp(trade.entry_timestamp),
                    "exitTimestamp": self._format_timestamp(trade.exit_timestamp),
                    "durationSeconds": trade.duration_seconds,
                }
                for trade in report.closed_trades
            ],
        }

    def _url(self, suffix: str) -> str:
        return f"{self._config.base_url.rstrip('/')}/api/v1/bots/{self._config.bot_id}{suffix}"

    def _headers(self, payload: dict[str, Any], *, timestamp: str, body: bytes) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "X-Bot-Api-Key": self._config.api_key,
            "X-Timestamp": timestamp,
        }
        if self._config.signer_secret:
            headers["X-Signature"] = generate_hmac_signature_bytes(body, self._config.signer_secret, timestamp=timestamp)
        return headers

    def _body(self, payload: dict[str, Any]) -> bytes:
        return gzip_bytes(canonical_json_bytes(payload))

    @staticmethod
    def _format_timestamp(value: datetime) -> str:
        if value.tzinfo is None:
            return value.isoformat()
        return value.astimezone(timezone.utc).replace(tzinfo=None).isoformat()

    @staticmethod
    def _dedupe_equity_history(equity_history: list[Any]) -> list[Any]:
        unique_points: dict[datetime, Any] = {}
        for point in equity_history:
            unique_points[point.timestamp] = point
        return list(unique_points.values())


__all__ = ["BacktestUploadConfig", "BacktestUploadClient"]
