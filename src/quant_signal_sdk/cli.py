from __future__ import annotations

import argparse
import csv
import json
import importlib
import importlib.util
import inspect
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .data_loader import BundleLoader
from .runtime.adapters import DataFrameFeed
from .runtime.backtest import BacktestConfig, BacktestFill, BacktestOrder, BacktestReport, PortfolioBacktestRunner
from .runtime.backtest_upload import BacktestUploadClient, BacktestUploadConfig
from .runtime.interfaces import BaseStrategy


logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quant-sdk")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backtest = subparsers.add_parser("backtest", help="Run an in-memory portfolio backtest")
    backtest.add_argument("--bot-file", default="my_bot.py", help="Path to a Python bot file or module")
    backtest.add_argument("--bot-object", default=None, help="Strategy class, instance, or attribute name inside the bot module")
    source_group = backtest.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--data-csv", default=None, help="Path to OHLCV CSV data")
    source_group.add_argument("--data-parquet", default=None, help="Path to parquet file or directory containing parquet files")
    source_group.add_argument("--bundle-dir", default=None, help="Path to a bundle directory containing manifest.json and parquet assets")
    backtest.add_argument("--symbol", default=None, help="Asset symbol to load from a bundle manifest")
    backtest.add_argument("--timestamp-column", default=None, help="Optional timestamp column name")
    backtest.add_argument("--initial-cash", type=float, default=0.0, help="Starting cash balance")
    backtest.add_argument("--maker-fee", type=float, default=0.0, help="Maker fee rate")
    backtest.add_argument("--taker-fee", type=float, default=0.0, help="Taker fee rate")
    backtest.add_argument("--slippage", type=float, default=0.0, help="Slippage rate")
    backtest.add_argument("--output-dir", default="backtest_output", help="Directory for CSV/HTML exports")
    backtest.add_argument("--export-html", action="store_true", help="Write a static HTML tear sheet")
    backtest.add_argument("--upload-backtest", action="store_true", help="Upload the completed backtest report to Marcus backend")
    backtest.add_argument("--backend-url", default=None, help="Marcus backend base URL for --upload-backtest")
    backtest.add_argument("--bot-id", default=None, help="Bot id for --upload-backtest")
    backtest.add_argument("--api-key", default=None, help="Bot API key for --upload-backtest")
    backtest.add_argument("--signer-secret", default=None, help="Optional bot signer secret for --upload-backtest")
    backtest.add_argument("--run-name", default=None, help="Optional display name for this backtest run")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.command == "backtest":
        report = run_backtest(args)
        export_backtest_results(report, output_dir=args.output_dir, export_html=args.export_html)
        if args.upload_backtest:
            upload_backtest_report(report, args)
        print(f"cash={report.context.cash:.8f}")
        print(f"realized_pnl={report.context.realized_pnl:.8f}")
        print(f"unrealized_pnl={report.context.unrealized_pnl:.8f}")
        print(f"equity={report.context.equity:.8f}")
        print(f"fills={len(report.fills)}")
        return 0

    raise SystemExit(f"unknown command: {args.command}")


def run_backtest(args: argparse.Namespace):
    strategy = _load_strategy(args.bot_file, args.bot_object)
    if getattr(args, "bundle_dir", None):
        dataframe = _load_bundle_dataframe(args.bundle_dir, args.symbol, args.timestamp_column)
    elif getattr(args, "data_parquet", None):
        dataframe = _load_parquet(args.data_parquet, args.timestamp_column)
    elif getattr(args, "data_csv", None):
        dataframe = pd.read_csv(args.data_csv)
    else:
        raise ValueError("Provide exactly one of --data-csv, --data-parquet, or --bundle-dir.")

    feed = DataFrameFeed(dataframe, timestamp_col=args.timestamp_column or "timestamp")
    config = BacktestConfig(
        initial_cash=args.initial_cash,
        maker_fee_rate=args.maker_fee,
        taker_fee_rate=args.taker_fee,
        slippage_rate=args.slippage,
    )
    runner = PortfolioBacktestRunner(feed=feed, strategy=strategy, config=config)
    return runner.run()


