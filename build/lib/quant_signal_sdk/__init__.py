"""Public API for quant_signal_sdk.

The package intentionally exposes a minimal surface area: `QuantSignalClient`,
the `SignalPayload` model and enums, `BaseStrategy`, and the `generate_hmac_signature`
helper. Example/demo bots and heavy deps (CCXT usage) live under `examples/`.
"""
from .client import QuantSignalClient
from .models import (
    MarginMode,
    MarketType,
    OrderType,
    ExecutionPolicies,
    SignalAction,
    SignalPayload,
    SignalSide,
    SignalStatus,
)
from .signing import generate_hmac_signature
from .strategy import BaseStrategy
from .data_provider import DataProvider, CcxtDataProvider
from .feature_engineer import FeatureEngineer
from .interfaces import BaseDispatcher, BaseFeed, MarketEvent, PortfolioContext, Signal
from .runner import Runner
from .adapters import BaseTrigger, CronTrigger, DataFrameFeed, IntervalTrigger, LiveHTTPDispatcher, LiveRESTFeed, MockDispatcher, ParquetReplayFeed, ScheduledRESTFeed
from .runtime.backtest import BacktestConfig, BacktestFill, BacktestOrder, BacktestReport, OhlcvReplayFeed, PortfolioBacktestRunner
from .core_strategy import FundingArbitrageConfig, FundingArbitrageStrategy
from .data_loader import BundleLoader, BundleManifest
from .translator import SignalTranslator, BoundaryValidationException, RiskManager, PercentageRiskManager

__all__ = [
    "QuantSignalClient",
    "MarginMode",
    "MarketType",
    "OrderType",
    "ExecutionPolicies",
    "SignalAction",
    "SignalPayload",
    "SignalSide",
    "SignalStatus",
    "generate_hmac_signature",
    "BaseStrategy",
    "DataProvider",
    "CcxtDataProvider",
    "FeatureEngineer",
    "BaseDispatcher",
    "BaseFeed",
    "BaseStrategy",
    "MarketEvent",
    "PortfolioContext",
    "Signal",
    "Runner",
    "BacktestConfig",
    "BacktestFill",
    "BacktestOrder",
    "BacktestReport",
    "BaseTrigger",
    "DataFrameFeed",
    "CronTrigger",
    "OhlcvReplayFeed",
    "IntervalTrigger",
    "PortfolioBacktestRunner",
    "LiveHTTPDispatcher",
    "LiveRESTFeed",
    "MockDispatcher",
    "ParquetReplayFeed",
    "ScheduledRESTFeed",
    "BundleLoader",
    "BundleManifest",
    "FundingArbitrageConfig",
    "FundingArbitrageStrategy",
    "SignalTranslator",
    "BoundaryValidationException",
    "RiskManager",
    "PercentageRiskManager",
]
