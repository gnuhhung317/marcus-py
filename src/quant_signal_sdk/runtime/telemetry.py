from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from .._http import build_auth_headers, response_json_or_empty
from ..signing import canonical_json_bytes


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

        body = canonical_json_bytes(payload)
        response = self._session.post(
            self._url("/telemetry"),
            headers=self._headers(body=body),
            data=body,
            timeout=self._config.timeout_seconds,
        )
        return response_json_or_empty(response)

    def _url(self, suffix: str) -> str:
        return f"{self._config.base_url.rstrip('/')}/api/v1/bots/{self._config.bot_id}{suffix}"

    def _headers(self, *, body: bytes) -> dict[str, str]:
        return build_auth_headers(
            api_key=self._config.api_key,
            body=body,
            signer_secret=self._config.signer_secret,
        )


BotTelemetryClient = TelemetryClient

__all__ = ["TelemetryConfig", "TelemetryClient", "BotTelemetryClient"]
