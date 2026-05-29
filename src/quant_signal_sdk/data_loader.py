from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


@dataclass(frozen=True, slots=True)
class BundleAsset:
    """Metadata for a single asset entry in a bundle manifest."""

    symbol: str
    data_paths: dict[str, str] = field(default_factory=dict)
    column_mapping: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class BundleManifest:
    """Parsed representation of a bundle manifest.json file."""

    def __init__(self, bundle_version: str, universe: list[BundleAsset]) -> None:
        self.bundle_version = bundle_version
        self.universe = universe

    @classmethod
    def from_file(cls, manifest_path: str | Path) -> "BundleManifest":
        path = Path(manifest_path)
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BundleManifest":
        bundle_version = str(payload.get("bundle_version") or payload.get("bundleVersion") or "")
        universe_raw = payload.get("universe") or payload.get("assets") or []
        universe = cls._parse_universe(universe_raw)
        return cls(bundle_version=bundle_version, universe=universe)

    @property
    def first_symbol(self) -> str | None:
        return self.universe[0].symbol if self.universe else None

    def get_asset(self, symbol: str) -> BundleAsset:
        normalized = self._normalize_symbol(symbol)
        for asset in self.universe:
            if self._normalize_symbol(asset.symbol) == normalized:
                return asset
        raise KeyError(f"Asset not found in bundle manifest: {symbol}")

    @staticmethod
    def _parse_universe(universe_raw: Any) -> list[BundleAsset]:
        assets: list[BundleAsset] = []

        if isinstance(universe_raw, Mapping):
            iterator = universe_raw.items()
            for symbol_key, raw_asset in iterator:
                assets.append(BundleManifest._parse_asset(raw_asset, default_symbol=str(symbol_key)))
            return assets

        if isinstance(universe_raw, list):
            for raw_asset in universe_raw:
                assets.append(BundleManifest._parse_asset(raw_asset))
            return assets

        raise ValueError("manifest universe must be a list or mapping")

    @staticmethod
    def _parse_asset(raw_asset: Any, default_symbol: str | None = None) -> BundleAsset:
        if isinstance(raw_asset, str):
            symbol = (default_symbol or raw_asset).strip()
            if not symbol:
                raise ValueError("bundle asset symbol cannot be empty")
            return BundleAsset(symbol=symbol)

        if not isinstance(raw_asset, Mapping):
            raise ValueError("bundle asset entries must be mappings or strings")

        symbol = str(
            raw_asset.get("symbol")
            or raw_asset.get("asset")
            or raw_asset.get("name")
            or default_symbol
            or ""
        ).strip()
        if not symbol:
            raise ValueError("bundle asset entry is missing a symbol")

        data_paths = BundleManifest._parse_data_paths(
            raw_asset.get("data_paths")
            or raw_asset.get("dataPaths")
            or raw_asset.get("paths")
            or raw_asset.get("data")
            or {}
        )
        column_mapping = BundleManifest._parse_column_mapping(
            raw_asset.get("column_mapping")
            or raw_asset.get("columnMapping")
            or raw_asset.get("columns")
            or {}
        )
        metadata = {
            key: value
            for key, value in raw_asset.items()
            if key not in {
                "symbol",
                "asset",
                "name",
                "data_paths",
                "dataPaths",
                "paths",
                "data",
                "column_mapping",
                "columnMapping",
                "columns",
            }
        }
        return BundleAsset(symbol=symbol, data_paths=data_paths, column_mapping=column_mapping, metadata=metadata)

    @staticmethod
    def _parse_data_paths(raw_paths: Any) -> dict[str, str]:
        if isinstance(raw_paths, Mapping):
            return {str(key): str(value) for key, value in raw_paths.items()}

        if isinstance(raw_paths, list):
            parsed: dict[str, str] = {}
            for entry in raw_paths:
                if not isinstance(entry, Mapping):
                    continue
                stream_name = entry.get("name") or entry.get("stream") or entry.get("type") or entry.get("key")
                path_value = entry.get("path") or entry.get("file") or entry.get("data_path") or entry.get("dataPath")
                if stream_name is None or path_value is None:
                    continue
                parsed[str(stream_name)] = str(path_value)
            return parsed

        return {}

    @staticmethod
    def _parse_column_mapping(raw_mapping: Any) -> dict[str, str]:
        if isinstance(raw_mapping, Mapping):
            return {str(key): str(value) for key, value in raw_mapping.items()}

        return {}

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return symbol.strip().upper()


class BundleLoader:
    """Load raw parquet assets for a single symbol from a bundle directory."""

    def __init__(self, bundle_dir: str | Path) -> None:
        self._bundle_dir = Path(bundle_dir).expanduser().resolve()
        self._manifest_path = self._bundle_dir / "manifest.json"
        if not self._manifest_path.exists():
            raise FileNotFoundError(f"manifest.json not found in bundle directory: {self._bundle_dir}")
        self._manifest = BundleManifest.from_file(self._manifest_path)
        self._manifest_root = self._manifest_path.parent
        self._cache: dict[str, dict[str, pd.DataFrame]] = {}

    @property
    def manifest(self) -> BundleManifest:
        return self._manifest

    def load_raw_asset_data(self, symbol: str) -> dict[str, pd.DataFrame]:
        """Lazy-load all parquet data streams defined for a symbol."""

        cache_key = self._normalize_symbol(symbol)
        if cache_key in self._cache:
            return self._cache[cache_key]

        asset = self._manifest.get_asset(symbol)
        if not asset.data_paths:
            raise ValueError(f"No data paths defined for symbol: {asset.symbol}")

        loaded: dict[str, pd.DataFrame] = {}
        for stream_name, relative_path in asset.data_paths.items():
            file_path = self._resolve_path(relative_path)
            if not file_path.exists():
                raise FileNotFoundError(f"Data file not found for {asset.symbol} stream {stream_name}: {file_path}")
            loaded[stream_name] = pd.read_parquet(file_path)

        self._cache[cache_key] = loaded
        return loaded

    def _resolve_path(self, raw_path: str | Path) -> Path:
        path = Path(raw_path).expanduser()
        if path.is_absolute():
            return path
        return (self._manifest_root / path).resolve()

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return symbol.strip().upper()