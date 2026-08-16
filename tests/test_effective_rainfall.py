"""実効雨量のテスト（仕様17.3節）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from amedas_rainfall.indices.effective_rainfall import (
    RESET_DUE_TO_GAP_COLUMN,
    WARMUP_COLUMN,
    calculate_effective_rainfall,
    half_life_to_decay_rate,
)


def _series(values: list[float]) -> pd.Series:
    index = pd.date_range("2020-01-01", periods=len(values), freq="h", tz="Asia/Tokyo")
    return pd.Series(values, index=index, dtype=float)


def test_initial_value_is_zero_effect_of_first_rain_equals_input() -> None:
    s = _series([10.0, 0.0, 0.0])
    result = calculate_effective_rainfall(s, half_life_hours=6.0, column_name="e")
    assert result["e"].iloc[0] == 10.0


def test_decays_exponentially_with_no_rain() -> None:
    s = _series([10.0] + [0.0] * 20)
    result = calculate_effective_rainfall(s, half_life_hours=6.0, column_name="e")
    decay = half_life_to_decay_rate(6.0)
    expected = 10.0
    for i in range(1, 21):
        expected *= decay
        assert result["e"].iloc[i] == pytest.approx(expected)


def test_value_is_half_after_one_half_life() -> None:
    half_life = 6.0
    s = _series([10.0] + [0.0] * 6)
    result = calculate_effective_rainfall(s, half_life_hours=half_life, column_name="e")
    assert abs(result["e"].iloc[6] - 5.0) < 1e-9


def test_warmup_and_reset_flags_after_gap() -> None:
    s = _series([10.0, 5.0, np.nan, np.nan, 3.0])
    result = calculate_effective_rainfall(s, half_life_hours=6.0, column_name="e")
    assert result[WARMUP_COLUMN].iloc[0]
    # 系列先頭の初期化は欠測による再初期化ではない。
    assert not result[RESET_DUE_TO_GAP_COLUMN].iloc[0]
    assert not result[WARMUP_COLUMN].iloc[1]
    assert result[WARMUP_COLUMN].iloc[4]
    assert result[RESET_DUE_TO_GAP_COLUMN].iloc[4]
    assert result["e"].iloc[4] == 3.0
