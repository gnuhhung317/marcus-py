from __future__ import annotations
from typing import Any

def normalize_symbol(symbol: Any) -> str:
    """Normalize symbol for CCXT clients (replaces / and - with empty string, : with _)."""
    if symbol is None:
        return ""
    clean = str(symbol).strip().upper()
    return clean.replace("/", "").replace("-", "").replace(":", "_")

def normalize_symbol_short(symbol: Any) -> str:
    """Normalize symbol and drop swap/future suffixes after ':'."""
    if symbol is None:
        return ""
    clean = str(symbol).strip().upper()
    return clean.replace("/", "").replace("-", "").replace("_", "").split(":")[0]

def clean_symbol(symbol: Any) -> str:
    """Basic symbol cleanup: strip and uppercase."""
    if symbol is None:
        return ""
    return str(symbol).strip().upper()

def validate_and_normalize_symbol(symbol: str) -> str:
    """Normalize and validate symbol for Pydantic models (removes _ and -)."""
    upper_val = symbol.strip().upper()
    cleaned = upper_val.replace("_", "").replace("-", "")
    if not cleaned.isalnum():
        raise ValueError("symbol must contain only letters, numbers, '_' or '-'")
    return cleaned
