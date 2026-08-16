"""推定10分雨量と気象庁標準3段タンクモデルによる土壌雨量の計算（9節）。

重要な留意点（README/仕様書にも明記）:
    気象庁が公表する「土壌雨量指数」そのものではない。気象庁から取得可能なのは
    時別値のみであるため、1時間雨量を6等分して10分雨量を推定した上で、
    気象庁標準3段タンクモデルの係数体系を用いて独自に計算した値である。

出典:
    気象庁「土壌雨量指数」に関する技術資料に示される標準3段タンクモデルの
    構造（3段直列タンク、各タンクの側方流出孔・側方流出係数・底面浸透係数）
    に基づく。具体的な数値は ``config/tank_model.yaml`` にまとめ、
    docs/calculation_method.md に出典・計算順序を記載する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

try:
    from numba import njit

    NUMBA_AVAILABLE = True
except ImportError:  # 最小構成・開発環境では同じ関数を純Pythonで実行する。
    NUMBA_AVAILABLE = False

    def njit(function):  # type: ignore[misc]
        return function

TEN_MIN_COLUMN = "rainfall_10min_mm"
TANK1_COLUMN = "soil_tank_1_mm"
TANK2_COLUMN = "soil_tank_2_mm"
TANK3_COLUMN = "soil_tank_3_mm"
SOIL_INDEX_COLUMN = "soil_rainfall_mm"

ZERO_FLOOR_MM = 0.3


@njit
def _run_hourly_tank_kernel(
    values: np.ndarray,
    dt_hours: float,
    initial: np.ndarray,
    outlet_heights_1: np.ndarray,
    outlet_coefficients_1: np.ndarray,
    outlet_heights_2: np.ndarray,
    outlet_coefficients_2: np.ndarray,
    outlet_heights_3: np.ndarray,
    outlet_coefficients_3: np.ndarray,
    infiltration_coefficients: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Numbaでネイティブ化可能な、時別入力→6回更新の数値計算本体。"""
    n = len(values)
    s1 = np.full(n, np.nan)
    s2 = np.full(n, np.nan)
    s3 = np.full(n, np.nan)
    cur1, cur2, cur3 = initial[0], initial[1], initial[2]
    pending_reset = True

    for i in range(n):
        hourly_rain = values[i]
        if np.isnan(hourly_rain):
            pending_reset = True
            continue
        if pending_reset:
            cur1, cur2, cur3 = initial[0], initial[1], initial[2]
            pending_reset = False

        rain_10min = hourly_rain / 6.0
        for _ in range(6):
            storage1 = cur1 + rain_10min
            out1 = 0.0
            for j in range(len(outlet_heights_1)):
                if storage1 > outlet_heights_1[j]:
                    out1 += outlet_coefficients_1[j] * (storage1 - outlet_heights_1[j]) * dt_hours
            inf1 = infiltration_coefficients[0] * storage1 * dt_hours
            cur1 = max(storage1 - out1 - inf1, 0.0)

            storage2 = cur2 + inf1
            out2 = 0.0
            for j in range(len(outlet_heights_2)):
                if storage2 > outlet_heights_2[j]:
                    out2 += outlet_coefficients_2[j] * (storage2 - outlet_heights_2[j]) * dt_hours
            inf2 = infiltration_coefficients[1] * storage2 * dt_hours
            cur2 = max(storage2 - out2 - inf2, 0.0)

            storage3 = cur3 + inf2
            out3 = 0.0
            for j in range(len(outlet_heights_3)):
                if storage3 > outlet_heights_3[j]:
                    out3 += outlet_coefficients_3[j] * (storage3 - outlet_heights_3[j]) * dt_hours
            inf3 = infiltration_coefficients[2] * storage3 * dt_hours
            cur3 = max(storage3 - out3 - inf3, 0.0)

        s1[i], s2[i], s3[i] = cur1, cur2, cur3

    return s1, s2, s3


def disaggregate_hourly_to_10min(rainfall_raw_mm: pd.Series) -> pd.Series:
    """時雨量を10分雨量へ均等分配する（1時間雨量を6等分）。

    気象庁の時別値は「当該時刻までの1時間」の積算値であるため、時刻 t の
    値は (t-50分, t-40分, ..., t) の6個の10分値に等分配する。
    時雨量が0であれば6個とも0とする。欠測（NaN）の時間は6個ともNaNとする。
    """
    parts = []
    for offset_min in (50, 40, 30, 20, 10, 0):
        shifted_index = rainfall_raw_mm.index - pd.Timedelta(minutes=offset_min)
        part = pd.Series(rainfall_raw_mm.to_numpy() / 6.0, index=shifted_index)
        parts.append(part)
    combined = pd.concat(parts).sort_index()
    combined.name = TEN_MIN_COLUMN
    return combined


@dataclass
class TankOutlet:
    height_mm: float
    coefficient_per_hour: float


@dataclass
class TankSpec:
    outlets: list[TankOutlet]
    infiltration_coefficient_per_hour: float


