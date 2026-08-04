"""推定10分雨量・3段タンクモデルのテスト（仕様17.4節・17.5節）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from amedas_rainfall.config import load_tank_model_config
from amedas_rainfall.indices.soil_tank import (
    TANK1_COLUMN,
    TANK2_COLUMN,
    TANK3_COLUMN,
    TankModelConfig,
    disaggregate_hourly_to_10min,
    run_tank_model_10min,
)
def _hourly_series(values: list[float]) -> pd.Series:
    index = pd.date_range("2020-01-01", periods=len(values), freq="h", tz="Asia/Tokyo")
    return pd.Series(values, index=index, dtype=float)


def test_zero_rainfall_gives_six_zeros() -> None:
    s = _hourly_series([0.0])
    result = disaggregate_hourly_to_10min(s)
    assert (result == 0.0).all()
    assert len(result) == 6


def test_0_4mm_splits_equally_into_six() -> None:
    s = _hourly_series([0.4])
    result = disaggregate_hourly_to_10min(s)
    assert len(result) == 6
    assert all(abs(v - 0.4 / 6) < 1e-12 for v in result.tolist())


def test_ten_minute_sum_matches_hourly_value() -> None:
    s = _hourly_series([12.0, 0.0, 3.6])
    result = disaggregate_hourly_to_10min(s)
    for hour_end, expected in zip(s.index, s.tolist()):
        window = result[(result.index > hour_end - pd.Timedelta(hours=1)) & (result.index <= hour_end)]
        assert len(window) == 6
        assert abs(window.sum() - expected) < 1e-9


@pytest.fixture()
def tank_config() -> TankModelConfig:
    raw = load_tank_model_config()
    return TankModelConfig.from_dict(raw)


def test_config_loaded_from_yaml_has_expected_coefficients(tank_config: TankModelConfig) -> None:
    assert tank_config.tank1.infiltration_coefficient_per_hour == pytest.approx(0.12)
    assert tank_config.tank2.infiltration_coefficient_per_hour == pytest.approx(0.05)
    assert tank_config.tank3.infiltration_coefficient_per_hour == pytest.approx(0.01)
    heights = sorted(o.height_mm for o in tank_config.tank1.outlets)
    assert heights == [15.0, 60.0]


def test_storage_never_negative(tank_config: TankModelConfig) -> None:
    rng = np.random.default_rng(0)
    values = rng.choice([0.0, 0.0, 0.0, 1.0, 5.0, 20.0], size=600)
    index = pd.date_range("2020-01-01", periods=600, freq="10min", tz="Asia/Tokyo")
    s = pd.Series(values, index=index)
    result = run_tank_model_10min(s, tank_config)
    assert (result[TANK1_COLUMN] >= 0).all()
    assert (result[TANK2_COLUMN] >= 0).all()
    assert (result[TANK3_COLUMN] >= 0).all()


def test_total_storage_does_not_increase_with_no_rain(tank_config: TankModelConfig) -> None:
    index = pd.date_range("2020-01-01", periods=60, freq="10min", tz="Asia/Tokyo")
    values = [50.0] + [0.0] * 59
    s = pd.Series(values, index=index)
    result = run_tank_model_10min(s, tank_config)
    total = result[TANK1_COLUMN] + result[TANK2_COLUMN] + result[TANK3_COLUMN]
    diffs = total.diff().dropna()
    assert (diffs <= 1e-9).all()


def test_water_balance_within_tolerance(tank_config: TankModelConfig) -> None:
    index = pd.date_range("2020-01-01", periods=200, freq="10min", tz="Asia/Tokyo")
    rng = np.random.default_rng(1)
    values = rng.choice([0.0, 0.0, 2.0, 10.0], size=200)
    s = pd.Series(values, index=index)
    result = run_tank_model_10min(s, tank_config)

    total_storage = (
        result[TANK1_COLUMN].iloc[-1] + result[TANK2_COLUMN].iloc[-1] + result[TANK3_COLUMN].iloc[-1]
    )
    total_input = s.sum()
    total_side_outflow = (
        result["tank1_outflow_mm"].sum()
        + result["tank2_outflow_mm"].sum()
        + result["tank3_outflow_mm"].sum()
    )
    total_deep_infiltration = result["tank3_infiltration_mm"].sum()
    # 入力 = 最終貯留量 + 側方流出合計 + タンク3底面浸透（系外流出）
    balance_error = total_input - (total_storage + total_side_outflow + total_deep_infiltration)
    assert abs(balance_error) < 1e-6


def test_known_hand_calculation_single_step() -> None:
    """手計算: タンク1貯留0, 降雨10mmを1ステップ投入した場合。"""
    raw = {
        "time_step_hours": 1 / 6,
        "tank1": {
            "outlets": [{"height_mm": 15.0, "coefficient_per_hour": 0.1}],
            "infiltration_coefficient_per_hour": 0.12,
        },
        "tank2": {
            "outlets": [{"height_mm": 15.0, "coefficient_per_hour": 0.05}],
            "infiltration_coefficient_per_hour": 0.05,
        },
        "tank3": {
            "outlets": [{"height_mm": 15.0, "coefficient_per_hour": 0.01}],
            "infiltration_coefficient_per_hour": 0.01,
        },
        "initial_storage_mm": {"tank1": 0.0, "tank2": 0.0, "tank3": 0.0},
    }
    config = TankModelConfig.from_dict(raw)
    index = pd.date_range("2020-01-01", periods=1, freq="10min", tz="Asia/Tokyo")
    s = pd.Series([10.0], index=index)
    result = run_tank_model_10min(s, config)

    # storage1 = 0 + 10 = 10mm; 15mm未満なので側方流出なし
    dt = 1 / 6
    expected_infil1 = 0.12 * 10.0 * dt
    expected_tank1 = 10.0 - expected_infil1
    assert result[TANK1_COLUMN].iloc[0] == pytest.approx(expected_tank1)

    expected_infil2 = 0.05 * expected_infil1 * dt
    expected_tank2 = expected_infil1 - expected_infil2
    assert result[TANK2_COLUMN].iloc[0] == pytest.approx(expected_tank2)

    expected_infil3 = 0.01 * expected_infil2 * dt
    expected_tank3 = expected_infil2 - expected_infil3
    assert result[TANK3_COLUMN].iloc[0] == pytest.approx(expected_tank3)
