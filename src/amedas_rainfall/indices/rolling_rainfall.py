"""過去24時間移動雨量の計算（8.2節）。

各時刻を終端とする直近24時間（当該時刻を含む）の合計。
24時間分の有効データが揃わない場合（欠測を含む場合、または先頭で
データが24時間に満たない場合）はNaNとする。
"""

from __future__ import annotations

import pandas as pd

from amedas_rainfall.indicators import DEFAULT_ROLLING_COLUMN, rolling_column

ROLLING_COLUMN = DEFAULT_ROLLING_COLUMN


def calculate_rolling_rainfall(
    rainfall_raw_mm: pd.Series,
    window_hours: int = 24,
    column_name: str | None = None,
) -> pd.DataFrame:
    """直近window_hours時間の移動雨量合計を計算する。"""
    if window_hours <= 0:
        raise ValueError("window_hoursは1以上で指定してください。")
    output_column = column_name or rolling_column(window_hours)
    rolling_sum = rainfall_raw_mm.rolling(window=window_hours, min_periods=window_hours).sum()
    return pd.DataFrame({output_column: rolling_sum}, index=rainfall_raw_mm.index)
