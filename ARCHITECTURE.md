# Architecture Documentation: bot-framework-python

## 1. Overview
The `bot-framework-python` SDK is a lightweight Python library designed to facilitate communication between trading bots and a backend signaling API. It focuses on three core pillars: **Data Standardization** (Models), **Secure Authentication** (Signing), and **Reliable Delivery** (Network).

## 2. Core Components

### 2.1 QuantSignalClient (`src/quant_signal_sdk/client.py`)
The primary entry point for the SDK. It orchestrates the process of:
- Registering bots with the backend.
- Preparing signals for transmission.
- Coordinating with the signing and network modules to securely send data.

### 2.2 Models (`src/quant_signal_sdk/models.py`)
Utilizes `pydantic` for strict data validation.
- **`SignalPayload`**: Ensures that every signal sent (Symbol, Side, Action, etc.) adheres to the required backend contract.
- **Enums**: Defines standardized values for `SignalAction`, `SignalSide`, and `SignalType`.

### 2.3 Signing (`src/quant_signal_sdk/signing.py`)
Handles HMAC-SHA256 signature generation.
- **Canonicalization**: Ensures JSON payloads are consistently formatted before signing to prevent verification failures due to whitespace or key ordering.
- **Security**: Protects signal integrity and authenticates the bot to the backend.

### 2.4 Network (`src/quant_signal_sdk/network.py`)
A robust HTTP client wrapper using `requests`.
- **Retry Logic**: Implements intelligent retry mechanisms to handle transient network issues.
- **Consistency**: Provides a unified interface for all HTTP communication within the SDK.

### 2.5 Strategy (`src/quant_signal_sdk/strategy.py`)
Provides the `BaseStrategy` abstract class, allowing developers to implement custom trading logic while leveraging the SDK's built-in signal delivery capabilities.

### 2.6 CCXT Integration (`src/quant_signal_sdk/ccxt_client.py`)
An optional module providing pre-integrated support for the `ccxt` library, enabling bots to easily fetch market data from various exchanges.

## 3. Main Execution Flow

1.  **Initialization**: The bot initializes `QuantSignalClient` with its API Key and Secret.
2.  **Data Acquisition**: The bot (potentially using `CCXTClient`) fetches market data (OHLCV).
3.  **Signal Generation**: A `BaseStrategy` implementation processes the market data and produces a `SignalPayload`.
4.  **Validation**: The `SignalPayload` is validated by Pydantic.
5.  **Signing**: `QuantSignalClient` passes the payload to the `Signing` module, which generates a timestamped HMAC signature.
6.  **Transmission**: The signed payload is passed to the `NetworkClient`, which performs an authenticated POST request to the Backend API.

## 4. Engineering Standards & Insights
- **Type Safety**: Heavy use of type hints and Pydantic models.
- **Minimal Dependencies**: The core SDK is lightweight, with `ccxt` as an optional "extra".
- **Extensibility**: Designed with inheritance in mind (`BaseStrategy`, `NetworkClient`) to allow for custom overrides.
- **Contract Driven**: Directly tested against backend contract fixtures (`tests/fixtures/contracts/`) to ensure ongoing compatibility.
