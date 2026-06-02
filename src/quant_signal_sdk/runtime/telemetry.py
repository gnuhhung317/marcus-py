from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from ..signing import generate_hmac_signature
from .interfaces import PortfolioContext


@dataclass(frozen=True, slots=True)
class TelemetryConfig:
    base_url: str
    bot_id: str
    api_key: str
    signer_secret: str | None = None
    sync_interval_seconds: float = 3600.0
    timeout_seconds: float = 10.0


class BotTelemetryClient:
    def __init__(self, config: TelemetryConfig, session: requests.Session | None = None) -> None:
        self._config = config
        self._session = session or requests.Session()

    def fetch_latest(self) -> dict[str, Any] | None:
        response = self._session.get(
            self._url("/telemetry/latest"),
            headers=self._headers({}),
            timeout=self._config.timeout_seconds,
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        return response.json()

    def push_context(self, context: PortfolioContext) -> dict[str, Any]:
        payload = {
            "timestamp": self._format_timestamp(context.timestamp),
            "equity": context.equity,
            "realizedPnl": context.realized_pnl,
            "unrealizedPnl": context.unrealized_pnl,
        }
        response = self._session.post(
            self._url("/telemetry"),
            headers=self._headers(payload),
            json=payload,
            timeout=self._config.timeout_seconds,
        )
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

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

    def _format_timestamp(self, timestamp: datetime | None) -> str:
        value = timestamp or datetime.now(timezone.utc)
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat()
