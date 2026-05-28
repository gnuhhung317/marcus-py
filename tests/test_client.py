from __future__ import annotations

from typing import Any
from datetime import datetime, timezone

from quant_signal_sdk.client import QuantSignalClient
from quant_signal_sdk.models import MarketType, OrderType, SignalAction, SignalPayload, SignalStatus, MarginMode


class FakeResponse:
    def __init__(self, content: bytes = b'{"result":"ok"}'):
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self):
        import json

        return json.loads(self.content)


class DummyNetworkClient:
    def __init__(self):
        self.calls = []

    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        json_body: dict[str, Any],
        timeout_seconds: float,
    ) -> FakeResponse:
        self.calls.append({"url": url, "headers": headers, "json": json_body, "timeout": timeout_seconds})
        return FakeResponse()


def test_send_payload_with_bot_key_sets_header_and_returns_json():
    net = DummyNetworkClient()
    client = QuantSignalClient(base_url="http://localhost:8080", api_key="user-key", network_client=net)

    payload = {"signal": "x"}
    result = client.send_payload_with_bot_key(payload, bot_api_key="bot-abc-123")

    assert result == {"result": "ok"}
    assert len(net.calls) == 1
    call = net.calls[0]
    assert call["headers"].get("X-Bot-Api-Key") == "bot-abc-123"
    assert "X-Timestamp" in call["headers"]
    assert call["json"]["signal"] == "x"
    assert "signalId" in call["json"]
    assert "generatedTimestamp" in call["json"]


def test_send_payload_with_bot_key_signs_payload_when_secret_is_set():
    net = DummyNetworkClient()
    client = QuantSignalClient(
        base_url="http://localhost:8080",
        api_key="user-key",
        signer_secret="bot-secret-123",
        network_client=net,
    )

    payload = {"signal": "x"}
    result = client.send_payload_with_bot_key(payload, bot_api_key="bot-abc-123")

    assert result == {"result": "ok"}
    assert len(net.calls) == 1
    call = net.calls[0]
    assert call["headers"].get("X-Bot-Api-Key") == "bot-abc-123"
    assert "X-Timestamp" in call["headers"]
    assert "X-Signature" in call["headers"]
    assert call["json"]["signal"] == "x"
    assert "signalId" in call["json"]
    assert "generatedTimestamp" in call["json"]


def test_send_signal_auto_injects_ids_from_client_context():
    net = DummyNetworkClient()
    client = QuantSignalClient(
        base_url="http://localhost:8080",
        api_key="user-key",
        default_bot_id="bot-context-123",
        network_client=net,
    )

    signal = SignalPayload(
        action=SignalAction.OPEN_LONG,
        symbol="BTCUSDT",
        market_type=MarketType.SPOT,
        order_type=OrderType.LIMIT,
        entry=29000,
        stop_loss=28000,
        take_profit=30000,
        amount=0.01,
        leverage=1,
        margin_mode=MarginMode.CROSS,
        reduce_only=False,
        status=SignalStatus.RECEIVED,
        generated_timestamp=datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc),
        metadata={"strategy": "unit-test"},
    )

    response = client.send_signal(signal)

    assert response == {"result": "ok"}
    assert len(net.calls) == 1
    call = net.calls[0]
    assert call["json"]["botId"] == "bot-context-123"
    assert call["json"]["signalId"] is not None
    assert signal.bot_id == "bot-context-123"
    assert signal.signal_id == call["json"]["signalId"]


def test_register_bot_sends_auth_or_bot_key_header():
    net = DummyNetworkClient()
    client = QuantSignalClient(base_url="http://localhost:8080", api_key="user-key", network_client=net)

    # auth token path
    resp1 = client.register_bot({"botId": "b1"}, auth_token="auth-xyz")
    assert resp1 == {"result": "ok"}
    assert net.calls[-1]["headers"].get("Authorization") == "Bearer auth-xyz"

    # bot api key path
    resp2 = client.register_bot({"botId": "b2"}, bot_api_key="bot-key-456")
    assert resp2 == {"result": "ok"}
    assert net.calls[-1]["headers"].get("X-Bot-Api-Key") == "bot-key-456"


def test_send_heartbeat_sends_post_with_bot_api_key():
    net = DummyNetworkClient()
    client = QuantSignalClient(
        base_url="http://localhost:8080",
        api_key="default-key",
        default_bot_id="default-bot-123",
        signer_secret="my-signer-secret",
        network_client=net,
    )

    # test case 1: use defaults
    result = client.send_heartbeat()
    assert result == {"result": "ok"}
    assert len(net.calls) == 1
    call = net.calls[0]
    assert call["url"] == "http://localhost:8080/api/v1/bots/default-bot-123/heartbeat"
    assert call["headers"].get("X-Bot-Api-Key") == "default-key"
    assert "X-Timestamp" in call["headers"]
    assert "X-Signature" in call["headers"]
    assert call["json"] == {}

    # test case 2: override bot_id and api_key
    result2 = client.send_heartbeat(bot_id="override-bot", bot_api_key="override-key")
    assert result2 == {"result": "ok"}
    assert len(net.calls) == 2
    call2 = net.calls[1]
    assert call2["url"] == "http://localhost:8080/api/v1/bots/override-bot/heartbeat"
    assert call2["headers"].get("X-Bot-Api-Key") == "override-key"
    assert "X-Timestamp" in call2["headers"]
    assert "X-Signature" in call2["headers"]
    assert call2["json"] == {}


def test_heartbeat_loop_starts_and_stops():
    import time
    net = DummyNetworkClient()
    client = QuantSignalClient(
        base_url="http://localhost:8080",
        api_key="default-key",
        default_bot_id="default-bot-123",
        network_client=net,
    )

    client.start_heartbeat_loop(interval_seconds=0.01)
    # Give it a tiny bit of time to make the initial call
    time.sleep(0.05)
    client.stop_heartbeat_loop()

    assert len(net.calls) >= 1
    call = net.calls[0]
    assert call["url"] == "http://localhost:8080/api/v1/bots/default-bot-123/heartbeat"
    assert call["headers"].get("X-Bot-Api-Key") == "default-key"
