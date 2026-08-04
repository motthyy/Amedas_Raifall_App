"""実効雨量（半減期減衰型指数平滑雨量）の計算（8.3節、8.4節）。

半減期 H [時間] に対する1時間あたりの残存率:
    a_H = 0.5 ** (1 / H)

漸化式:
    E_H(t) = r_t + a_H * E_H(t-1)

観測開始時（および欠測明けの最初の有効時刻）の初期値は0とし、
``state_reset_due_to_gap`` / ``warmup_flag`` を付与する。

値は理論上0に漸近し続けるだけで厳密には0にならないため、0.3mm未満に
なった時点で0に丸める（``ZERO_FLOOR_MM``）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

RESET_DUE_TO_GAP_COLUMN = "state_reset_due_to_gap"
WARMUP_COLUMN = "warmup_flag"

ZERO_FLOOR_MM = 0.3


def half_life_to_decay_rate(half_life_hours: float) -> float:
    """半減期から1時間あたりの残存率 a_H を求める。"""
    return 0.5 ** (1.0 / half_life_hours)


def calculate_effective_rainfall(
    rainfall_raw_mm: pd.Series,
    half_life_hours: float,
    column_name: str,
) -> pd.DataFrame:
    """指定した半減期の実効雨量を計算する。

    Args:
        rainfall_raw_mm: 時雨量（1時間間隔の連続インデックス、欠測はNaN）。
        half_life_hours: 半減期[時間]。
        column_name: 出力列名。

    Returns:
        実効雨量列と状態フラグ列を持つDataFrame。値は理論上0に漸近し続けるが、
        ``ZERO_FLOOR_MM`` (0.3mm) 未満になった時点で0に丸める。
    """
    decay = half_life_to_decay_rate(half_life_hours)
    n = len(rainfall_raw_mm)
    index = rainfall_raw_mm.index
    values = rainfall_raw_mm.to_numpy(dtype=float)

    out = np.full(n, np.nan)
    reset_due_to_gap = np.zeros(n, dtype=bool)
    warmup = np.zeros(n, dtype=bool)

    prev = 0.0
    pending_reset = True
    for i in range(n):
        val = values[i]
        if np.isnan(val):
            pending_reset = True
            continue
        if pending_reset:
            prev = 0.0
            warmup[i] = True
            reset_due_to_gap[i] = True
            pending_reset = False
            cur = val + decay * prev
        else:
            cur = val + decay * prev
        if cur < ZERO_FLOOR_MM:
            cur = 0.0
        out[i] = cur
        prev = cur

    result = pd.DataFrame(
        {
            column_name: out,
            RESET_DUE_TO_GAP_COLUMN: reset_due_to_gap,
            WARMUP_COLUMN: warmup,
        },
        index=index,
    )
    return result


def calculate_all_effective_rainfall(
    rainfall_raw_mm: pd.Series,
    half_lives_hours: list[float] | None = None,
    column_map: dict[float, str] | None = None,
) -> pd.DataFrame:
    """複数の半減期について実効雨量をまとめて計算する。"""
    half_lives_hours = half_lives_hours or [3.0, 6.0, 24.0]
    column_map = column_map or {
        3.0: "effective_rainfall_3h_mm",
        6.0: "effective_rainfall_6h_mm",
        24.0: "effective_rainfall_24h_mm",
    }
    frames = []
    state_reset = None
    warmup = None
    for hl in half_lives_hours:
        col = column_map.get(hl, f"effective_rainfall_{hl}h_mm")
        df = calculate_effective_rainfall(rainfall_raw_mm, hl, col)
        frames.append(df[[col]])
        if state_reset is None:
            state_reset = df[RESET_DUE_TO_GAP_COLUMN]
            warmup = df[WARMUP_COLUMN]
    combined = pd.concat(frames, axis=1)
    combined[RESET_DUE_TO_GAP_COLUMN] = state_reset
    combined[WARMUP_COLUMN] = warmup
    return combined
