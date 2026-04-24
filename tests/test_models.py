from quant_signal_sdk.models import SignalPayload, SignalAction, SignalSide
from pydantic import ValidationError


def test_signalpayload_symbol_normalize_and_validation():
    s = SignalPayload(side=SignalSide.LONG, action=SignalAction.OPEN_LONG, symbol=" btc_usdt ", confidence_score=0.5)
    assert s.symbol == "BTCUSDT"


def test_signalpayload_invalid_confidence():
    try:
        SignalPayload(side=SignalSide.SHORT, action=SignalAction.OPEN_SHORT, symbol="BTCUSDT", confidence_score=2.0)
        assert False, "Expected ValidationError"
    except ValidationError:
        assert True