def upload_backtest_report(report: BacktestReport, args: argparse.Namespace) -> dict[str, Any]:
    missing = [
        name
        for name in ("backend_url", "bot_id", "api_key")
        if not getattr(args, name, None)
    ]
    if missing:
        raise ValueError(f"--upload-backtest requires: {', '.join('--' + name.replace('_', '-') for name in missing)}")
    client = BacktestUploadClient(
        BacktestUploadConfig(
            base_url=args.backend_url,
            bot_id=args.bot_id,
            api_key=args.api_key,
            signer_secret=args.signer_secret,
            run_name=args.run_name,
        )
    )
    return client.push_backtest_report(report)


def export_backtest_results(report: BacktestReport, *, output_dir: str, export_html: bool) -> None:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    _export_trades_csv(report, output_path / "trades.csv")
    _export_orders_csv(report, output_path / "orders.csv")
    _export_equity_curve_csv(report, output_path / "equity_curve.csv")
    _export_closed_trades_csv(report, output_path / "closed_trades.csv")
    _export_metrics_json(report, output_path / "metrics.json")
    if export_html:
        _export_tearsheet_html(report, output_path / "tearsheet.html")


def _export_trades_csv(report: BacktestReport, file_path: Path) -> None:
    field_names = ["timestamp", "order_id", "signal_id", "symbol", "market_type", "side", "quantity", "price", "fee", "fee_type"]
    with file_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=field_names)
        writer.writeheader()
        for fill in report.fills:
            writer.writerow({
                "timestamp": fill.timestamp.isoformat(),
                "order_id": fill.order_id,
                "signal_id": fill.signal_id,
                "symbol": fill.symbol,
                "market_type": fill.market_type,
                "side": fill.side,
                "quantity": fill.quantity,
                "price": fill.price,
                "fee": fill.fee,
                "fee_type": fill.fee_type,
            })


def _export_orders_csv(report: BacktestReport, file_path: Path) -> None:
    field_names = ["order_id", "signal_id", "symbol", "market_type", "action", "order_type", "side", "quantity", "limit_price", "status", "created_at", "eligible_at", "cancel_after", "filled_quantity", "fill_price", "fee_paid"]
    with file_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=field_names)
        writer.writeheader()
        for order in report.orders:
            writer.writerow({
                "order_id": order.order_id,
                "signal_id": order.signal.signal_id,
                "symbol": order.symbol,
                "market_type": order.market_type,
                "action": order.action.value,
                "order_type": order.order_type.value,
                "side": order.side,
                "quantity": order.quantity,
                "limit_price": order.limit_price,
                "status": order.status,
                "created_at": order.created_at.isoformat(),
                "eligible_at": order.eligible_at.isoformat(),
                "cancel_after": order.cancel_after.isoformat() if order.cancel_after else None,
                "filled_quantity": order.filled_quantity,
                "fill_price": order.fill_price,
                "fee_paid": order.fee_paid,
            })


def _export_equity_curve_csv(report: BacktestReport, file_path: Path) -> None:
    field_names = ["timestamp", "cash", "unrealized_pnl", "realized_pnl", "total_fees", "equity"]
    with file_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=field_names)
        writer.writeheader()
        for point in report.equity_history:
            writer.writerow({
                "timestamp": point.timestamp.isoformat(),
                "cash": point.cash,
                "unrealized_pnl": point.unrealized_pnl,
                "realized_pnl": point.realized_pnl,
                "total_fees": point.total_fees,
                "equity": point.equity,
            })


def _export_closed_trades_csv(report: BacktestReport, file_path: Path) -> None:
    field_names = ["symbol", "market_type", "side", "entry_timestamp", "exit_timestamp", "quantity", "entry_price", "exit_price", "entry_fees", "exit_fees", "pnl", "duration_seconds"]
    with file_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=field_names)
        writer.writeheader()
        for trade in report.closed_trades:
            writer.writerow({
                "symbol": trade.symbol,
                "market_type": trade.market_type,
                "side": trade.side,
                "entry_timestamp": trade.entry_timestamp.isoformat(),
                "exit_timestamp": trade.exit_timestamp.isoformat(),
                "quantity": trade.quantity,
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "entry_fees": trade.entry_fees,
                "exit_fees": trade.exit_fees,
                "pnl": trade.pnl,
                "duration_seconds": trade.duration_seconds,
            })


