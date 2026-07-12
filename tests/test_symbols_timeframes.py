import pytest
from quant_signal_sdk.symbols import (
    normalize_symbol,
    normalize_symbol_short,
    clean_symbol,
    validate_and_normalize_symbol,
)
from quant_signal_sdk.timeframes import parse_timeframe_seconds, parse_timeframe_ms

def test_symbols():
    assert normalize_symbol("BTC/USDT:USDT") == "BTCUSDT_USDT"
    assert normalize_symbol_short("BTC/USDT:USDT") == "BTCUSDT"
    assert clean_symbol(" btc/usdt ") == "BTC/USDT"
    assert validate_and_normalize_symbol(" btc_usdt ") == "BTCUSDT"
    with pytest.raises(ValueError):
        validate_and_normalize_symbol("btc/usdt")

def test_timeframes():
    assert parse_timeframe_seconds("15m") == 900
    assert parse_timeframe_ms("1h") == 3600000
    with pytest.raises(ValueError):
        parse_timeframe_seconds("1x")
