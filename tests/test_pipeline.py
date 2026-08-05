"""指標キャッシュ（indices.parquet）の読み込みに関するテスト。

指標計算ロジックが変わって列名が変わった場合（例:
estimated_soil_rainfall_mm → soil_rainfall_mm）、正規化済みデータ
（hourly.parquet）自体は更新されないため、mtime比較だけでは古いキャッシュが
有効なままとみなされ、新しい列がグラフに表示されない不具合があった。
"""

from __future__ import annotations

import time

import pandas as pd
import pytest

from amedas_rainfall.config import AppConfig
from amedas_rainfall.pipeline import (
    EXPECTED_INDICES_COLUMNS,
    indices_cache_path,
    load_or_compute_all_indices,
    normalized_hourly_path,
)


@pytest.fixture()
def config(tmp_path) -> AppConfig:
    return AppConfig(
        raw={
            "paths": {
                "normalized_dir": str(tmp_path / "normalized"),
                "calculated_dir": str(tmp_path / "calculated"),
            }
        }
    )


def _write_hourly(config: AppConfig, station_code: str) -> pd.DataFrame:
    index = pd.date_range("2020-01-01", periods=48, freq="h", tz="Asia/Tokyo")
    hourly_df = pd.DataFrame({"rainfall_raw_mm": [0.0] * 48}, index=index)
    hourly_path = normalized_hourly_path(config, station_code)
    hourly_path.parent.mkdir(parents=True, exist_ok=True)
    hourly_df.to_parquet(hourly_path)
    return hourly_df


def test_stale_cache_missing_new_columns_is_recomputed(config: AppConfig) -> None:
    station_code = "test1"
    hourly_df = _write_hourly(config, station_code)

    # 現行コードより前の指標計算で作られた、soil_rainfall_mm列を持たない
    # 「古い」キャッシュを模擬する（列名が変わる前のスキーマ）。
    stale_cache = hourly_df.copy()
    stale_cache["estimated_soil_rainfall_mm"] = 0.0
    cache_path = indices_cache_path(config, station_code)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    stale_cache.to_parquet(cache_path)

    # キャッシュの方がhourly.parquetより新しい（mtime比較だけなら「有効」と
    # 判定されてしまう状態）にしておく。
    time.sleep(0.01)
    cache_path.touch()

    result = load_or_compute_all_indices(config, station_code, hourly_df=hourly_df)

    assert set(EXPECTED_INDICES_COLUMNS).issubset(result.columns)
    assert "soil_rainfall_mm" in result.columns


def test_fresh_valid_cache_is_reused_without_recompute(config: AppConfig, monkeypatch) -> None:
    station_code = "test2"
    hourly_df = _write_hourly(config, station_code)

    result = load_or_compute_all_indices(config, station_code, hourly_df=hourly_df)
    assert set(EXPECTED_INDICES_COLUMNS).issubset(result.columns)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("有効なキャッシュがあるのに再計算が呼ばれた")

    monkeypatch.setattr("amedas_rainfall.pipeline.compute_all_indices", _fail_if_called)

    reused = load_or_compute_all_indices(config, station_code, hourly_df=hourly_df)
    pd.testing.assert_frame_equal(reused, result, check_freq=False)
