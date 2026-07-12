from __future__ import annotations

# pyright: reportMissingImports=false

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_signal_sdk.models import ExecutionPolicies, MarginMode, MarketType, OrderType, SignalAction, SignalPayload, SignalStatus
from quant_signal_sdk.signing import generate_hmac_signature

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "contracts"


def _load_fixture(name: str) -> dict[str, Any]:
    fixture_path = FIXTURE_DIR / name
    return json.loads(fixture_path.read_text(encoding="utf-8"))


class SdkContractFixtureTest(unittest.TestCase):
    def test_should_match_sdk_signal_payload_contract_fixture(self) -> None:
        expected = _load_fixture("sdk_signal_payload_v1.json")
        payload = SignalPayload(
            signal_id="sig-contract-001",
            bot_id="bot-contract-001",
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
            timeframe="1h",
            metadata={"strategy": "contract-fixture"},
            policies=ExecutionPolicies(
                max_size_percent=0.1,
                cancel_order_after=1711976400,
                close_position_after=1711978200,
            ),
        )

        self.assertEqual(payload.model_dump(mode="json", by_alias=True, exclude_none=True), expected)

    def test_should_serialize_update_tp_sl_contract_payload(self) -> None:
        payload = SignalPayload(
            signal_id="sig-update-001",
            bot_id="bot-contract-001",
            action=SignalAction.UPDATE_TP_SL,
            symbol="BTCUSDT",
            market_type=MarketType.FUTURE,
            order_type=OrderType.MARKET,
            stop_loss=28250,
            take_profit=30100,
            generated_timestamp=datetime(2026, 4, 1, 12, 5, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(
            payload.model_dump(mode="json", by_alias=True, exclude_none=True),
            {
                "signalId": "sig-update-001",
                "botId": "bot-contract-001",
                "action": "UPDATE_TP_SL",
                "symbol": "BTCUSDT",
                "marketType": "FUTURE",
                "orderType": "MARKET",
                "stopLoss": 28250.0,
                "takeProfit": 30100.0,
                "generatedTimestamp": "2026-04-01T12:05:00Z",
                "metadata": {},
            },
        )

    def test_should_match_hmac_signature_vector_fixture(self) -> None:
        vector = _load_fixture("sdk_signature_vector_v1.json")
        signature = generate_hmac_signature(vector["payload"], vector["secret"])

        self.assertEqual(signature, vector["signature"])


if __name__ == "__main__":
    unittest.main()
