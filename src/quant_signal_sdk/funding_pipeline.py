from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import joblib


OI_COLUMNS = {
    "sum_open_interest": ["sum_open_interest"],
    "top_ls_ratio": ["top_ls_ratio"],
    "global_ls_ratio": ["global_ls_ratio"],
    "oi_change_1h": ["oi_change_1h"],
    "oi_change_24h": ["oi_change_24h"],
}


@dataclass(slots=True)
class DataPaths:
    data_root: Path
    ohlcv_dir: Path
    oi_dir: Path
    funding_dir: Path


def resolve_data_paths(data_root: str | Path, oi_dir: str | None = None, funding_dir: str | None = None) -> DataPaths:
    root = Path(data_root).expanduser().resolve()
    ohlcv_dir = root / "ohlcv"
    if oi_dir:
        oi_path = Path(oi_dir).expanduser().resolve()
    else:
        oi_path = root / "derivatives"
        if not oi_path.exists():
            alt = root / "data"
            oi_path = alt if alt.exists() else oi_path

    funding_path = Path(funding_dir).expanduser().resolve() if funding_dir else root / "funding"
    return DataPaths(data_root=root, ohlcv_dir=ohlcv_dir, oi_dir=oi_path, funding_dir=funding_path)


def resolve_symbols(ohlcv_dir: Path, raw_symbols: str, max_symbols: int) -> list[str]:
    if raw_symbols.strip():
        return [normalize_symbol(sym) for sym in raw_symbols.split(",") if sym.strip()]

    symbols: list[str] = []
    for path in sorted(ohlcv_dir.glob("*.parquet")):
        symbol = normalize_symbol(path.stem.split("_")[0])
        if symbol:
            symbols.append(symbol)
        if max_symbols and len(symbols) >= max_symbols:
            break
    return symbols


