from __future__ import annotations

# pyright: reportMissingImports=false

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import requests
from pydantic import ValidationError
from requests.adapters import HTTPAdapter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_signal_sdk.client import QuantSignalClient
from quant_signal_sdk.models import SignalAction, SignalPayload, SignalSide
from quant_signal_sdk.network import NetworkClient
from quant_signal_sdk.signing import generate_hmac_signature


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.content = b'{"accepted":true}'

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeNetworkClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        json_body: dict[str, Any],
        timeout_seconds: float,
    ) -> _FakeResponse:
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "json_body": json_body,
                "timeout_seconds": timeout_seconds,
            }
        )
        return _FakeResponse({"accepted": True})


class SignalPayloadValidationTest(unittest.TestCase):
    def test_should_reject_invalid_side(self) -> None:
        with self.assertRaises(ValidationError):
            SignalPayload(
                side=cast(SignalSide, "INVALID"),
                action=SignalAction.OPEN_LONG,
                symbol="BTCUSDT",
                confidence_score=0.9,
            )

    def test_should_reject_invalid_action(self) -> None:
        with self.assertRaises(ValidationError):
            SignalPayload(
                side=SignalSide.LONG,
                action=cast(SignalAction, "BUY_NOW"),
                symbol="BTCUSDT",
                confidence_score=0.9,
            )


class NetworkClientRetryWiringTest(unittest.TestCase):
    def test_should_mount_retry_enabled_http_adapters(self) -> None:
        session = requests.Session()
        _ = NetworkClient(retries=5, backoff_factor=0.75, session=session)

        https_adapter = cast(HTTPAdapter, session.adapters["https://"])
        self.assertIsInstance(https_adapter, HTTPAdapter)
        self.assertEqual(https_adapter.max_retries.total, 5)
        self.assertAlmostEqual(https_adapter.max_retries.backoff_factor, 0.75)
        self.assertEqual(set(https_adapter.max_retries.status_forcelist), {500, 502, 503, 504})

    def test_should_wire_mount_calls_for_mocked_session(self) -> None:
        mock_session = MagicMock()
        _ = NetworkClient(session=mock_session)

        self.assertEqual(mock_session.mount.call_count, 2)


class SignatureGenerationTest(unittest.TestCase):
    def test_should_generate_deterministic_hmac_signature(self) -> None:
        payload_a = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "action": "OPEN_LONG",
            "confidence_score": 0.9,
        }
        payload_b = {
            "action": "OPEN_LONG",
            "confidence_score": 0.9,
            "side": "LONG",
            "symbol": "BTCUSDT",
        }

        sig_a = generate_hmac_signature(payload_a, "dev-secret")
        sig_b = generate_hmac_signature(payload_b, "dev-secret")

        self.assertEqual(sig_a, sig_b)
        self.assertEqual(sig_a, "554c259bafbb52125cd885546f81e07dbbab240b8c1e5df0aeab390b0e66581a")


class QuantSignalClientRequestBuildTest(unittest.TestCase):
    def test_should_build_endpoint_headers_and_body_when_sending_signal(self) -> None:
        fake_network = _FakeNetworkClient()
        client = QuantSignalClient(
            base_url="https://api.marcus.test/",
            api_key="api-key-123",
            timeout_seconds=15,
            signer_secret="signing-secret",
            network_client=fake_network,
        )
        signal = SignalPayload(
            side=SignalSide.LONG,
            action=SignalAction.OPEN_LONG,
            symbol="BTCUSDT",
            tp=30000,
            sl=28000,
            confidence_score=0.82,
            metadata={"strategy": "unit-test"},
            timestamp=datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc),
        )

        response = client.send_signal(signal)

        self.assertEqual(response, {"accepted": True})
        self.assertEqual(len(fake_network.calls), 1)

        call = fake_network.calls[0]
        self.assertEqual(call["url"], "https://api.marcus.test/api/v1/signals")
        self.assertEqual(call["timeout_seconds"], 15)
        self.assertEqual(call["headers"]["X-API-Key"], "api-key-123")
        self.assertIn("X-Signature", call["headers"])
        self.assertEqual(call["headers"]["Content-Type"], "application/json")
        self.assertEqual(call["json_body"]["symbol"], "BTCUSDT")
        self.assertEqual(call["json_body"]["action"], "OPEN_LONG")
        self.assertEqual(call["json_body"]["timestamp"], "2026-04-01T12:00:00Z")


if __name__ == "__main__":
    unittest.main()