def _export_metrics_json(report: BacktestReport, file_path: Path) -> None:
    payload = asdict(report.metrics) if report.metrics is not None else {}
    file_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _export_tearsheet_html(report: BacktestReport, file_path: Path) -> None:
    equity_values = [point.equity for point in report.equity_history]
    equity_timestamps = [point.timestamp.isoformat() for point in report.equity_history]
    candle_rows = report.candle_history
    metric_rows = asdict(report.metrics) if report.metrics is not None else {}

    equity_svg = _line_chart_svg(equity_values, width=900, height=220, stroke="#10b981")
    underwater_values = _underwater_values(equity_values)
    underwater_svg = _line_chart_svg(underwater_values, width=900, height=180, stroke="#ef4444", fill=True)
    candle_svg = _candles_svg(candle_rows, report.fills, width=900, height=320)

    metrics_html = "".join(
        f"<div class='metric'><div class='label'>{key}</div><div class='value'>{value}</div></div>"
        for key, value in metric_rows.items()
    )
    trades_html = "".join(
        f"<tr><td>{trade.symbol}</td><td>{trade.side}</td><td>{trade.entry_timestamp.isoformat()}</td><td>{trade.exit_timestamp.isoformat()}</td><td>{trade.pnl:.6f}</td></tr>"
        for trade in report.closed_trades
    )

    html = f"""<!doctype html>
<html lang='en'>
<head>
  <meta charset='utf-8' />
  <meta name='viewport' content='width=device-width, initial-scale=1' />
  <title>Backtest Tear Sheet</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; background: #0b1220; color: #e5e7eb; }}
    h1, h2 {{ margin: 0 0 12px 0; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }}
    .metric {{ background: #111827; border: 1px solid #1f2937; border-radius: 10px; padding: 12px; }}
    .label {{ font-size: 12px; color: #9ca3af; text-transform: uppercase; }}
    .value {{ font-size: 20px; font-weight: 700; margin-top: 4px; }}
    .card {{ background: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 16px; margin-top: 18px; }}
    svg {{ width: 100%; height: auto; display: block; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
    th, td {{ border-bottom: 1px solid #1f2937; padding: 8px; text-align: left; font-size: 13px; }}
    th {{ color: #9ca3af; }}
  </style>
</head>
<body>
  <h1>Backtest Tear Sheet</h1>
  <div class='grid'>{metrics_html}</div>
  <div class='card'>
    <h2>Equity Curve</h2>
    {equity_svg}
  </div>
  <div class='card'>
    <h2>Underwater Plot</h2>
    {underwater_svg}
  </div>
  <div class='card'>
    <h2>Candles</h2>
    {candle_svg}
  </div>
  <div class='card'>
    <h2>Closed Trades</h2>
    <table><thead><tr><th>Symbol</th><th>Side</th><th>Entry</th><th>Exit</th><th>PnL</th></tr></thead><tbody>{trades_html}</tbody></table>
  </div>
  <div class='card'>
    <h2>Equity Points</h2>
    <div style='font-size:12px;color:#9ca3af'>Points: {len(equity_timestamps)}</div>
  </div>
</body>
</html>"""
    file_path.write_text(html, encoding="utf-8")


