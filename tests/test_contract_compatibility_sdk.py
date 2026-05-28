from __future__ import annotations

# pyright: reportMissingImports=false

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_signal_sdk.models import MarginMode, MarketType, OrderType, SignalAction, SignalPayload, SignalStatus
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
        )

        self.assertEqual(payload.model_dump(mode="json", by_alias=True, exclude_none=True), expected)

    def test_should_match_hmac_signature_vector_fixture(self) -> None:
        vector = _load_fixture("sdk_signature_vector_v1.json")
        signature = generate_hmac_signature(vector["payload"], vector["secret"])

        self.assertEqual(signature, vector["signature"])


if __name__ == "__main__":
    unittest.main()
