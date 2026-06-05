from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_signal_sdk.runtime.telemetry import TelemetryClient, TelemetryConfig


class FakeResponse:
    content = b'{"status":"ok"}'

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"status": "ok"}


@dataclass
class FakeSession:
    def __post_init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        kwargs["url"] = url
        self.calls.append(kwargs)
        return FakeResponse()


def test_push_telemetry_constructs_canonical_payload() -> None:
    session = FakeSession()
    client = TelemetryClient(
        TelemetryConfig(
            base_url="http://api",
            bot_id="bot_123",
            api_key="ak_test",
            signer_secret="secret_test",
        ),
        session=session,  # type: ignore[arg-type]
    )

    result = client.push_telemetry(
        equity=10500.25,
        realized_pnl=500.25,
        unrealized_pnl=-50.0,
        metrics={"cpu": 12.5, "latency": 45},
        timestamp="2026-06-05T09:00:00Z",
    )

    assert result == {"status": "ok"}
    assert len(session.calls) == 1

    call = session.calls[0]
    assert call["url"] == "http://api/api/v1/bots/bot_123/telemetry"
    assert call["headers"]["X-Bot-Api-Key"] == "ak_test"
    assert "X-Signature" in call["headers"]

    payload = json.loads(call["data"])
    assert payload["equity"] == 10500.25
    assert payload["realizedPnl"] == 500.25
    assert payload["unrealizedPnl"] == -50.0
    assert payload["timestamp"] == "2026-06-05T09:00:00Z"
    assert payload["metrics"] == {"cpu": 12.5, "latency": 45}


def test_push_telemetry_uses_defaults_when_omitted() -> None:
    session = FakeSession()
    client = TelemetryClient(
        TelemetryConfig(
            base_url="http://api",
            bot_id="bot_123",
            api_key="ak_test",
        ),
        session=session,  # type: ignore[arg-type]
    )

    client.push_telemetry(equity=999.0)

    call = session.calls[0]
    payload = json.loads(call["data"])
    assert payload["equity"] == 999.0
    assert payload["realizedPnl"] == 0.0
    assert payload["unrealizedPnl"] == 0.0
    assert payload["metrics"] == {}
    assert "timestamp" not in payload
    assert "X-Signature" not in call["headers"]
