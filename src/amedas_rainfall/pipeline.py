"""地点選択からダウンロード・正規化・指標計算・統計解析までを結ぶ処理の橋渡し。

Streamlit UI（ui/配下）はこのモジュールの関数を呼び出すことで、
下位モジュール（jma/, processing/, indices/, statistics/）を直接意識せずに
一連の処理を実行できる。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

import pandas as pd

from amedas_rainfall.config import AppConfig, load_tank_model_config
from amedas_rainfall.indicators import (
    DEFAULT_CONTINUOUS_COLUMN,
    DEFAULT_EFFECTIVE_COLUMNS,
    DEFAULT_ROLLING_COLUMN,
    annual_indicator_columns,
    continuous_column,
    effective_column,
    indicator_requires_state_continuity,
    rolling_column,
)
from amedas_rainfall.indices.continuous_rainfall import calculate_continuous_rainfall
from amedas_rainfall.indices.effective_rainfall import calculate_all_effective_rainfall
from amedas_rainfall.indices.rolling_rainfall import calculate_rolling_rainfall
from amedas_rainfall.indices.soil_tank import TankModelConfig, calculate_soil_rainfall_hourly
from amedas_rainfall.jma.csv_parser import parse_jma_hourly_precipitation_csv
from amedas_rainfall.processing.merging import merge_hourly_frames
from amedas_rainfall.processing.normalization import reindex_to_continuous_hourly
from amedas_rainfall.storage.files import atomic_write_json, atomic_write_parquet
from amedas_rainfall.statistics.annual_maxima import (
    ALL_YEAR_BOUNDARIES,
    calculate_annual_completeness,
    calculate_annual_maxima,
)
from amedas_rainfall.models import YearBoundaryDefinition

INDICES_CACHE_VERSION = 3

INDICATOR_COLUMNS_FOR_ANNUAL_MAXIMA = [
    "rainfall_raw_mm",
    DEFAULT_CONTINUOUS_COLUMN,
    DEFAULT_ROLLING_COLUMN,
    *DEFAULT_EFFECTIVE_COLUMNS,
    "soil_rainfall_mm",
]

EXPECTED_INDICES_COLUMNS = [
    DEFAULT_CONTINUOUS_COLUMN,
    DEFAULT_ROLLING_COLUMN,
    *DEFAULT_EFFECTIVE_COLUMNS,
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


def indices_cache_metadata_path(config: AppConfig, station_code: str) -> Path:
    return indices_cache_path(config, station_code).with_suffix(".meta.json")


def raw_station_dir(config: AppConfig, station_code: str, station_name: str) -> Path:
    base = config.resolved_path("paths.raw_dir")
    stable = base / station_code
    legacy = base / f"{station_code}_{station_name}"
    if not stable.exists() and legacy.exists():
        return legacy
    return stable


def raw_station_dirs(config: AppConfig, station_code: str, station_name: str) -> list[Path]:
    """新しい安定パスと旧「コード_地点名」パスを両方返し、移行中の履歴を失わない。"""
    base = config.resolved_path("paths.raw_dir")
    candidates = [base / station_code, base / f"{station_code}_{station_name}"]
    existing = []
    for candidate in candidates:
        if candidate.exists() and candidate not in existing:
            existing.append(candidate)
    return existing or [candidates[0]]


def rebuild_normalized_from_raw(config: AppConfig, station_code: str, station_name: str) -> pd.DataFrame:
    """data/raw配下の全CSVを解析・統合し、正規化済み時別Parquetを再構築する。"""
    raw_dirs = raw_station_dirs(config, station_code, station_name)
    csv_files = sorted(
        (path for raw_dir in raw_dirs if raw_dir.exists() for path in raw_dir.glob("*.csv")),
        key=lambda path: (path.name, str(path.parent)),
    )
    if not csv_files:
        raise FileNotFoundError(
            "生データが見つかりません: " + " / ".join(str(path) for path in raw_dirs)
        )

    frames = []
    names = []
    for f in csv_files:
        parsed = parse_jma_hourly_precipitation_csv(f.read_bytes())
        frames.append(parsed.frame)
        names.append(f"{f.parent.name}/{f.name}")

    merged = merge_hourly_frames(frames, names)
    merged = reindex_to_continuous_hourly(merged)

    out_path = normalized_hourly_path(config, station_code)
    atomic_write_parquet(merged, out_path)
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
    dry_hours = config.get("rainfall.dry_hours_reset", 12)
    continuous = calculate_continuous_rainfall(
        raw, dry_hours_reset=dry_hours, column_name=continuous_column(dry_hours)
    )

    rolling_hours = config.get("rainfall.rolling_window_hours", 24)
    _report(0.05, f"{rolling_hours}時間移動雨量を計算しています...")
    rolling = calculate_rolling_rainfall(
        raw, window_hours=rolling_hours, column_name=rolling_column(rolling_hours)
    )

    _report(0.10, "実効雨量を計算しています...")
    effective = calculate_all_effective_rainfall(
        raw, half_lives_hours=config.get("rainfall.effective_half_lives_hours", [3, 6, 24])
    )

    _report(0.15, "土壌雨量を計算しています...")
    tank_raw = load_tank_model_config()
    tank_config = TankModelConfig.from_dict(tank_raw)

    def _tank_progress(fraction: float) -> None:
        _report(0.15 + fraction * 0.80, "土壌雨量を計算しています...")

    tank_hourly = calculate_soil_rainfall_hourly(
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


def expected_indices_columns(config: AppConfig) -> list[str]:
    half_lives = config.get("rainfall.effective_half_lives_hours", [3, 6, 24])
    return [
        continuous_column(config.get("rainfall.dry_hours_reset", 12)),
        rolling_column(config.get("rainfall.rolling_window_hours", 24)),
        *(effective_column(value) for value in half_lives),
        "soil_tank_1_mm",
        "soil_tank_2_mm",
        "soil_tank_3_mm",
        "soil_rainfall_mm",
    ]


def indices_cache_signature(config: AppConfig, station_code: str) -> str | None:
    hourly_path = normalized_hourly_path(config, station_code)
    if not hourly_path.exists():
        return None
    stat = hourly_path.stat()
    tank_path = Path(__file__).resolve().parents[2] / "config" / "tank_model.yaml"
    tank_hash = hashlib.sha256(tank_path.read_bytes()).hexdigest() if tank_path.exists() else None
    payload = {
        "version": INDICES_CACHE_VERSION,
        "normalized_size": stat.st_size,
        "normalized_mtime_ns": stat.st_mtime_ns,
        "rainfall": config.get("rainfall", {}),
        "timezone": config.get("timezone", "Asia/Tokyo"),
        "tank_model_sha256": tank_hash,
        "expected_columns": expected_indices_columns(config),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _cache_metadata_matches(config: AppConfig, station_code: str, signature: str | None) -> bool:
    metadata_path = indices_cache_metadata_path(config, station_code)
    if signature is None or not metadata_path.exists():
        return False
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload.get("signature") == signature


def is_indices_cache_valid(config: AppConfig, station_code: str) -> bool:
    """指標キャッシュが現在の入力データ・設定・実装と一致するかを返す。"""
    cache_path = indices_cache_path(config, station_code)
    if not cache_path.exists():
        return False
    signature = indices_cache_signature(config, station_code)
    if not _cache_metadata_matches(config, station_code, signature):
        return False
    try:
        import pyarrow.parquet as pq

        columns = pq.read_schema(cache_path).names
    except (OSError, ValueError):
        return False
    return set(expected_indices_columns(config)).issubset(columns)


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

    signature = indices_cache_signature(config, station_code)
    if not force_recompute and cache_path.exists() and hourly_path.exists():
        if _cache_metadata_matches(config, station_code, signature):
            try:
                cached = pd.read_parquet(cache_path)
            except (OSError, ValueError):
                cached = None
            if cached is not None and set(expected_indices_columns(config)).issubset(cached.columns):
                return cached

    if hourly_df is None:
        hourly_df = load_normalized_hourly(config, station_code)

    result = compute_all_indices(config, hourly_df, progress_callback=progress_callback)

    atomic_write_parquet(result, cache_path)
    signature = indices_cache_signature(config, station_code)
    atomic_write_json(
        indices_cache_metadata_path(config, station_code),
        {"signature": signature, "cache_version": INDICES_CACHE_VERSION},
    )
    return result


def year_boundaries(config: AppConfig | None = None) -> dict[str, YearBoundaryDefinition]:
    if config is None:
        return ALL_YEAR_BOUNDARIES
    raw = config.get("year_boundaries", {}) or {}
    if not raw:
        return ALL_YEAR_BOUNDARIES
    return {
        key: YearBoundaryDefinition(
            key=key,
            label=node.get("label", key),
            start_month=int(node["start_month"]),
            start_day=int(node["start_day"]),
        )
        for key, node in raw.items()
    }


def compute_annual_maxima_all_boundaries(
    indices_df: pd.DataFrame,
    columns: list[str] | None = None,
    config: AppConfig | None = None,
) -> dict[str, dict[str, pd.DataFrame]]:
    """3種類の年区切りそれぞれについて、各指標の年最大値を計算する。"""
    columns = columns or annual_indicator_columns(indices_df.columns)
    result: dict[str, dict[str, pd.DataFrame]] = {}
    for key, boundary in year_boundaries(config).items():
        per_indicator = {}
        for col in columns:
            if col in indices_df.columns:
                per_indicator[col] = calculate_annual_maxima(indices_df[col], boundary)
        result[key] = per_indicator
    return result


def compute_completeness_all_boundaries(
    indices_df: pd.DataFrame,
    completeness_threshold_percent: float = 95.0,
    config: AppConfig | None = None,
    exclude_state_reset: bool = True,
    now: pd.Timestamp | None = None,
) -> dict[str, list]:
    valid_mask = indices_df["rainfall_raw_mm"].notna()
    if "state_reset_due_to_gap" in indices_df.columns:
        state_reset_mask = indices_df["state_reset_due_to_gap"].fillna(False).astype(bool)
    else:
        # 正規化データだけを渡した品質画面でも、欠測明けを同じ規則で検出する。
        state_reset_mask = valid_mask & ~valid_mask.shift(1, fill_value=False)
        if len(state_reset_mask):
            state_reset_mask.iloc[0] = False
    result = {}
    timezone = config.get("timezone", "Asia/Tokyo") if config else "Asia/Tokyo"
    now = now or pd.Timestamp.now(tz=timezone)
    options = config.get("annual_maxima", {}) if config else {}
    for key, boundary in year_boundaries(config).items():
        result[key] = calculate_annual_completeness(
            valid_mask,
            boundary,
            state_reset_mask=state_reset_mask,
            completeness_threshold_percent=completeness_threshold_percent,
            now=now,
            exclude_incomplete_start_year=options.get("exclude_incomplete_start_year", True),
            exclude_incomplete_end_year=options.get("exclude_incomplete_end_year", True),
            exclude_ongoing_latest_year=options.get("exclude_ongoing_latest_year", True),
            exclude_below_completeness=options.get("exclude_below_completeness", True),
            exclude_state_reset_unreliable=(
                exclude_state_reset and options.get("exclude_state_reset_unreliable", True)
            ),
        )
    return result


def build_annual_analysis(
    indices_df: pd.DataFrame,
    indicator: str,
    boundary_key: str,
    *,
    config: AppConfig | None = None,
    completeness_threshold_percent: float | None = None,
    now: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """年最大値と採否判定を、全画面・出力で共通利用できる表にまとめる。"""
    threshold = (
        completeness_threshold_percent
        if completeness_threshold_percent is not None
        else (config.get("annual_maxima.completeness_threshold_percent", 95.0) if config else 95.0)
    )
    maxima = compute_annual_maxima_all_boundaries(
        indices_df, columns=[indicator], config=config
    )[boundary_key].get(indicator)
    if maxima is None:
        return pd.DataFrame()
    completeness = compute_completeness_all_boundaries(
        indices_df,
        completeness_threshold_percent=threshold,
        config=config,
        exclude_state_reset=indicator_requires_state_continuity(indicator),
        now=now,
    )[boundary_key]
    rows = {row.year_label: row for row in completeness}
    result = maxima.copy()
    result["indicator"] = indicator
    result["is_eligible_default"] = result["year_label"].map(
        lambda label: rows[label].is_eligible_default if label in rows else False
    )
    result["exclusion_reasons"] = result["year_label"].map(
        lambda label: "、".join(rows[label].exclusion_reasons) if label in rows else "完全性情報なし"
    )
    result["completeness_percent"] = result["year_label"].map(
        lambda label: rows[label].completeness_percent if label in rows else float("nan")
    )
    return result
