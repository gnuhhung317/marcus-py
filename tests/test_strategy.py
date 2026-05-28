from quant_signal_sdk.strategy import SimpleSmaStrategy
from quant_signal_sdk.translator import PercentageRiskManager
from quant_signal_sdk.models import SignalAction


def test_sma_insufficient_data():
    strat = SimpleSmaStrategy(short_window=3, long_window=5)
    assert strat.generate_signal_payload("bot", [1, 2, 3]) is None


def test_sma_generates_signal_open_long_and_short():
    strat = SimpleSmaStrategy(short_window=2, long_window=3)
    # prices where short MA > long MA -> OPEN_LONG
    payload = strat.generate_signal_payload("bot", [10, 11, 12, 13, 14])
    assert payload is not None and payload["action"] in ("OPEN_LONG", "OPEN_SHORT")


def test_percentage_risk_manager():
    rm = PercentageRiskManager(sl_percent=0.02, tp_percent=0.05)
    sl, tp = rm.calculate_sl_tp(100.0, SignalAction.OPEN_LONG)
    assert sl == 98.0
    assert tp == 105.0

    sl, tp = rm.calculate_sl_tp(100.0, SignalAction.OPEN_SHORT)
    assert sl == 102.0
    assert tp == 95.0
