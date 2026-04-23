"""Public API for quant_signal_sdk.

The package intentionally exposes a minimal surface area: `QuantSignalClient`,
the `SignalPayload` model and enums, `BaseStrategy`, and the `generate_hmac_signature`
helper. Example/demo bots and heavy deps (CCXT usage) live under `examples/`.
"""
from .client import QuantSignalClient
from .models import SignalAction, SignalPayload, SignalSide
from .signing import generate_hmac_signature
from .strategy import BaseStrategy

__all__ = [
    "QuantSignalClient",
    "SignalAction",
    "SignalPayload",
    "SignalSide",
    "generate_hmac_signature",
    "BaseStrategy",
]
