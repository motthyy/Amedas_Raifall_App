"""年最大値・年区切り・完全性のテスト（仕様17.6節）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from amedas_rainfall.statistics.annual_maxima import (
    CALENDAR_YEAR,
    FISCAL_YEAR,
    JUNE_START_YEAR,
    calculate_annual_completeness,
    calculate_annual_maxima,
    year_label,
    year_window,
)


def test_year_labels_are_formatted_correctly() -> None:
    assert year_label(2025, CALENDAR_YEAR) == "2025年"
    assert year_label(2025, FISCAL_YEAR) == "2025年度"
    assert year_label(2025, JUNE_START_YEAR) == "2025年6月始まり"


def test_fiscal_year_window_boundaries() -> None:
    start, end = year_window(2025, FISCAL_YEAR)
    assert (start.month, start.day) == (4, 1)
    assert start.year == 2025
    assert (end.month, end.day) == (4, 1)
    assert end.year == 2026


def test_june_start_year_window_boundaries() -> None:
    start, end = year_window(2025, JUNE_START_YEAR)
    assert (start.year, start.month, start.day) == (2025, 6, 1)
    assert (end.year, end.month, end.day) == (2026, 6, 1)


def test_boundary_timestamps_assigned_to_correct_fiscal_year() -> None:
    idx = pd.DatetimeIndex(
        [
            pd.Timestamp("2026-03-31 23:00", tz="Asia/Tokyo"),
            pd.Timestamp("2026-04-01 00:00", tz="Asia/Tokyo"),
            pd.Timestamp("2026-04-01 01:00", tz="Asia/Tokyo"),
        ]
    )
    values = pd.Series([1.0, 2.0, 3.0], index=idx)
    result = calculate_annual_maxima(values, FISCAL_YEAR)
    labels = dict(zip(result["year_label"], result["max_value"], strict=True))
    assert labels["2025年度"] == 2.0
    assert labels["2026年度"] == 3.0


def test_annual_max_datetime_is_preserved() -> None:
    idx = pd.date_range("2020-01-01", periods=24 * 5, freq="h", tz="Asia/Tokyo")
    values = pd.Series(np.zeros(len(idx)), index=idx)
    peak_time = idx[50]
    values.loc[peak_time] = 99.9
    result = calculate_annual_maxima(values, CALENDAR_YEAR)
    row = result[result["year_label"] == "2020年"].iloc[0]
    assert row["max_value"] == 99.9
    assert row["max_datetime"] == peak_time


def test_completeness_percentage_calculation() -> None:
    idx = pd.date_range("2020-01-01 01:00", periods=24 * 10, freq="h", tz="Asia/Tokyo")
    valid = pd.Series(True, index=idx)
    # 10日間中1日分(24時間)を欠測にする
    valid.iloc[24:48] = False
    results = calculate_annual_completeness(
        valid,
        CALENDAR_YEAR,
        data_start=idx[0],
        data_end=idx[-1],
        now=pd.Timestamp("2030-01-01", tz="Asia/Tokyo"),
    )
    result_2020 = [r for r in results if r.year_label == "2020年"][0]
    # 部分期間だけを分母にすると短い観測年を完全と誤認するため、暦年全体を分母にする。
    assert result_2020.expected_hours == 8784
    assert result_2020.missing_hours == 8784 - (len(idx) - 24)
    expected_percent = 100.0 * (len(idx) - 24) / 8784
    assert abs(result_2020.completeness_percent - expected_percent) < 1e-9


def test_complete_finished_year_is_eligible_and_has_full_denominator() -> None:
    index = pd.date_range(
        "2023-01-01 01:00", "2024-01-01 00:00", freq="h", tz="Asia/Tokyo"
    )
    valid = pd.Series(True, index=index)

    result = calculate_annual_completeness(
        valid,
        CALENDAR_YEAR,
        now=pd.Timestamp("2024-06-01", tz="Asia/Tokyo"),
    )[0]

    assert result.expected_hours == 8760
    assert result.valid_hours == 8760
    assert result.is_eligible_default


def test_ongoing_year_uses_elapsed_hours_but_is_excluded_by_reason() -> None:
    index = pd.date_range(
        "2024-01-01 01:00", "2024-06-01 12:00", freq="h", tz="Asia/Tokyo"
    )
    valid = pd.Series(True, index=index)
    now = pd.Timestamp("2024-06-01 12:30", tz="Asia/Tokyo")

    result = calculate_annual_completeness(valid, CALENDAR_YEAR, now=now)[0]

    assert result.expected_hours == len(index)
    assert "実行時点で終了していない最新年区分" in result.exclusion_reasons
