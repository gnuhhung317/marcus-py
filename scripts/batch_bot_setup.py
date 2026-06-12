#!/usr/bin/env python
from __future__ import annotations

import argparse
import getpass
import json
import os
import random
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import requests

# Add directories to sys.path to ensure correct imports
parent_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(parent_dir))
sys.path.append(str(parent_dir / "src"))

from quant_signal_sdk.client import QuantSignalClient
from quant_signal_sdk.runtime.adapters import DataFrameFeed
from quant_signal_sdk.runtime.backtest import BacktestConfig, PortfolioBacktestRunner
from quant_signal_sdk.runtime.backtest_upload import BacktestUploadClient, BacktestUploadConfig
from sma_cross_strategy import SmaCrossStrategy


def login_developer(backend_url: str, username: str, password: str) -> str:
    """Authenticate with the Marcus backend as a Developer to get access token."""
    login_endpoints = [
        f"{backend_url.rstrip('/')}/api/v1/auth/login",
        f"{backend_url.rstrip('/')}/auth/login"
    ]
    
    last_err = None
    for url in login_endpoints:
        try:
            print(f"Attempting login at: {url}...")
            response = requests.post(
                url,
                json={"username": username, "password": password},
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            return data["accessToken"]
        except Exception as e:
            last_err = e
            continue
            
    raise RuntimeError(f"Failed to login to Marcus backend. Error: {last_err}")


def scan_parquet_files(data_dir: str) -> list[tuple[Path, str, str]]:
    """Scan directory for parquet files and extract base symbol name.
    Returns list of tuples: (file_path, base_symbol, trading_pair)
    """
    path = Path(data_dir)
    if not path.exists():
        print(f"Warning: Data directory {data_dir} does not exist.")
        return []
        
    results = []
    # Match both *.parquet and *.PARQUET
    for filepath in path.glob("*.parquet"):
        stem = filepath.stem
        # Normalize name: remove _USDT suffix
        if stem.endswith("_USDT"):
            stem = stem[:-5]
        
        # Determine symbol name
        if stem.endswith("USDT"):
            base = stem[:-4]
            trading_pair = f"{base}/USDT"
        else:
            base = stem
            trading_pair = f"{base}/USDT"
            
        results.append((filepath, base, trading_pair))
        
    # Sort symbols for determinism
    results.sort(key=lambda x: x[1])
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Marcus Trading Batch Bot Setup and Backtester")
    parser.add_argument("--backend-url", default="https://marcus-api.tromoi.xyz", help="Marcus API URL")
    parser.add_argument("--data-dir", default=r"D:\Code\Projects\self-projects\macd-overlay - Copy\data\ohlcv", help="OHLCV Parquet files directory")
    parser.add_argument("--email", default="dev+e2e1@example.com", help="Developer email/username")
    parser.add_argument("--password", default="Password123!", help="Developer password")
    parser.add_argument("--limit", type=int, default=10, help="Max number of bots to setup")
    parser.add_argument("--exchange", default="binance", help="Exchange ID for bot registration")
    parser.add_argument("--config-file", default="registered_bots.json", help="JSON file to load/save bot configs")
    parser.add_argument("--run-name-prefix", default="SMA-Cross", help="Prefix for backtest run display name")
    
    args = parser.parse_args()
    
    # Resolve credentials
    email = args.email or os.environ.get("MARCUS_DEV_EMAIL")
    password = args.password or os.environ.get("MARCUS_DEV_PASSWORD")
    
    if not email:
        email = input("Enter developer email/username: ").strip()
    if not password:
        password = getpass.getpass("Enter developer password: ").strip()
        
    if not email or not password:
        print("Error: Email and password are required.")
        sys.exit(1)
        
    print(f"Backend URL: {args.backend_url}")
    print(f"Data Directory: {args.data_dir}")
    
    # 1. Login
    try:
        token = login_developer(args.backend_url, email, password)
        print("Login successful!")
    except Exception as e:
        print(f"Login failed: {e}")
        sys.exit(1)
        
    # 2. Scan parquet files
    files = scan_parquet_files(args.data_dir)
    print(f"Found {len(files)} parquet files in {args.data_dir}.")
    if not files:
        print("No parquet data files found. Cannot proceed.")
        sys.exit(1)
        
    # 3. Load existing config
    config_path = Path(args.config_file)
    registered_bots: dict[str, dict[str, str]] = {}
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                registered_bots = json.load(f)
            print(f"Loaded {len(registered_bots)} existing registered bots from {args.config_file}.")
        except Exception as e:
            print(f"Warning: Failed to load config file: {e}")
            
    # Initialize SDK client for registration helper
    sdk_client = QuantSignalClient(base_url=args.backend_url, api_key="")
    
    # Determine which files to process up to limit
    random.shuffle(files)
    to_process = files[:args.limit]
    print(f"Processing up to {len(to_process)} symbols...")
    
    # 4. Register bots
    for idx, (filepath, base_symbol, trading_pair) in enumerate(to_process, start=1):
        if base_symbol in registered_bots:
            print(f"[{idx}/{len(to_process)}] {base_symbol} already registered. Skipping registration.")
            continue
            
        bot_name = f"SmaCross-{base_symbol}"
        payload = {
            "botName": bot_name,
            "description": f"Auto-registered SMA Cross Bot for {trading_pair}",
            "tradingPair": trading_pair,
            "exchange": args.exchange
        }
        
        try:
            print(f"[{idx}/{len(to_process)}] Registering bot for {trading_pair}...")
            res = sdk_client.register_bot(payload, auth_token=token)
            
            bot_id = res["botId"]
            api_key = res["apiKey"]
            raw_secret = res["rawSecret"]
            
            registered_bots[base_symbol] = {
                "botId": bot_id,
                "apiKey": api_key,
                "rawSecret": raw_secret,
                "tradingPair": trading_pair,
                "botName": bot_name
            }
            
            # Save progress immediately
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(registered_bots, f, indent=2)
                
            print(f"  Successfully registered {base_symbol} (Bot ID: {bot_id})")
        except Exception as e:
            print(f"  Failed to register bot for {base_symbol}: {e}")
            
    # 5. Run backtest and upload for all bots
    print("\n--- Starting Backtests and Uploads ---")
    for idx, (filepath, base_symbol, trading_pair) in enumerate(to_process, start=1):
        if base_symbol not in registered_bots:
            print(f"[{idx}/{len(to_process)}] {base_symbol} credentials missing. Skipping backtest.")
            continue
            
        bot_info = registered_bots[base_symbol]
        bot_id = bot_info["botId"]
        api_key = bot_info["apiKey"]
        raw_secret = bot_info["rawSecret"]
        
        print(f"[{idx}/{len(to_process)}] Running backtest for {base_symbol} using {filepath.name}...")
        
        try:
            # Load parquet data
            df = pd.read_parquet(filepath)
            
            # Ensure columns are normalized
            # DataFrameFeed needs timestamp column.
            # Some files might have different names, normalize to lowercase
            df.columns = [str(c).lower() for c in df.columns]
            
            if "timestamp" not in df.columns:
                # Try to find common timestamp columns
                time_cols = [c for c in df.columns if c in ("time", "datetime", "date")]
                if time_cols:
                    df = df.rename(columns={time_cols[0]: "timestamp"})
                else:
                    print(f"  Error: No timestamp column found in {filepath.name}. Columns: {list(df.columns)}")
                    continue
            
            # Convert timestamp to pandas DatetimeIndex/Series
            df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
            
            # Check for other required columns
            required_cols = {"open", "high", "low", "close"}
            missing = required_cols.difference(df.columns)
            if missing:
                print(f"  Error: Missing columns {missing} in {filepath.name}")
                continue
                
            # Run backtest
            feed = DataFrameFeed(df, timestamp_col="timestamp")
            strategy = SmaCrossStrategy(short_window=5, long_window=15)
            config = BacktestConfig(initial_cash=10000.0)
            runner = PortfolioBacktestRunner(feed=feed, strategy=strategy, config=config)
            report = runner.run()
            
            metrics = report.metrics
            if metrics:
                print(f"  Backtest complete. Trades: {metrics.total_trades}, Sharpe: {metrics.sharpe_ratio:.4f}, Return: {metrics.total_return*100:.2f}%")
            else:
                print("  Backtest complete (no metrics generated).")
                
            # Upload results
            print(f"  Uploading results for {base_symbol} (Bot ID: {bot_id}) to backend...")
            upload_config = BacktestUploadConfig(
                base_url=args.backend_url,
                bot_id=bot_id,
                api_key=api_key,
                signer_secret=raw_secret,
                run_name=f"{args.run_name_prefix}-{base_symbol}",
            )
            upload_client = BacktestUploadClient(upload_config)
            upload_client.push_backtest_report(report)
            print(f"  Successfully uploaded {base_symbol} results!")
            
        except Exception as e:
            print(f"  Failed backtest/upload for {base_symbol}: {e}")
            import traceback
            traceback.print_exc()

    print("\nBatch process finished!")
    print(f"Leaderboard is available at: https://marcus-ui.tromoi.xyz/terminal/leaderboard")


if __name__ == "__main__":
    main()