def _line_chart_svg(values: list[float], *, width: int, height: int, stroke: str, fill: bool = False) -> str:
    if not values:
        return f"<svg viewBox='0 0 {width} {height}'></svg>"
    minimum = min(values)
    maximum = max(values)
    if minimum == maximum:
        maximum = minimum + 1.0
    points = []
    for index, value in enumerate(values):
        x = 20 + (width - 40) * (index / max(len(values) - 1, 1))
        y = 20 + (height - 40) * (1.0 - (value - minimum) / (maximum - minimum))
        points.append(f"{x:.2f},{y:.2f}")
    if fill:
        path = f"M 20,{height-20} L {' L '.join(points)} L {width-20},{height-20} Z"
        return f"<svg viewBox='0 0 {width} {height}'><path d='{path}' fill='{stroke}' opacity='0.15' stroke='none' /><polyline points='{' '.join(points)}' fill='none' stroke='{stroke}' stroke-width='2' /></svg>"
    return f"<svg viewBox='0 0 {width} {height}'><polyline points='{' '.join(points)}' fill='none' stroke='{stroke}' stroke-width='2' /></svg>"


def _underwater_values(values: list[float]) -> list[float]:
    underwater: list[float] = []
    running_max = values[0] if values else 0.0
    for value in values:
        running_max = max(running_max, value)
        underwater.append((value - running_max) / running_max if running_max > 0 else 0.0)
    return underwater


def _candles_svg(candle_rows: list[dict[str, Any]], fills: list[BacktestFill], *, width: int, height: int) -> str:
    if not candle_rows:
        return f"<svg viewBox='0 0 {width} {height}'></svg>"

    highs = [float(row["high"]) for row in candle_rows]
    lows = [float(row["low"]) for row in candle_rows]
    minimum = min(lows)
    maximum = max(highs)
    if minimum == maximum:
        maximum = minimum + 1.0
    candle_width = max((width - 40) / max(len(candle_rows), 1) * 0.6, 2.0)

    def scale(value: float) -> float:
        return 20 + (height - 40) * (1.0 - (value - minimum) / (maximum - minimum))

    fill_points: list[str] = []
    for index, fill in enumerate(fills):
        candle_index = min(index, len(candle_rows) - 1)
        row = candle_rows[candle_index]
        x = 20 + (width - 40) * (candle_index / max(len(candle_rows) - 1, 1))
        y = scale(float(fill.price))
        color = "#10b981" if fill.side == "BUY" else "#ef4444"
        fill_points.append(f"<circle cx='{x:.2f}' cy='{y:.2f}' r='4' fill='{color}' />")

    candle_shapes: list[str] = []
    for index, row in enumerate(candle_rows):
        x = 20 + (width - 40) * (index / max(len(candle_rows) - 1, 1))
        open_y = scale(float(row["open"]))
        close_y = scale(float(row["close"]))
        high_y = scale(float(row["high"]))
        low_y = scale(float(row["low"]))
        body_top = min(open_y, close_y)
        body_height = max(abs(close_y - open_y), 1.0)
        body_color = "#10b981" if float(row["close"]) >= float(row["open"]) else "#ef4444"
        candle_shapes.append(f"<line x1='{x:.2f}' y1='{high_y:.2f}' x2='{x:.2f}' y2='{low_y:.2f}' stroke='#94a3b8' stroke-width='1' />")
        candle_shapes.append(f"<rect x='{x - candle_width / 2:.2f}' y='{body_top:.2f}' width='{candle_width:.2f}' height='{body_height:.2f}' fill='{body_color}' opacity='0.8' />")

    return f"<svg viewBox='0 0 {width} {height}'>{''.join(candle_shapes)}{''.join(fill_points)}</svg>"


def _load_strategy(bot_file: str, bot_object: str | None) -> BaseStrategy:
    module = _load_module(bot_file)
    candidate = None

    if bot_object is not None:
        candidate = getattr(module, bot_object)
    else:
        candidate = getattr(module, "STRATEGY", None)
        if candidate is None:
            candidate = _find_strategy_candidate(module)

    if candidate is None:
        raise ValueError("No strategy object found. Export STRATEGY or pass --bot-object.")

    if inspect.isclass(candidate):
        return candidate()
    return candidate


def _load_module(bot_file: str):
    path = Path(bot_file)
    if path.exists():
        module_name = path.stem
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Unable to load bot file: {bot_file}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return importlib.import_module(bot_file)


def _find_strategy_candidate(module: Any):
    for _, value in vars(module).items():
        if inspect.isclass(value) and hasattr(value, "on_event"):
            return value
        if hasattr(value, "on_event") and callable(getattr(value, "on_event")):
            return value
    return None


