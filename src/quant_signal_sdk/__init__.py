"""Core public API for quant_signal_sdk.

The package root intentionally stays lightweight. Backtesting, market-data,
feature engineering, and ML helpers remain available from their submodules and
extras, but importing quant_signal_sdk should only require the core SDK deps.
"""
from .client import QuantSignalClient
from .interfaces import BaseDispatcher, BaseFeed, BaseStrategy, MarketEvent, PortfolioContext, Signal
from .models import (
    ExecutionPolicies,
    MarginMode,
    MarketType,
    OrderType,
    SignalAction,
    SignalPayload,
    SignalSide,
    SignalStatus,
)
from .signing import generate_hmac_signature
from .runner import Runner

__all__ = [
    "QuantSignalClient",
    "BaseDispatcher",
    "BaseFeed",
    "BaseStrategy",
    "MarketEvent",
    "PortfolioContext",
    "Signal",
    "Runner",
    "ExecutionPolicies",
    "MarginMode",
    "MarketType",
    "OrderType",
    "SignalAction",
    "SignalPayload",
    "SignalSide",
    "SignalStatus",
    "generate_hmac_signature",
]
