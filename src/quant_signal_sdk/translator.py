from __future__ import annotations

import pandas as pd
import logging
from typing import Any

from .models import SignalPayload, SignalAction, ExecutionPolicies
from .timeframes import parse_timeframe_seconds
import time

class BoundaryValidationException(Exception):
    """Raised when the data boundary contract is violated (e.g. time gaps detected)."""
    pass


class RiskManager:
    """
    Interface for calculating risk parameters like Stop Loss (SL) and Take Profit (TP).
    Allows decoupling of risk calculation from signal transport/boundary layers.
    """
    def calculate_sl_tp(
        self,
        entry: float,
        action: SignalAction,
        **kwargs: Any
    ) -> tuple[float | None, float | None]:
        raise NotImplementedError("RiskManager must implement calculate_sl_tp")


class PercentageRiskManager(RiskManager):
    """Simple percentage-based SL/TP risk manager."""
    def __init__(self, sl_percent: float, tp_percent: float):
        self.sl_percent = sl_percent
        self.tp_percent = tp_percent

    def calculate_sl_tp(
        self,
        entry: float,
        action: SignalAction,
        **kwargs: Any
    ) -> tuple[float | None, float | None]:
        if action in (SignalAction.OPEN_LONG, SignalAction.CLOSE_SHORT):
            sl = entry * (1.0 - self.sl_percent)
            tp = entry * (1.0 + self.tp_percent)
        else:
            sl = entry * (1.0 + self.sl_percent)
            tp = entry * (1.0 - self.tp_percent)
        return round(sl, 8), round(tp, 8)


class SignalTranslator:
    """
    The Boundary Guard ensures a strategy's intent is validated at the data boundary
    before it is serialized to the backend payload shape.
    """

    def __init__(self, logger: logging.Logger | None = None):
        self._logger = logger or logging.getLogger(__name__)

    def validate_timeframe_integrity(self, df: pd.DataFrame, expected_timeframe: str):
        """
        Validate that candle timelines form a continuous contiguous sequence.
        Raises BoundaryValidationException if large data gaps detected.
        """
        if df.empty or len(df) < 2:
            return
            
        # Perform raw index-diff analysis
        diffs = df.index.to_series().diff().dropna()
        mode_diff = diffs.mode()[0]
        
        last_diff = diffs.iloc[-1]
        
        # Tolerate dynamic diffs up to 1.5x interval mode
        if last_diff > (mode_diff * 1.5):
             raise BoundaryValidationException(
                 f"Detected significant data gap leading up to signal. Expected ~{mode_diff}, got {last_diff}. "
                 "Aborting signal dispatch for boundary safety."
             )

    def to_backend_payload(self, signal: SignalPayload) -> dict[str, Any]:
        """Serialize a validated signal model into the backend contract shape."""
        return signal.model_dump(mode="json", by_alias=True, exclude_none=True)

    def build_policies_from_candles(
        self,
        *,
        cancel_after_candles: int | float | None = None,
        close_after_candles: int | float | None = None,
        timeframe: str | None = None,
    ) -> ExecutionPolicies | None:
        """Convert candle-based timeouts into absolute UNIX-second timestamps.

        Example: cancel_after_candles=4 and timeframe='15m' -> cancelOrderAfter = now + 4*900
        Returns an ExecutionPolicies instance or None when no inputs provided.
        """
        if cancel_after_candles is None and close_after_candles is None:
            return None

        if timeframe is None:
            raise ValueError("timeframe is required when using candle-based policies")

        candle_seconds = parse_timeframe_seconds(timeframe)

        now = int(time.time())
        kwargs: dict[str, int] = {}
        if cancel_after_candles is not None:
            kwargs["cancel_order_after"] = int(now + int(cancel_after_candles) * candle_seconds)
        if close_after_candles is not None:
            kwargs["close_position_after"] = int(now + int(close_after_candles) * candle_seconds)

        return ExecutionPolicies(**kwargs)