def _load_parquet(path: str, timestamp_column: str | None) -> pd.DataFrame:
    p = Path(path)
    if p.is_dir():
        candidates = sorted(p.glob("*.parquet"))
        if not candidates:
            raise ValueError(f"No parquet files found in directory: {path}")
        p = candidates[0]
    if not p.exists():
        raise ValueError(f"Parquet file not found: {p}")
    try:
        df = pd.read_parquet(p)
    except Exception as exc:
        raise RuntimeError(f"Failed to read parquet file {p}: {exc}") from exc
    if timestamp_column is not None and timestamp_column in df.columns:
        df[timestamp_column] = pd.to_datetime(df[timestamp_column], errors="coerce", utc=True)
    return df


def _load_bundle_dataframe(bundle_dir: str, symbol: str | None, timestamp_column: str | None) -> pd.DataFrame:
    loader = BundleLoader(bundle_dir)
    asset_symbol = symbol or loader.manifest.first_symbol
    if asset_symbol is None:
        raise ValueError(f"Bundle manifest does not define any assets: {bundle_dir}")

    asset = loader.manifest.get_asset(asset_symbol)
    raw_asset_data = loader.load_raw_asset_data(asset_symbol)
    return _merge_bundle_streams(
        raw_asset_data,
        timestamp_column=timestamp_column or "timestamp",
        column_mapping=asset.column_mapping,
    )


def _merge_bundle_streams(
    raw_asset_data: dict[str, pd.DataFrame],
    *,
    timestamp_column: str,
    column_mapping: dict[str, str] | None = None,
) -> pd.DataFrame:
    if not raw_asset_data:
        raise ValueError("Bundle asset has no data streams to merge.")

    prepared_frames: list[pd.DataFrame] = []
    mapping = dict(column_mapping or {})
    for stream_name, frame in raw_asset_data.items():
        normalized = _normalize_stream_frame(frame, timestamp_column=timestamp_column)
        if mapping:
            columns_to_keep = [timestamp_column]
            rename_map: dict[str, str] = {}
            for column in normalized.columns:
                if column == timestamp_column:
                    continue
                mapped_name = mapping.get(column)
                if mapped_name is None:
                    continue
                columns_to_keep.append(column)
                rename_map[column] = mapped_name
            if len(columns_to_keep) == 1:
                raise ValueError(
                    f"No mapped columns found for stream '{stream_name}'. Provide column_mapping entries in manifest.json."
                )
            normalized = normalized.loc[:, columns_to_keep].rename(columns=rename_map)
        elif len(raw_asset_data) > 1:
            raise ValueError(
                "Multi-stream bundle assets require manifest column_mapping entries to produce a flat payload."
            )
        prepared_frames.append(normalized)

    ordered_frames = sorted(prepared_frames, key=len, reverse=True)
    merged = ordered_frames[0].sort_values(timestamp_column).reset_index(drop=True)
    for frame in ordered_frames[1:]:
        merged = pd.merge_asof(
            merged.sort_values(timestamp_column),
            frame.sort_values(timestamp_column),
            on=timestamp_column,
            direction="backward",
        )

    return merged.ffill().reset_index(drop=True)


def _normalize_stream_frame(frame: pd.DataFrame, *, timestamp_column: str) -> pd.DataFrame:
    normalized = frame.copy()

    if timestamp_column in normalized.columns:
        pass
    elif isinstance(normalized.index, pd.DatetimeIndex):
        normalized.index.name = timestamp_column
        normalized = normalized.reset_index()
    else:
        raise ValueError(
            f"Stream data must contain a '{timestamp_column}' column or use a DatetimeIndex; "
            f"found columns={list(normalized.columns)}"
        )

    normalized[timestamp_column] = pd.to_datetime(normalized[timestamp_column], errors="coerce", utc=True)
    normalized = normalized.dropna(subset=[timestamp_column]).sort_values(timestamp_column).reset_index(drop=True)
    return normalized
