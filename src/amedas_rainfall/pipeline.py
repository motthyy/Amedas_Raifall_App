"""地点選択からダウンロード・正規化・指標計算・統計解析までを結ぶ処理の橋渡し。

Streamlit UI（ui/配下）はこのモジュールの関数を呼び出すことで、
下位モジュール（jma/, processing/, indices/, statistics/）を直接意識せずに
一連の処理を実行できる。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Callable

import pandas as pd

from amedas_rainfall.config import AppConfig, load_tank_model_config
from amedas_rainfall.indices.continuous_rainfall import calculate_continuous_rainfall
from amedas_rainfall.indices.effective_rainfall import calculate_all_effective_rainfall
from amedas_rainfall.indices.rolling_rainfall import calculate_rolling_rainfall
from amedas_rainfall.indices.soil_tank import TankModelConfig, calculate_soil_rainfall
from amedas_rainfall.jma.csv_parser import parse_jma_hourly_precipitation_csv
from amedas_rainfall.processing.merging import merge_hourly_frames
from amedas_rainfall.processing.normalization import reindex_to_continuous_hourly
from amedas_rainfall.statistics.annual_maxima import (
    ALL_YEAR_BOUNDARIES,
    calculate_annual_completeness,
    calculate_annual_maxima,
)

INDICATOR_COLUMNS_FOR_ANNUAL_MAXIMA = [
    "rainfall_raw_mm",
    "continuous_rainfall_12h_mm",
    "rolling_rainfall_24h_mm",
    "effective_rainfall_3h_mm",
    "effective_rainfall_6h_mm",
    "effective_rainfall_24h_mm",
    "soil_rainfall_mm",
]

EXPECTED_INDICES_COLUMNS = [
    "continuous_rainfall_12h_mm",
    "rolling_rainfall_24h_mm",
    "effective_rainfall_3h_mm",
    "effective_rainfall_6h_mm",
    "effective_rainfall_24h_mm",
    "soil_tank_1_mm",
    "soil_tank_2_mm",
    "soil_tank_3_mm",
    "soil_rainfall_mm",
]
"""compute_all_indicesが生成する列名の一覧。

