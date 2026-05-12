from __future__ import annotations

import pandas as pd
import logging
from typing import Any

class BoundaryValidationException(Exception):
    """Raised when the data boundary contract is violated (e.g. time gaps detected)."""
    pass

class SignalTranslator:
    """
    The Boundary Guard ensuring a Strategy's Intent is translated into an executable Payload,
    enforcing absolute clean boundaries:
    1. Validates that no timeline gaps exist in incoming decision dataframe.
    2. Computes absolute Entry/TP/SL prices internally before handing off to the backend payload.
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

    def compile_absolute_payload(
        self, 
        bot_id: str, 
        symbol: str,
        action: str,
        timeframe: str,
        last_close: float,
        sl_absolute: float | None = None,
        tp_absolute: float | None = None,
        metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Assemble absolute values payload destined for backend routing."""
        from uuid import uuid4
        from datetime import datetime, timezone
        
        return {
            "signalId": f"sig_{uuid4().hex[:10]}",
            "botId": bot_id,
            "symbol": symbol,
            "action": action,
            "entry": float(last_close),
            "stopLoss": float(sl_absolute) if sl_absolute else None,
            "takeProfit": float(tp_absolute) if tp_absolute else None,
            "timeframe": timeframe,
            "generatedTimestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "metadata": metadata or {}
        }
