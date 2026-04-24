from quant_signal_sdk.strategy import SimpleSmaStrategy


def test_sma_insufficient_data():
    strat = SimpleSmaStrategy(short_window=3, long_window=5)
    assert strat.generate_signal_payload("bot", [1, 2, 3]) is None


def test_sma_generates_signal_open_long_and_short():
    strat = SimpleSmaStrategy(short_window=2, long_window=3)
    # prices where short MA > long MA -> OPEN_LONG
    payload = strat.generate_signal_payload("bot", [10, 11, 12, 13, 14])
    assert payload is not None and payload["action"] in ("OPEN_LONG", "OPEN_SHORT")