@dataclass
class TankModelConfig:
    time_step_hours: float
    tank1: TankSpec
    tank2: TankSpec
    tank3: TankSpec
    initial_storage_mm: dict[str, float] = field(
        default_factory=lambda: {"tank1": 0.0, "tank2": 0.0, "tank3": 0.0}
    )

    @classmethod
    def from_dict(cls, raw: dict) -> "TankModelConfig":
        def _spec(key: str) -> TankSpec:
            node = raw[key]
            outlets = [
                TankOutlet(height_mm=o["height_mm"], coefficient_per_hour=o["coefficient_per_hour"])
                for o in node["outlets"]
            ]
            return TankSpec(
                outlets=outlets,
                infiltration_coefficient_per_hour=node["infiltration_coefficient_per_hour"],
            )

        config = cls(
            time_step_hours=raw.get("time_step_hours", 1.0 / 6.0),
            tank1=_spec("tank1"),
            tank2=_spec("tank2"),
            tank3=_spec("tank3"),
            initial_storage_mm=raw.get(
                "initial_storage_mm", {"tank1": 0.0, "tank2": 0.0, "tank3": 0.0}
            ),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.time_step_hours <= 0:
            raise ValueError("time_step_hoursは0より大きい値で指定してください。")
        for name, spec in (("tank1", self.tank1), ("tank2", self.tank2), ("tank3", self.tank3)):
            if spec.infiltration_coefficient_per_hour < 0:
                raise ValueError(f"{name}の浸透係数は0以上で指定してください。")
            for outlet in spec.outlets:
                if outlet.height_mm < 0 or outlet.coefficient_per_hour < 0:
                    raise ValueError(f"{name}の流出孔設定は0以上で指定してください。")


def _side_outflow_mm(storage_mm: float, spec: TankSpec, dt_hours: float) -> float:
    total = 0.0
    for outlet in spec.outlets:
        if storage_mm > outlet.height_mm:
            total += outlet.coefficient_per_hour * (storage_mm - outlet.height_mm) * dt_hours
    return total


def _infiltration_mm(storage_mm: float, spec: TankSpec, dt_hours: float) -> float:
    return spec.infiltration_coefficient_per_hour * storage_mm * dt_hours


def run_tank_model_10min(
    rainfall_10min_mm: pd.Series,
    config: TankModelConfig,
    progress_callback: "Callable[[float], None] | None" = None,
) -> pd.DataFrame:
    """10分刻みで3段タンクモデルを実行する。

    Args:
        progress_callback: 進捗（0.0〜1.0）を通知するコールバック。演算量が多いため
            （時別データ1件につき6ステップ）、呼び出し頻度は全体を約200分割した間隔に間引く。

    Returns:
        10分刻みのタンク貯留量・流出量・浸透量を持つDataFrame。
    """
    n = len(rainfall_10min_mm)
    dt_hours = config.time_step_hours
    index = rainfall_10min_mm.index
    values = rainfall_10min_mm.to_numpy(dtype=float)

    s1 = np.full(n, np.nan)
    s2 = np.full(n, np.nan)
    s3 = np.full(n, np.nan)
    outflow1 = np.full(n, np.nan)
    outflow2 = np.full(n, np.nan)
    outflow3 = np.full(n, np.nan)
    infil1 = np.full(n, np.nan)
    infil2 = np.full(n, np.nan)
    infil3 = np.full(n, np.nan)

    cur1 = config.initial_storage_mm.get("tank1", 0.0)
    cur2 = config.initial_storage_mm.get("tank2", 0.0)
    cur3 = config.initial_storage_mm.get("tank3", 0.0)
    pending_reset = True

    report_interval = max(1, n // 200)

    for i in range(n):
        if progress_callback is not None and i % report_interval == 0:
            progress_callback(i / n)
        rain = values[i]
        if np.isnan(rain):
            pending_reset = True
            continue
        if pending_reset:
            cur1 = config.initial_storage_mm.get("tank1", 0.0)
            cur2 = config.initial_storage_mm.get("tank2", 0.0)
            cur3 = config.initial_storage_mm.get("tank3", 0.0)
            pending_reset = False

        # タンク1: 入力は降雨
        storage1 = cur1 + rain
        out1 = _side_outflow_mm(storage1, config.tank1, dt_hours)
        inf1 = _infiltration_mm(storage1, config.tank1, dt_hours)
        cur1 = max(storage1 - out1 - inf1, 0.0)

        # タンク2: 入力はタンク1の浸透
        storage2 = cur2 + inf1
        out2 = _side_outflow_mm(storage2, config.tank2, dt_hours)
        inf2 = _infiltration_mm(storage2, config.tank2, dt_hours)
        cur2 = max(storage2 - out2 - inf2, 0.0)

        # タンク3: 入力はタンク2の浸透
        storage3 = cur3 + inf2
        out3 = _side_outflow_mm(storage3, config.tank3, dt_hours)
        inf3 = _infiltration_mm(storage3, config.tank3, dt_hours)
        cur3 = max(storage3 - out3 - inf3, 0.0)

        s1[i] = cur1
        s2[i] = cur2
        s3[i] = cur3
        outflow1[i] = out1
        outflow2[i] = out2
        outflow3[i] = out3
        infil1[i] = inf1
        infil2[i] = inf2
        infil3[i] = inf3

    return pd.DataFrame(
        {
            TANK1_COLUMN: s1,
            TANK2_COLUMN: s2,
            TANK3_COLUMN: s3,
            "tank1_outflow_mm": outflow1,
            "tank2_outflow_mm": outflow2,
            "tank3_outflow_mm": outflow3,
            "tank1_infiltration_mm": infil1,
            "tank2_infiltration_mm": infil2,
            "tank3_infiltration_mm": infil3,
        },
        index=index,
    )


def aggregate_tank_result_to_hourly(tank_10min: pd.DataFrame) -> pd.DataFrame:
    """10分刻みのタンク計算結果を、各時間の6回目更新後の値として時別化する。

    土壌雨量（3タンク合計）は理論上0に漸近し続けるだけで厳密には0にならない
    ため、0.3mm未満になった時点で0に丸める（``ZERO_FLOOR_MM``）。この丸めは
    出力値のみに適用し、各タンクの内部貯留量（タンク間の流出・浸透の計算）
    には影響しない。
    """
    hourly = tank_10min.resample("h", label="right", closed="right").last()
    total = hourly[TANK1_COLUMN] + hourly[TANK2_COLUMN] + hourly[TANK3_COLUMN]
    hourly[SOIL_INDEX_COLUMN] = total.where(total.isna() | (total >= ZERO_FLOOR_MM), other=0.0)
    return hourly[[TANK1_COLUMN, TANK2_COLUMN, TANK3_COLUMN, SOIL_INDEX_COLUMN]]


def calculate_soil_rainfall(
    rainfall_raw_mm: pd.Series,
    config: TankModelConfig,
    progress_callback: Callable[[float], None] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """推定10分雨量からタンクモデルを実行し、10分値と時別値の両方を返す。

    Returns:
        (10分刻みDataFrame, 時別DataFrame) のタプル。
    """
    rainfall_10min = disaggregate_hourly_to_10min(rainfall_raw_mm)
    tank_10min = run_tank_model_10min(rainfall_10min, config, progress_callback=progress_callback)
    tank_hourly = aggregate_tank_result_to_hourly(tank_10min)
    return tank_10min, tank_hourly


def calculate_soil_rainfall_hourly(
    rainfall_raw_mm: pd.Series,
    config: TankModelConfig,
    progress_callback: Callable[[float], None] | None = None,
) -> pd.DataFrame:
    """10分値の巨大な中間DataFrameを保持せず、時別タンク値だけを計算する。

    各時雨量を6等分して逐次更新する点は ``calculate_soil_rainfall`` と同一。
    Pythonアプリの通常パイプラインではこの省メモリ版を使用する。
    """
    config.validate()
    values = rainfall_raw_mm.to_numpy(dtype=float)
    if progress_callback is not None:
        progress_callback(0.0)
    initial = np.asarray(
        [
            config.initial_storage_mm.get("tank1", 0.0),
            config.initial_storage_mm.get("tank2", 0.0),
            config.initial_storage_mm.get("tank3", 0.0),
        ],
        dtype=float,
    )

    def _outlets(spec: TankSpec) -> tuple[np.ndarray, np.ndarray]:
        return (
            np.asarray([outlet.height_mm for outlet in spec.outlets], dtype=float),
            np.asarray([outlet.coefficient_per_hour for outlet in spec.outlets], dtype=float),
        )

    heights1, coefficients1 = _outlets(config.tank1)
    heights2, coefficients2 = _outlets(config.tank2)
    heights3, coefficients3 = _outlets(config.tank3)
    infiltration_coefficients = np.asarray(
        [
            config.tank1.infiltration_coefficient_per_hour,
            config.tank2.infiltration_coefficient_per_hour,
            config.tank3.infiltration_coefficient_per_hour,
        ],
        dtype=float,
    )
    s1, s2, s3 = _run_hourly_tank_kernel(
        values,
        config.time_step_hours,
        initial,
        heights1,
        coefficients1,
        heights2,
        coefficients2,
        heights3,
        coefficients3,
        infiltration_coefficients,
    )
    if progress_callback is not None:
        progress_callback(1.0)

    total = s1 + s2 + s3
    total = np.where(np.isnan(total) | (total >= ZERO_FLOOR_MM), total, 0.0)
    return pd.DataFrame(
        {
            TANK1_COLUMN: s1,
            TANK2_COLUMN: s2,
            TANK3_COLUMN: s3,
            SOIL_INDEX_COLUMN: total,
        },
        index=rainfall_raw_mm.index,
    )
