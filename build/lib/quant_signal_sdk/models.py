from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SignalSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class SignalAction(str, Enum):
    OPEN_LONG = "OPEN_LONG"
    OPEN_SHORT = "OPEN_SHORT"
    CLOSE_LONG = "CLOSE_LONG"
    CLOSE_SHORT = "CLOSE_SHORT"
    CLOSE = "CLOSE"


class MarketType(str, Enum):
    SPOT = "SPOT"
    FUTURE = "FUTURE"
    MARGIN = "MARGIN"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class MarginMode(str, Enum):
    CROSS = "CROSS"
    ISOLATED = "ISOLATED"


class SignalStatus(str, Enum):
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    BROADCASTED = "BROADCASTED"
    DISPATCHED = "DISPATCHED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    EXPIRED = "EXPIRED"
    FAILED_DELIVERY = "FAILED_DELIVERY"
    FAILED = "FAILED"


class SignalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    signal_id: str | None = Field(default=None, alias="signalId")
    bot_id: str | None = Field(default=None, alias="botId")
    action: SignalAction
    symbol: str = Field(min_length=2, max_length=24)
    market_type: MarketType = Field(alias="marketType")
    order_type: OrderType = Field(alias="orderType")
    entry: float | None = Field(default=None, gt=0)
    stop_loss: float | None = Field(default=None, gt=0, alias="stopLoss")
    take_profit: float | None = Field(default=None, gt=0, alias="takeProfit")
    amount: float | None = Field(default=None, gt=0)
    leverage: int | None = Field(default=None, ge=1, le=125)
    margin_mode: MarginMode | None = Field(default=None, alias="marginMode")
    reduce_only: bool | None = Field(default=None, alias="reduceOnly")
    status: SignalStatus | None = None
    generated_timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        alias="generatedTimestamp"
    )
    timeframe: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Execution policies: optional strict contract for executor behavior
    policies: Optional["ExecutionPolicies"] = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def handle_legacy_fields(cls, data: Any) -> Any:
        import warnings
        if not isinstance(data, dict):
            return data

        warnings_issued = []

        if "tp" in data:
            warnings_issued.append("tp is deprecated, use take_profit instead")
            data["take_profit"] = data.pop("tp")

        if "sl" in data:
            warnings_issued.append("sl is deprecated, use stop_loss instead")
            data["stop_loss"] = data.pop("sl")

        if "timestamp" in data:
            warnings_issued.append("timestamp is deprecated, use generated_timestamp instead")
            val = data.pop("timestamp")
            if isinstance(val, (int, float)):
                if val > 1e11:
                    dt = datetime.fromtimestamp(val / 1000.0, timezone.utc)
                else:
                    dt = datetime.fromtimestamp(val, timezone.utc)
                data["generated_timestamp"] = dt
            else:
                data["generated_timestamp"] = val

        if "side" in data:
            warnings_issued.append("side is deprecated and has been moved to metadata")
            side_val = data.pop("side")
            if "metadata" not in data or data["metadata"] is None:
                data["metadata"] = {}
            if "side" not in data["metadata"]:
                data["metadata"]["side"] = side_val

        if "confidence_score" in data:
            warnings_issued.append("confidence_score is deprecated and has been moved to metadata")
            conf_val = data.pop("confidence_score")
            if "metadata" not in data or data["metadata"] is None:
                data["metadata"] = {}
            if "confidence_score" not in data["metadata"]:
                data["metadata"]["confidence_score"] = conf_val

        for msg in warnings_issued:
            warnings.warn(msg, DeprecationWarning, stacklevel=2)

        return data

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        symbol = value.strip().upper()
        cleaned = symbol.replace("_", "").replace("-", "")
        if not cleaned.isalnum():
            raise ValueError("symbol must contain only letters, numbers, '_' or '-'")
        return cleaned


class ExecutionPolicies(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # 0-1 fraction (e.g. 0.1 == 10%)
    max_size_percent: float | None = Field(default=None, alias="maxSizePercent", ge=0.0, le=1.0)
    # Absolute UNIX seconds (int). Accepts datetime or ms/seconds numeric in input.
    cancel_order_after: int | None = Field(default=None, alias="cancelOrderAfter")
    close_position_after: int | None = Field(default=None, alias="closePositionAfter")

    @field_validator("cancel_order_after", "close_position_after", mode="before")
    @classmethod
    def _coerce_timestamp(cls, v):
        if v is None:
            return v
        if isinstance(v, datetime):
            return int(v.replace(tzinfo=timezone.utc).timestamp())
        if isinstance(v, (int, float)):
            # Accept milliseconds as large ints (>1e11)
            if v > 1e11:
                return int(v / 1000)
            return int(v)
        raise ValueError("timestamp must be datetime or int/float seconds")
