from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SignalSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class SignalAction(str, Enum):
    OPEN_LONG = "OPEN_LONG"
    OPEN_SHORT = "OPEN_SHORT"
    CLOSE_LONG = "CLOSE_LONG"
    CLOSE_SHORT = "CLOSE_SHORT"
    CLOSE = "CLOSE"


class SignalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    side: SignalSide
    action: SignalAction
    symbol: str = Field(min_length=2, max_length=24)
    tp: float | None = Field(default=None, gt=0)
    sl: float | None = Field(default=None, gt=0)
    confidence_score: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        symbol = value.strip().upper()
        cleaned = symbol.replace("_", "").replace("-", "")
        if not cleaned.isalnum():
            raise ValueError("symbol must contain only letters, numbers, '_' or '-'")
        return symbol