def build_master_df(paths: DataPaths, symbols: list[str]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for symbol in symbols:
        frame = load_symbol_frame(paths.ohlcv_dir, paths.oi_dir, paths.funding_dir, symbol)
        if not frame.empty and not frame.isna().all().all():
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    master = pd.concat(frames, ignore_index=True)
    master["symbol"] = master["symbol"].astype("category")
    return master


def load_symbol_frame(ohlcv_dir: Path, oi_dir: Path, funding_dir: Path, symbol: str) -> pd.DataFrame:
    ohlcv_path = resolve_parquet(ohlcv_dir, symbol)
    if ohlcv_path is None:
        raise FileNotFoundError(f"Missing OHLCV file for {symbol}")

    df = pd.read_parquet(ohlcv_path, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.floor("h")
    df = df.drop_duplicates("timestamp", keep="last").set_index("timestamp")

    df = _join_oi(df, oi_dir, symbol)
    df = _join_funding(df, funding_dir, symbol)

    df = df.reset_index()
    df["symbol"] = symbol
    df["spot_symbol"] = symbol
    df["futures_symbol"] = symbol
    return df


def _join_oi(df: pd.DataFrame, oi_dir: Path, symbol: str) -> pd.DataFrame:
    oi_path = resolve_parquet(oi_dir, symbol, prefer_suffix="_USDT")
    if oi_path is None or not oi_path.exists():
        for col in OI_COLUMNS:
            df[col] = np.nan
        return df

    oi_cols: list[str] = []
    rename_map: dict[str, str] = {}
    available = set(pd.read_parquet(oi_path, engine="pyarrow").columns)

    for standard_name, variants in OI_COLUMNS.items():
        match = next((col for col in available if col.lower() in variants), None)
        if match:
            oi_cols.append(match)
            rename_map[match] = standard_name

    if not oi_cols:
        for col in OI_COLUMNS:
            df[col] = np.nan
        return df

    oi_df = pd.read_parquet(oi_path, columns=["timestamp"] + oi_cols, engine="pyarrow")
    oi_df = oi_df.rename(columns=rename_map)
    oi_df["timestamp"] = pd.to_datetime(oi_df["timestamp"], utc=True).dt.floor("h")
    oi_df = oi_df.drop_duplicates("timestamp", keep="last").set_index("timestamp")

    df = df.join(oi_df, how="left")
    for col in OI_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan
    for col in rename_map.values():
        df[col] = df[col].ffill()
    return df


def _join_funding(df: pd.DataFrame, funding_dir: Path, symbol: str) -> pd.DataFrame:
    funding_path = resolve_parquet(funding_dir, symbol, prefer_suffix="_USDT")
    if funding_path is None:
        df["funding_rate"] = 0.0
        return df

    funding = pd.read_parquet(funding_path)
    col = find_column(funding.columns, {"funding_rate", "fundingrate"})
    if col is None:
        df["funding_rate"] = 0.0
        return df

    funding = funding[["timestamp", col]].rename(columns={col: "funding_rate"})
    funding["timestamp"] = pd.to_datetime(funding["timestamp"], utc=True).dt.floor("h")
    funding = funding.groupby("timestamp", as_index=False)["funding_rate"].sum().set_index("timestamp")

    df = df.join(funding, how="left")
    df["funding_rate"] = df["funding_rate"].fillna(0.0)
    return df


class QuantFeatureEngineer:
    def __init__(self, target_horizon_hours: int = 168, lookback_windows: Iterable[int] | None = None) -> None:
        self.target_horizon = target_horizon_hours
        self.windows = list(lookback_windows or [12, 24, 72, 168])
        self.feature_cols: list[str] = []

    def fit_transform(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
        grouped = df.groupby("symbol", observed=False)

        df["target_sum_funding"] = grouped["funding_rate"].transform(
            lambda series: series.rolling(self.target_horizon).sum().shift(-self.target_horizon)
        )

        for window in self.windows:
            df[f"roc_close_{window}h"] = grouped["close"].pct_change(window)
            df[f"roc_volume_{window}h"] = grouped["volume"].transform(lambda series: np.log1p(series).diff(window))
            df[f"roc_oi_{window}h"] = grouped["sum_open_interest"].pct_change(window, fill_method=None)

        df["ret_1h"] = grouped["close"].pct_change(1)
        for window in self.windows:
            df[f"volatility_{window}h"] = grouped["ret_1h"].transform(lambda series: series.rolling(window).std())
            df[f"sum_funding_{window}h"] = grouped["funding_rate"].transform(lambda series: series.rolling(window).sum())
            df[f"mean_funding_{window}h"] = grouped["funding_rate"].transform(lambda series: series.rolling(window).mean())
            if "ls_divergence" not in df.columns:
                df["ls_divergence"] = df["top_ls_ratio"] - df["global_ls_ratio"]
            df[f"mean_ls_div_{window}h"] = grouped["ls_divergence"].transform(lambda series: series.rolling(window).mean())

        if "roc_oi_24h" in df.columns:
            df["cross_zscore_roc_oi_24h"] = self._cross_sectional_zscore(df, "roc_oi_24h")
        if "sum_funding_168h" in df.columns:
            df["cross_zscore_sum_funding_168h"] = self._cross_sectional_zscore(df, "sum_funding_168h")
        if "roc_close_24h" in df.columns:
            df["cross_zscore_roc_close_24h"] = self._cross_sectional_zscore(df, "roc_close_24h")

        df = df.drop(columns=["ret_1h"])

        base_cols = {
            "timestamp",
            "symbol",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "sum_open_interest",
            "top_ls_ratio",
            "global_ls_ratio",
            "oi_change_1h",
            "oi_change_24h",
            "funding_rate",
            "ls_divergence",
            "spot_symbol",
            "futures_symbol",
        }
        self.feature_cols = [col for col in df.columns if col not in base_cols and col != "target_sum_funding"]

        df = df.dropna(subset=self.feature_cols).reset_index(drop=True)
        return df

    @staticmethod
    def _cross_sectional_zscore(df: pd.DataFrame, col_name: str) -> pd.Series:
        return df.groupby("timestamp")[col_name].transform(
            lambda series: (series - series.mean()) / (series.std() + 1e-8)
        )


@dataclass(slots=True)
class RankerBundle:
    model_paths: list[str]
    feature_cols: list[str]
    metadata: dict[str, Any]


def add_target_relevance(df: pd.DataFrame, target_col: str = "target_sum_funding") -> pd.DataFrame:
    df = df.copy()
    df["target_relevance"] = df.groupby("timestamp")[target_col].transform(
        lambda series: (series.rank(pct=True) * 4.999).astype(int)
    )
    return df


def train_ranker_models(
    df: pd.DataFrame,
    *,
    feature_cols: list[str],
    target_col: str = "target_relevance",
    n_splits: int = 5,
    gap_hours: int = 168,
    random_state: int = 42,
) -> list[Any]:
    try:
        from sklearn.model_selection import TimeSeriesSplit
        import lightgbm as lgb
    except ImportError as exc:
        raise ImportError("Training requires scikit-learn and lightgbm installed.") from exc

    df = df.sort_values(["timestamp", "symbol"]).reset_index(drop=True)
    unique_times = df["timestamp"].unique()
    tscv = TimeSeriesSplit(n_splits=n_splits, gap=gap_hours)

    models: list[Any] = []
    fold = 1
    for train_idx, test_idx in tscv.split(unique_times):
        train_times = unique_times[train_idx]
        test_times = unique_times[test_idx]

        train_df = df[df["timestamp"].isin(train_times)]
        test_df = df[df["timestamp"].isin(test_times)]

        X_train = train_df[feature_cols]
        y_train = train_df[target_col]
        X_test = test_df[feature_cols]
        y_test = test_df[target_col]

        group_train = train_df.groupby("timestamp").size().values
        group_test = test_df.groupby("timestamp").size().values

        ranker = lgb.LGBMRanker(
            objective="lambdarank",
            metric="ndcg",
            n_estimators=500,
            learning_rate=0.05,
            max_depth=6,
            importance_type="gain",
            random_state=random_state,
            n_jobs=-1,
        )

        ranker.fit(
            X_train,
            y_train,
            group=group_train,
            eval_set=[(X_test, y_test)],
            eval_group=[group_test],
            callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False), lgb.log_evaluation(period=0)],
        )
        ranker.model_name_ = f"fold_{fold}"
        models.append(ranker)
        fold += 1

    return models


def save_ranker_bundle(bundle_dir: str | Path, *, models: list[Any], feature_cols: list[str], metadata: dict[str, Any]) -> RankerBundle:
    bundle_path = Path(bundle_dir)
    bundle_path.mkdir(parents=True, exist_ok=True)

    model_paths: list[str] = []
    for idx, model in enumerate(models, start=1):
        model_name = getattr(model, "model_name_", f"fold_{idx}")
        filename = f"lgb_ranker_{model_name}.pkl"
        model_path = bundle_path / filename
        joblib.dump(model, model_path)
        model_paths.append(filename)

    payload = {
        "model_type": "lgb_ranker",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "feature_cols": feature_cols,
        "model_paths": model_paths,
        "metadata": metadata,
    }
    (bundle_path / "bundle.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return RankerBundle(model_paths=model_paths, feature_cols=feature_cols, metadata=metadata)


def load_ranker_bundle(bundle_dir: str | Path) -> tuple[list[Any], list[str], dict[str, Any]]:
    bundle_path = Path(bundle_dir)
    payload = json.loads((bundle_path / "bundle.json").read_text(encoding="utf-8"))
    models = [joblib.load(bundle_path / name) for name in payload["model_paths"]]
    return models, payload["feature_cols"], payload.get("metadata", {})


def predict_scores(models: list[Any], df: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    X = df[feature_cols]
    preds = np.zeros(len(X))
    for model in models:
        preds += model.predict(X)
    preds = preds / max(len(models), 1)

    scored = df.copy()
    scored["predicted_score"] = preds
    return scored


def resolve_parquet(directory: Path, symbol: str, prefer_suffix: str | None = None) -> Path | None:
    candidates: list[str] = []
    if prefer_suffix:
        candidates.append(f"{symbol}{prefer_suffix}.parquet")
    candidates.extend([f"{symbol}.parquet", f"{symbol}_USDT.parquet"])
    for name in candidates:
        path = directory / name
        if path.exists():
            return path

    for path in directory.glob(f"{symbol}*.parquet"):
        return path
    return None


def find_column(columns: Iterable[Any], candidates: set[str]) -> str | None:
    for column in columns:
        name = str(column).strip()
        if name.lower() in candidates:
            return name
    return None


def normalize_symbol(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("/", "").replace("-", "").replace("_", "").split(":")[0].upper()