指標の計算ロジックが変更され列名が変わった場合（例: estimated_soil_rainfall_mm
→ soil_rainfall_mm への改名）、正規化済みデータ自体は更新されないためmtime比較
だけでは古いキャッシュ（indices.parquet）が有効なままとみなされてしまい、
新しい列がグラフに表示されない不具合になっていた。読み込んだキャッシュに
これらの列が揃っているかを確認し、欠けていれば再計算する。
"""


def normalized_hourly_path(config: AppConfig, station_code: str) -> Path:
    base = config.resolved_path("paths.normalized_dir")
    return base / station_code / "hourly.parquet"


def indices_cache_path(config: AppConfig, station_code: str) -> Path:
    """計算済み指標（compute_all_indicesの結果）のキャッシュ保存先。"""
    base = config.resolved_path("paths.calculated_dir")
    return base / station_code / "indices.parquet"


def raw_station_dir(config: AppConfig, station_code: str, station_name: str) -> Path:
    base = config.resolved_path("paths.raw_dir")
    return base / f"{station_code}_{station_name}"


def rebuild_normalized_from_raw(config: AppConfig, station_code: str, station_name: str) -> pd.DataFrame:
    """data/raw配下の全CSVを解析・統合し、正規化済み時別Parquetを再構築する。"""
    raw_dir = raw_station_dir(config, station_code, station_name)
    csv_files = sorted(raw_dir.glob("*.csv")) if raw_dir.exists() else []
    if not csv_files:
        raise FileNotFoundError(f"生データが見つかりません: {raw_dir}")

    frames = []
    names = []
    for f in csv_files:
        parsed = parse_jma_hourly_precipitation_csv(f.read_bytes())
        frames.append(parsed.frame)
        names.append(f.name)

    merged = merge_hourly_frames(frames, names)
    merged = reindex_to_continuous_hourly(merged)

    out_path = normalized_hourly_path(config, station_code)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(out_path)
    return merged


def load_normalized_hourly(config: AppConfig, station_code: str) -> pd.DataFrame:
    path = normalized_hourly_path(config, station_code)
    return pd.read_parquet(path)


def compute_all_indices(
    config: AppConfig,
    hourly_df: pd.DataFrame,
    progress_callback: Callable[[float, str], None] | None = None,
) -> pd.DataFrame:
    """全ての雨量指標（8節・9節）をまとめて計算する。

    Args:
        progress_callback: (進捗率0.0〜1.0, 状況メッセージ) を通知するコールバック。
            土壌雨量（3段タンクモデル、10分刻み）が最も計算量が多いため、
            その内部進捗もこの範囲へマッピングして報告する。
    """

    def _report(fraction: float, message: str) -> None:
        if progress_callback is not None:
            progress_callback(fraction, message)

    raw = hourly_df["rainfall_raw_mm"]

    _report(0.0, "連続雨量を計算しています...")
    continuous = calculate_continuous_rainfall(raw, dry_hours_reset=config.get("rainfall.dry_hours_reset", 12))

    _report(0.05, "24時間移動雨量を計算しています...")
    rolling = calculate_rolling_rainfall(raw, window_hours=config.get("rainfall.rolling_window_hours", 24))

    _report(0.10, "実効雨量を計算しています...")
    effective = calculate_all_effective_rainfall(
        raw, half_lives_hours=config.get("rainfall.effective_half_lives_hours", [3, 6, 24])
    )

    _report(0.15, "土壌雨量を計算しています...")
    tank_raw = load_tank_model_config()
    tank_config = TankModelConfig.from_dict(tank_raw)

    def _tank_progress(fraction: float) -> None:
        _report(0.15 + fraction * 0.80, "土壌雨量を計算しています...")

    tank_10min, tank_hourly = calculate_soil_rainfall(
        raw, tank_config, progress_callback=_tank_progress
    )

    _report(0.95, "計算結果をまとめています...")
    result = hourly_df.copy()
    result = result.join(continuous, how="left")
    result = result.join(rolling, how="left")
    result = result.join(effective, how="left", rsuffix="_eff")
    result = result.join(tank_hourly, how="left")

    _report(1.0, "計算が完了しました。")
    return result


def load_or_compute_all_indices(
    config: AppConfig,
    station_code: str,
    hourly_df: pd.DataFrame | None = None,
    force_recompute: bool = False,
    progress_callback: Callable[[float, str], None] | None = None,
) -> pd.DataFrame:
    """指標計算結果をキャッシュから読み込む。なければ計算してキャッシュに保存する。

    キャッシュ（data/calculated/{地点コード}/indices.parquet）は、正規化済み
    時別データ（hourly.parquet）よりも新しい場合にのみ有効とみなす。
    正規化データが更新された場合は自動的に再計算される。
    """
    cache_path = indices_cache_path(config, station_code)
    hourly_path = normalized_hourly_path(config, station_code)

    if not force_recompute and cache_path.exists() and hourly_path.exists():
        if cache_path.stat().st_mtime >= hourly_path.stat().st_mtime:
            cached = pd.read_parquet(cache_path)
            if set(EXPECTED_INDICES_COLUMNS).issubset(cached.columns):
                return cached

    if hourly_df is None:
        hourly_df = load_normalized_hourly(config, station_code)

    result = compute_all_indices(config, hourly_df, progress_callback=progress_callback)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(cache_path)
    return result


def compute_annual_maxima_all_boundaries(
    indices_df: pd.DataFrame,
    columns: list[str] | None = None,
) -> dict[str, dict[str, pd.DataFrame]]:
    """3種類の年区切りそれぞれについて、各指標の年最大値を計算する。"""
    columns = columns or INDICATOR_COLUMNS_FOR_ANNUAL_MAXIMA
    result: dict[str, dict[str, pd.DataFrame]] = {}
    for key, boundary in ALL_YEAR_BOUNDARIES.items():
        per_indicator = {}
        for col in columns:
            if col in indices_df.columns:
                per_indicator[col] = calculate_annual_maxima(indices_df[col], boundary)
        result[key] = per_indicator
    return result


def compute_completeness_all_boundaries(
    indices_df: pd.DataFrame,
    completeness_threshold_percent: float = 95.0,
) -> dict[str, list]:
    valid_mask = indices_df["rainfall_raw_mm"].notna()
    state_reset_mask = (
        indices_df["state_reset_due_to_gap"] if "state_reset_due_to_gap" in indices_df.columns else None
    )
    result = {}
    now = pd.Timestamp.now(tz="Asia/Tokyo")
    for key, boundary in ALL_YEAR_BOUNDARIES.items():
        result[key] = calculate_annual_completeness(
            valid_mask,
            boundary,
            state_reset_mask=state_reset_mask,
            completeness_threshold_percent=completeness_threshold_percent,
            now=now,
        )
    return result
