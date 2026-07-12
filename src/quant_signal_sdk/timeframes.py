from __future__ import annotations
import re

_TIMEFRAME_PATTERN = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)

def parse_timeframe_seconds(timeframe: str) -> int:
    """Parse timeframe string (e.g. '15m', '1h') to total seconds."""
    match = _TIMEFRAME_PATTERN.match(timeframe)
    if match is None:
        raise ValueError(
            f"Unsupported timeframe format: {timeframe!r}. Expected values like '15m', '1h', '4h', or '1d'."
        )
    quantity = int(match.group(1))
    unit = match.group(2).lower()
    unit_seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    return quantity * unit_seconds

def parse_timeframe_ms(timeframe: str) -> int:
    """Parse timeframe string to milliseconds."""
    return parse_timeframe_seconds(timeframe) * 1000
