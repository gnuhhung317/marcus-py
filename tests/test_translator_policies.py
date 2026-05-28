from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_signal_sdk.translator import SignalTranslator


def test_build_policies_from_candles_15m_4_candles():
    tr = SignalTranslator()
    now = int(time.time())
    policies = tr.build_policies_from_candles(cancel_after_candles=4, timeframe="15m")
    assert policies is not None
    assert hasattr(policies, "cancel_order_after")
    # Expected delta is 4 * 15m = 3600 seconds
    expected = now + 3600
    # allow 3s tolerance
    assert abs(policies.cancel_order_after - expected) <= 3
