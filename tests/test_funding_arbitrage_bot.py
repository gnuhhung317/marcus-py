from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import tempfile
import os

from examples.funding_arbitrage_bot import (
    QuantFeatureEngineer,
    ArbitrageBot,
    StateManager,
    dispatch_arbitrage_orders,
    fetch_arbitrage_candidates,
)
from quant_signal_sdk.client import QuantSignalClient
from quant_signal_sdk.models import SignalAction, MarketType


class TestFundingArbitrageBot(unittest.TestCase):
    def test_feature_engineering_basic(self) -> None:
        engineer = QuantFeatureEngineer()
        
        # Build mock dataframes
        idx = pd.date_range(end="2026-05-25 00:00:00", periods=200, freq="h")
        ohlcv = pd.DataFrame(
            {
                "open": np.random.randn(200) + 100.0,
                "high": np.random.randn(200) + 101.0,
                "low": np.random.randn(200) + 99.0,
                "close": np.random.randn(200) + 100.0,
                "volume": np.random.rand(200) * 1000.0,
            },
            index=idx,
        )
        funding = pd.DataFrame(
            {"funding_rate": [0.0001] * 200},
            index=idx,
        )

        features = engineer.calculate_features("BTC/USDT:USDT", ohlcv, funding)
        self.assertIsNotNone(features)
        self.assertIn("roc_close_24h", features.index)
        self.assertIn("volatility_24h", features.index)
        self.assertIn("sum_funding_168h", features.index)
        self.assertIn("mean_funding_168h", features.index)

    def test_arbitrage_bot_heuristic_ranking(self) -> None:
        bot = ArbitrageBot(model_paths=[], features=[])
        
        feature_df = pd.DataFrame(
            [
                {"symbol": "BTC/USDT:USDT", "funding_rate": 0.0001, "sum_funding_24h": 0.0003},
                {"symbol": "ETH/USDT:USDT", "funding_rate": 0.0002, "sum_funding_24h": 0.0006},
            ]
        )

        ranked = bot.predict_scores(feature_df)
        self.assertIn("predicted_score", ranked.columns)
        
        # ETH has higher sum_funding_24h, so it should rank higher when sorting by predicted_score descending
        ranked_sorted = ranked.sort_values(by="predicted_score", ascending=False).reset_index(drop=True)
        self.assertEqual(ranked_sorted.loc[0, "symbol"], "ETH/USDT:USDT")

    def test_state_manager_portfolio(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name

        try:
            state_mgr = StateManager(filepath=tmp_path)
            # Empty state initially
            self.assertEqual(state_mgr.load_portfolio(), {})

            # Save portfolio
            test_pairs = {"BTC/USDT:USDT": 1.5, "ETH/USDT:USDT": 2.0}
            state_mgr.save_portfolio(test_pairs)

            # Reload
            loaded = state_mgr.load_portfolio()
            self.assertEqual(loaded, test_pairs)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    @patch("quant_signal_sdk.client.QuantSignalClient.send_signal")
    def test_dispatch_arbitrage_orders_sends_spot_and_future(self, mock_send_signal: MagicMock) -> None:
        mock_send_signal.return_value = {"status": "success"}

        client = QuantSignalClient(base_url="http://localhost:8080", api_key="bot_key", default_bot_id="my_bot")
        dispatch_arbitrage_orders(
            client=client,
            symbol="BTC/USDT:USDT",
            action="OPEN",
            amount=1.5,
            leverage=3,
            margin_mode="ISOLATED",
        )

        # Should have sent 2 structured signals: 1 SPOT, 1 FUTURE
        self.assertEqual(mock_send_signal.call_count, 2)

        calls = mock_send_signal.call_args_list
        spot_signal = calls[0][0][0]
        future_signal = calls[1][0][0]

        # Spot signal assertions
        # It may be a SignalPayload object — inspect attributes if present
        if hasattr(spot_signal, "action"):
            self.assertEqual(spot_signal.action.value, SignalAction.OPEN_LONG.value)
            self.assertEqual(spot_signal.symbol, "BTCUSDT")
            self.assertEqual(spot_signal.market_type.value, MarketType.SPOT.value)
            self.assertEqual(spot_signal.amount, 1.5)
        else:
            # fallback to dict representation
            self.assertEqual(spot_signal["action"], SignalAction.OPEN_LONG.value)
            self.assertEqual(spot_signal["symbol"], "BTCUSDT")
            self.assertEqual(spot_signal["marketType"], MarketType.SPOT.value)
            self.assertEqual(spot_signal["amount"], 1.5)

        # Futures signal assertions
        if hasattr(future_signal, "action"):
            self.assertEqual(future_signal.action.value, SignalAction.OPEN_SHORT.value)
            self.assertEqual(future_signal.symbol, "BTCUSDT")
            self.assertEqual(future_signal.market_type.value, MarketType.FUTURE.value)
            self.assertEqual(future_signal.amount, 1.5)
            self.assertEqual(future_signal.leverage, 3)
            self.assertEqual(future_signal.margin_mode.value, "ISOLATED")
        else:
            self.assertEqual(future_signal["action"], SignalAction.OPEN_SHORT.value)
            self.assertEqual(future_signal["symbol"], "BTCUSDT")
            self.assertEqual(future_signal["marketType"], MarketType.FUTURE.value)
            self.assertEqual(future_signal["amount"], 1.5)
            self.assertEqual(future_signal["leverage"], 3)
            self.assertEqual(future_signal["marginMode"], "ISOLATED")

    def test_fetch_arbitrage_candidates(self) -> None:
        mock_exchange = MagicMock()
        mock_exchange.id = "binance"
        mock_exchange.markets = {
            "BTC/USDT": {"spot": True, "active": True, "base": "BTC", "quote": "USDT"},
            "ETH/USDT": {"spot": True, "active": True, "base": "ETH", "quote": "USDT"},
            "BTC/USDT:USDT": {"swap": True, "active": True, "linear": True, "base": "BTC", "quote": "USDT"},
            "ETH/USDT:USDT": {"swap": True, "active": True, "linear": True, "base": "ETH", "quote": "USDT"},
            "SOL/USDT:USDT": {"swap": True, "active": True, "linear": True, "base": "SOL", "quote": "USDT"}, # No spot
        }
        mock_exchange.fetch_funding_rates.return_value = {
            "BTC/USDT:USDT": {"fundingRate": 0.0001},
            "ETH/USDT:USDT": {"fundingRate": 0.0002},
            "SOL/USDT:USDT": {"fundingRate": 0.0003},
        }

        candidates = fetch_arbitrage_candidates(mock_exchange)
        
        # BTC and ETH have spot counterparts, SOL does not
        self.assertIn("BTC/USDT:USDT", candidates)
        self.assertIn("ETH/USDT:USDT", candidates)
        self.assertNotIn("SOL/USDT:USDT", candidates)

        self.assertEqual(candidates["BTC/USDT:USDT"]["spot_symbol"], "BTC/USDT")
        self.assertEqual(candidates["ETH/USDT:USDT"]["funding_rate"], 0.0002)


if __name__ == "__main__":
    unittest.main()
