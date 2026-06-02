from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quant_signal_sdk.cli import export_backtest_results
from quant_signal_sdk.funding_pipeline import (
    QuantFeatureEngineer,
    add_target_relevance,
    build_master_df,
    load_ranker_bundle,
    predict_scores,
    resolve_data_paths,
    resolve_symbols,
    save_ranker_bundle,
    train_ranker_models,
)
from quant_signal_sdk.runtime.adapters import DataFrameFeed
from quant_signal_sdk.models import OrderType
from quant_signal_sdk.runtime.backtest import BacktestConfig, BacktestFill, BacktestOrder, EquityPoint, PortfolioBacktestRunner
from quant_signal_sdk.runtime.interfaces import MarketEvent, PortfolioContext

from funding_carry_bot import FundingCarryConfig, FundingCarryStrategy


@dataclass(slots=True)
class FundingBacktestConfig:
    funding_hours: tuple[int, ...] = (0, 8, 16)
    funding_rate_col: str = "funding_rate"
    price_col: str = "close"
    spot_fee_rate: float = 0.001
    future_fee_rate: float = 0.0002


class FundingBacktestRunner(PortfolioBacktestRunner):
    def __init__(
        self,
        *,
        feed: DataFrameFeed,
        strategy: FundingCarryStrategy,
        config: BacktestConfig,
        funding_config: FundingBacktestConfig | None = None,
    ) -> None:
        super().__init__(feed=feed, strategy=strategy, config=config)
        self._funding_config = funding_config or FundingBacktestConfig()
        self._last_funding: dict[str, datetime] = {}

    def _process_event(self, event: MarketEvent) -> None:
        super()._process_event(event)
        self._apply_funding(event)

    def _fill_order(self, order_id: str, order: BacktestOrder, price: float, current_timestamp: datetime, fee_type: str) -> None:
        fee_rate = self._resolve_fee_rate(order)
        fee = abs(order.quantity * price) * fee_rate
        quantity = abs(order.quantity)
        signed_quantity = quantity if order.side == "BUY" else -quantity
        self._apply_fill(order, price, fee, signed_quantity)
        order.status = "FILLED"
        order.filled_quantity = quantity
        order.fill_price = price
        order.fee_paid = fee
        self._fills.append(
            BacktestFill(
                order_id=order.order_id,
                signal_id=order.signal.signal_id,
                symbol=order.symbol,
                market_type=order.market_type,
                action=order.action,
                side=order.side,
                quantity=quantity,
                price=price,
                fee=fee,
                timestamp=current_timestamp,
                fee_type=fee_type,
            )
        )
        self._open_orders.pop(order_id, None)

    def _resolve_fee_rate(self, order: BacktestOrder) -> float:
        market_type = str(order.market_type).upper()
        if market_type == "SPOT":
            return self._funding_config.spot_fee_rate
        if market_type == "FUTURE":
            return self._funding_config.future_fee_rate
        return self._config.taker_fee_rate

    def _apply_funding(self, event: MarketEvent) -> None:
        if event.timestamp.hour not in self._funding_config.funding_hours:
            return

        symbol = _normalize_symbol(event.payload.get("symbol"))
        if not symbol:
            return

        last_seen = self._last_funding.get(symbol)
        if last_seen is not None and last_seen == event.timestamp:
            return

        rate = _safe_float(event.payload.get(self._funding_config.funding_rate_col))
        if rate == 0.0:
            return

        price = _safe_float(event.payload.get(self._funding_config.price_col))
        if price <= 0.0:
            return

        cash = self._context.cash
        realized = self._context.realized_pnl
        for position in self._context.positions.values():
            if not isinstance(position, dict):
                continue
            if position.get("market_type") != "FUTURE":
                continue
            if _normalize_symbol(position.get("symbol")) != symbol:
                continue

            qty = float(position.get("net_quantity") or position.get("quantity") or 0.0)
            if qty == 0.0:
                continue

            notional = abs(qty) * price
            sign = -1.0 if qty > 0.0 else 1.0
            funding_pnl = notional * rate * sign
            cash += funding_pnl
            realized += funding_pnl

        if cash != self._context.cash or realized != self._context.realized_pnl:
            self._context = PortfolioContext(
                positions=self._copy_mapping(self._context.positions),
                cash=cash,
                open_orders=self._copy_mapping(self._open_orders),
                realized_pnl=realized,
                unrealized_pnl=self._context.unrealized_pnl,
                total_fees=self._context.total_fees,
                equity=cash + self._context.unrealized_pnl,
                timestamp=event.timestamp,
            )
            if self._equity_history and self._equity_history[-1].timestamp == event.timestamp:
                last = self._equity_history[-1]
                self._equity_history[-1] = EquityPoint(
                    timestamp=last.timestamp,
                    cash=self._context.cash,
                    unrealized_pnl=self._context.unrealized_pnl,
                    realized_pnl=self._context.realized_pnl,
                    total_fees=self._context.total_fees,
                    equity=self._context.equity,
                )

        self._last_funding[symbol] = event.timestamp


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Funding carry trade backtest")
    parser.add_argument("--data-root", default=r"D:\\Code\\Projects\\self-projects\\macd-overlay - Copy\\data")
    parser.add_argument("--oi-dir", default=None)
    parser.add_argument("--funding-dir", default=None)
    parser.add_argument("--symbols", default="", help="Comma separated symbols (default: auto-discover)")
    parser.add_argument("--max-symbols", type=int, default=50)
    parser.add_argument("--lookback-windows", default="12,24,72,168")
    parser.add_argument("--target-horizon", type=int, default=168)
    parser.add_argument("--split-ratio", type=float, default=0.8)
    parser.add_argument("--train", action="store_true", help="Force retraining even if a bundle exists")
    parser.add_argument("--model-dir", default="models/funding_ranker")
    parser.add_argument("--n-splits", type=int, default=5)
    parser.add_argument("--gap-hours", type=int, default=168)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--hold-hours", type=int, default=168)
    parser.add_argument("--min-funding-rate", type=float, default=0.0)
    parser.add_argument("--target-notional", type=float, default=100.0)
    parser.add_argument("--initial-cash", type=float, default=10000.0)
    parser.add_argument("--spot-fee", type=float, default=0.001)
    parser.add_argument("--future-fee", type=float, default=0.0002)
    parser.add_argument("--slippage", type=float, default=0.0)
    parser.add_argument("--rebalance-mode", choices=["fixed", "adaptive"], default="fixed")
    parser.add_argument("--alpha-threshold", type=float, default=0.0)
    parser.add_argument("--order-type", choices=["market", "limit"], default="limit")
    parser.add_argument("--output-dir", default="backtest_output_funding")
    parser.add_argument("--start", default=None, help="Filter start timestamp (e.g. 2024-01-01)")
    parser.add_argument("--end", default=None, help="Filter end timestamp (e.g. 2024-12-31)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = resolve_data_paths(args.data_root, args.oi_dir, args.funding_dir)
    symbols = resolve_symbols(paths.ohlcv_dir, args.symbols, args.max_symbols)
    if not symbols:
        raise SystemExit("No symbols found. Check --data-root or --symbols.")

    master_df = build_master_df(paths, symbols)
    if master_df.empty:
        raise SystemExit("Master dataset is empty. Check your data paths.")

    windows = [int(value.strip()) for value in args.lookback_windows.split(",") if value.strip()]
    engineer = QuantFeatureEngineer(target_horizon_hours=args.target_horizon, lookback_windows=windows)
    engineered_df = engineer.fit_transform(master_df)

    train_ready_df = engineered_df.dropna(subset=["target_sum_funding"]).copy()
    train_ready_df = train_ready_df.replace([np.inf, -np.inf], np.nan).dropna()
    train_ready_df = add_target_relevance(train_ready_df, target_col="target_sum_funding")

    bundle_dir = Path(args.model_dir)
    bundle_path = bundle_dir / "bundle.json"
    if args.train or not bundle_path.exists():
        models = train_ranker_models(
            train_ready_df,
            feature_cols=engineer.feature_cols,
            target_col="target_relevance",
            n_splits=args.n_splits,
            gap_hours=args.gap_hours,
        )
        save_ranker_bundle(
            bundle_dir,
            models=models,
            feature_cols=engineer.feature_cols,
            metadata={
                "lookback_windows": windows,
                "target_horizon": args.target_horizon,
                "symbols": symbols,
            },
        )

    models, feature_cols, _metadata = load_ranker_bundle(bundle_dir)

    oos_df = split_oos(train_ready_df, split_ratio=args.split_ratio)
    scored_df = predict_scores(models, oos_df, feature_cols)
    scored_df = filter_by_date(scored_df, args.start, args.end)
    scored_df = scored_df.sort_values(["timestamp", "symbol"]).reset_index(drop=True)

    feed = DataFrameFeed(scored_df, timestamp_col="timestamp")
    order_type = OrderType.LIMIT if args.order_type == "limit" else OrderType.MARKET
    strategy = FundingCarryStrategy(
        FundingCarryConfig(
            top_k=args.top_k,
            target_notional=args.target_notional,
            hold_hours=args.hold_hours,
            min_funding_rate=args.min_funding_rate,
            score_col="predicted_score",
            rebalance_mode=args.rebalance_mode,
            fee_rate=args.spot_fee,
            alpha_threshold=args.alpha_threshold,
            order_type=order_type,
        )
    )

    config = BacktestConfig(
        initial_cash=args.initial_cash,
        maker_fee_rate=0.0,
        taker_fee_rate=0.0,
        slippage_rate=args.slippage,
    )

    funding_config = FundingBacktestConfig(
        spot_fee_rate=args.spot_fee,
        future_fee_rate=args.future_fee,
    )

    report = FundingBacktestRunner(feed=feed, strategy=strategy, config=config, funding_config=funding_config).run()
    export_backtest_results(report, output_dir=args.output_dir, export_html=True)

    print(f"symbols={len(symbols)}")
    print(f"final_equity={report.context.equity:.6f}")
    print(f"realized_pnl={report.context.realized_pnl:.6f}")
    print(f"fills={len(report.fills)}")
    print(f"model_dir={bundle_dir.resolve()}")
    print(f"outputs={Path(args.output_dir).resolve()}")

    return 0


def split_oos(df: pd.DataFrame, split_ratio: float) -> pd.DataFrame:
    unique_times = df["timestamp"].unique()
    split_idx = int(len(unique_times) * split_ratio)
    oos_times = unique_times[split_idx:]
    return df[df["timestamp"].isin(oos_times)].copy()


def filter_by_date(df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    if start:
        start_ts = pd.to_datetime(start, utc=True)
        df = df[df["timestamp"] >= start_ts]
    if end:
        end_ts = pd.to_datetime(end, utc=True)
        df = df[df["timestamp"] <= end_ts]
    return df


def _normalize_symbol(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("/", "").replace("-", "").replace("_", "").split(":")[0].upper()


def _safe_float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    if result != result:
        return 0.0
    return result


if __name__ == "__main__":
    raise SystemExit(main())