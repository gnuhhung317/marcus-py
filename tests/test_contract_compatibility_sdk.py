from __future__ import annotations

# pyright: reportMissingImports=false

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_signal_sdk.models import SignalPayload
from quant_signal_sdk.signing import generate_hmac_signature

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "contracts"


def _load_fixture(name: str) -> dict[str, Any]:
    fixture_path = FIXTURE_DIR / name
    return json.loads(fixture_path.read_text(encoding="utf-8"))


class SdkContractFixtureTest(unittest.TestCase):
    def test_should_match_sdk_signal_payload_contract_fixture(self) -> None:
        expected = _load_fixture("sdk_signal_payload_v1.json")
        payload = SignalPayload(
            side="LONG",
            action="OPEN_LONG",
            symbol="BTCUSDT",
            tp=30000,
            sl=28000,
            confidence_score=0.82,
            metadata={"strategy": "contract-fixture"},
            timestamp=datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(payload.model_dump(mode="json", exclude_none=True), expected)

    def test_should_match_hmac_signature_vector_fixture(self) -> None:
        vector = _load_fixture("sdk_signature_vector_v1.json")
        signature = generate_hmac_signature(vector["payload"], vector["secret"])

        self.assertEqual(signature, vector["signature"])


if __name__ == "__main__":
    unittest.main()
