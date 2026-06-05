from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import requests

from ..signing import generate_hmac_signature


@dataclass(frozen=True, slots=True)
class TelemetryConfig:
    base_url: str
    bot_id: str
    api_key: str
    signer_secret: str | None = None
    timeout_seconds: float = 10.0


class TelemetryClient:
    """Transport for operational telemetry, separate from dry-run PnL state."""

    def __init__(self, config: TelemetryConfig, session: requests.Session | None = None) -> None:
        self._config = config
        self._session = session or requests.Session()

    def push_telemetry(
        self,
        equity: float,
        realized_pnl: float = 0.0,
        unrealized_pnl: float = 0.0,
        metrics: dict[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "equity": equity,
            "realizedPnl": realized_pnl,
            "unrealizedPnl": unrealized_pnl,
            "metrics": metrics or {},
        }
        if timestamp:
            payload["timestamp"] = timestamp

        response = self._session.post(
            self._url("/telemetry"),
            headers=self._headers(payload),
            data=self._body(payload),
            timeout=self._config.timeout_seconds,
        )
        response.raise_for_status()
        return response.json() if response.content else {}

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


BotTelemetryClient = TelemetryClient

__all__ = ["TelemetryConfig", "TelemetryClient", "BotTelemetryClient"]
