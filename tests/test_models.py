from datetime import datetime, timezone

from quant_signal_sdk.models import MarginMode, MarketType, OrderType, SignalAction, SignalPayload, SignalStatus
from pydantic import ValidationError


def test_signalpayload_symbol_normalize_and_validation():
    s = SignalPayload(
        signal_id="sig-1",
        bot_id="bot-1",
        action=SignalAction.OPEN_LONG,
        symbol=" btc_usdt ",
        market_type=MarketType.SPOT,
        order_type=OrderType.LIMIT,
        entry=10,
        stop_loss=9,
        take_profit=11,
        amount=1,
        leverage=1,
        margin_mode=MarginMode.CROSS,
        reduce_only=False,
        status=SignalStatus.RECEIVED,
        generated_timestamp=datetime.now(timezone.utc),
    )
    assert s.symbol == "BTCUSDT"


def test_signalpayload_invalid_entry_validation():
    try:
        SignalPayload(
            signal_id="sig-2",
            bot_id="bot-1",
            action=SignalAction.OPEN_SHORT,
            symbol="BTCUSDT",
            market_type=MarketType.SPOT,
            order_type=OrderType.LIMIT,
            entry=0,
            stop_loss=9,
            take_profit=11,
            amount=1,
            leverage=1,
            margin_mode=MarginMode.CROSS,
            reduce_only=False,
            status=SignalStatus.RECEIVED,
            generated_timestamp=datetime.now(timezone.utc),
        )
        assert False, "Expected ValidationError"
    except ValidationError:
        assert True


def test_signalpayload_legacy_field_mapping():
    import warnings

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        
        # Instantiate with legacy fields
        s = SignalPayload(
            action=SignalAction.OPEN_LONG,
            symbol="BTCUSDT",
            market_type=MarketType.SPOT,
            order_type=OrderType.LIMIT,
            tp=110,
            sl=90,
            timestamp=1716500000000,  # millisecond timestamp
            side="LONG",
            confidence_score=0.95,
        )

        # Assert correct translations
        assert s.take_profit == 110
        assert s.stop_loss == 90
        assert s.generated_timestamp == datetime.fromtimestamp(1716500000000 / 1000.0, timezone.utc)
        assert s.metadata["side"] == "LONG"
        assert s.metadata["confidence_score"] == 0.95

        # Check deprecation warnings
        assert len(w) > 0
        warning_messages = [str(warn.message) for warn in w]
        assert any("tp is deprecated" in m for m in warning_messages)
        assert any("sl is deprecated" in m for m in warning_messages)
        assert any("timestamp is deprecated" in m for m in warning_messages)
        assert any("side is deprecated" in m for m in warning_messages)
        assert any("confidence_score is deprecated" in m for m in warning_messages)

