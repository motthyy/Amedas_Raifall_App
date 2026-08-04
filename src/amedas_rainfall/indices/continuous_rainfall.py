"""12時間無降雨リセット連続雨量の計算。

仕様（README/指示書 8.1節）:
    - 時雨量が0より大きい時刻から一連降雨を開始し、その雨量を累積する。
    - 無降雨が12時間未満の場合、途中で雨が止んでも同じ一連降雨として扱い、
      連続雨量を保持する（減算しない）。
    - 無降雨が12時間連続した時点で、連続雨量を0にリセットする。
    - リセット後、次に雨が降った時刻から新しい一連降雨（イベント）を開始する。

欠測（NaN）をまたぐ場合は、欠測直後の最初の有効値から状態量を0に再初期化し、
``state_reset_due_to_gap`` と ``warmup_flag`` を立てる。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CONTINUOUS_COLUMN = "continuous_rainfall_12h_mm"
DRY_HOURS_COLUMN = "dry_hours"
EVENT_ID_COLUMN = "rain_event_id"
EVENT_START_COLUMN = "rain_event_start"
EVENT_LAST_RAIN_COLUMN = "rain_event_last_rain"
RESET_DUE_TO_GAP_COLUMN = "state_reset_due_to_gap"
WARMUP_COLUMN = "warmup_flag"


def calculate_continuous_rainfall(
    rainfall_raw_mm: pd.Series,
    dry_hours_reset: int = 12,
) -> pd.DataFrame:
    """12時間無降雨リセット連続雨量を計算する。

    Args:
        rainfall_raw_mm: 時雨量（時間間隔=1時間の連続インデックス、
            欠測はNaNとして表現）。
        dry_hours_reset: 連続雨量をリセットする無降雨時間数（既定12時間）。

    Returns:
        入力と同じインデックスを持つDataFrame。
    """
    n = len(rainfall_raw_mm)
    index = rainfall_raw_mm.index
    values = rainfall_raw_mm.to_numpy(dtype=float)

    cum = np.full(n, np.nan)
    dry = np.full(n, np.nan)
    event_id = np.full(n, np.nan)
    event_start = np.full(n, pd.NaT, dtype=object)
    event_last_rain = np.full(n, pd.NaT, dtype=object)
    reset_due_to_gap = np.zeros(n, dtype=bool)
    warmup = np.zeros(n, dtype=bool)

    index_values = list(index)

    # 状態変数
    cur_cum = 0.0
    cur_dry = dry_hours_reset  # 開始前は「十分に乾燥している」とみなす
    cur_event_id = 0
    cur_event_start = pd.NaT
    cur_event_last_rain = pd.NaT
    pending_reset = True  # 最初の有効値はwarmupとして扱う

    for i in range(n):
        val = values[i]
        if np.isnan(val):
            pending_reset = True
            continue

        if pending_reset:
            cur_cum = 0.0
            cur_dry = dry_hours_reset
            cur_event_start = pd.NaT
            cur_event_last_rain = pd.NaT
            warmup[i] = True
            reset_due_to_gap[i] = True
            pending_reset = False

        if val > 0.0:
            if cur_dry >= dry_hours_reset:
                cur_event_id += 1
                cur_event_start = index_values[i]
            cur_cum = cur_cum + val
            cur_dry = 0
            cur_event_last_rain = index_values[i]
        else:
            cur_dry = cur_dry + 1
            if cur_dry >= dry_hours_reset:
                cur_cum = 0.0

        cum[i] = cur_cum
        dry[i] = cur_dry
        event_id[i] = cur_event_id if cur_event_id > 0 else np.nan
        event_start[i] = cur_event_start
        event_last_rain[i] = cur_event_last_rain

    result = pd.DataFrame(
        {
            CONTINUOUS_COLUMN: cum,
            DRY_HOURS_COLUMN: dry,
            EVENT_ID_COLUMN: event_id,
            EVENT_START_COLUMN: pd.Series(event_start, index=index),
            EVENT_LAST_RAIN_COLUMN: pd.Series(event_last_rain, index=index),
            RESET_DUE_TO_GAP_COLUMN: reset_due_to_gap,
            WARMUP_COLUMN: warmup,
        },
        index=index,
    )
    return result
