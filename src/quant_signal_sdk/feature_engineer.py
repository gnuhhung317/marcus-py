from __future__ import annotations

import pandas as pd
from typing import Callable

class FeatureEngineer:
    """
    Stateless orchestrator responsible for applying technical indicator transformations
    upon a standardized Pandas DataFrame.
    """

    @staticmethod
    def calculate_sma(df: pd.DataFrame, column: str = "close", window: int = 14) -> pd.DataFrame:
        """Calculate Simple Moving Average."""
        df = df.copy()
        df[f"sma_{window}"] = df[column].rolling(window=window).mean()
        return df

    @staticmethod
    def calculate_atr(df: pd.DataFrame, window: int = 14) -> pd.DataFrame:
        """Calculate Average True Range."""
        df = df.copy()
        high = df["high"]
        low = df["low"]
        prev_close = df["close"].shift(1)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df["atr"] = tr.rolling(window=window).mean()
        return df

    @staticmethod
    def apply_pipeline(df: pd.DataFrame, *stages: Callable[[pd.DataFrame], pd.DataFrame]) -> pd.DataFrame:
        """Execute list of transformation pipeline stages sequentially."""
        res = df.copy()
        for stage in stages:
            res = stage(res)
        return res
