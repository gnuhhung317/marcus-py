from __future__ import annotations

from quant_signal_sdk.client import QuantSignalClient


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

    def post_json(self, *, url: str, headers: dict, json_body: dict, timeout_seconds: float):
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
    assert call["json"] == payload


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
