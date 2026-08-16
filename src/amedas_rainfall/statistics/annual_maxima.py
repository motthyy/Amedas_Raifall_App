"""3種類の年区切りによる年最大値・データ完全性の計算（10節）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from amedas_rainfall.models import AnnualCompleteness, YearBoundaryDefinition

CALENDAR_YEAR = YearBoundaryDefinition(key="calendar", label="暦年", start_month=1, start_day=1)
FISCAL_YEAR = YearBoundaryDefinition(key="fiscal", label="年度", start_month=4, start_day=1)
JUNE_START_YEAR = YearBoundaryDefinition(
    key="june_start", label="6月始まり年", start_month=6, start_day=1
)

ALL_YEAR_BOUNDARIES: dict[str, YearBoundaryDefinition] = {
    "calendar": CALENDAR_YEAR,
    "fiscal": FISCAL_YEAR,
    "june_start": JUNE_START_YEAR,
}


def _start_year_for_timestamp(ts: pd.Timestamp, boundary: YearBoundaryDefinition) -> int:
    # 気象庁の時別値は「その時刻までの直前1時間」を表し、24時は翌日0時へ
    # 正規化している。区切り日の0時は前日24時の観測なので前の年区分へ入れる。
    observation_period = ts - pd.Timedelta(nanoseconds=1)
    if (observation_period.month, observation_period.day) >= (
        boundary.start_month,
        boundary.start_day,
    ):
        return observation_period.year
    return observation_period.year - 1


def year_label(start_year: int, boundary: YearBoundaryDefinition) -> str:
    if boundary.key == "calendar":
        return f"{start_year}年"
    if boundary.key == "fiscal":
        return f"{start_year}年度"
    if boundary.key == "june_start":
        return f"{start_year}年6月始まり"
    return f"{start_year}年（{boundary.label}）"


def year_window(start_year: int, boundary: YearBoundaryDefinition, tz: str = "Asia/Tokyo") -> tuple[pd.Timestamp, pd.Timestamp]:
    """区切り境界を返す。時別観測の対象範囲は(start, end]。"""
    start = pd.Timestamp(
        year=start_year, month=boundary.start_month, day=boundary.start_day, hour=0, tz=tz
    )
    end = pd.Timestamp(
        year=start_year + 1, month=boundary.start_month, day=boundary.start_day, hour=0, tz=tz
    )
    return start, end


def assign_year_labels(index: pd.DatetimeIndex, boundary: YearBoundaryDefinition) -> pd.Series:
    """各時刻がどの年区分に属するかのラベル系列を返す。"""
    start_years = [_start_year_for_timestamp(ts, boundary) for ts in index]
    labels = [year_label(y, boundary) for y in start_years]
    return pd.Series(labels, index=index, name="year_label")


def calculate_annual_maxima(
    series: pd.Series,
    boundary: YearBoundaryDefinition,
) -> pd.DataFrame:
    """指定した年区分での年最大値とその発生日時を計算する。"""
    if series.empty:
        return pd.DataFrame(columns=["year_label", "start_year", "max_value", "max_datetime"])

    labels = assign_year_labels(series.index, boundary)
    df = pd.DataFrame({"value": series, "year_label": labels})

    records = []
    for lbl, group in df.groupby("year_label", sort=False):
        valid = group["value"].dropna()
        if valid.empty:
            max_value = np.nan
            max_dt: pd.Timestamp | None = None
        else:
            max_dt = valid.idxmax()
            max_value = valid.loc[max_dt]
        start_year = int(str(lbl).split("年")[0])
        records.append(
            {
                "year_label": lbl,
                "start_year": start_year,
                "max_value": max_value,
                "max_datetime": max_dt,
            }
        )
    result = pd.DataFrame(records).sort_values("start_year").reset_index(drop=True)
    return result


def calculate_annual_completeness(
    valid_mask: pd.Series,
    boundary: YearBoundaryDefinition,
    state_reset_mask: pd.Series | None = None,
    completeness_threshold_percent: float = 95.0,
    data_start: pd.Timestamp | None = None,
    data_end: pd.Timestamp | None = None,
    now: pd.Timestamp | None = None,
    exclude_incomplete_start_year: bool = True,
    exclude_incomplete_end_year: bool = True,
    exclude_ongoing_latest_year: bool = True,
    exclude_below_completeness: bool = True,
    exclude_state_reset_unreliable: bool = True,
) -> list[AnnualCompleteness]:
    """年区分ごとのデータ完全性を評価し、既定の採否判定を付ける。

    Args:
        valid_mask: 有効（欠測でない）時刻はTrueの真偽値系列（連続1時間インデックス）。
        boundary: 年区切り定義。
        state_reset_mask: 状態量再初期化（欠測明け）が発生した時刻でTrueの系列。
        completeness_threshold_percent: 採用可否の既定閾値。
        data_start: 観測データの実際の開始時刻（先頭不完全年判定に使用）。
        data_end: 観測データの実際の終了時刻。
        now: 現在時刻（最新未終了年区分の判定に使用）。
    """
    if valid_mask.empty:
        return []

    index = valid_mask.index
    tz = index.tz
    data_start = data_start or index.min()
    data_end = data_end or index.max()
    now = now or pd.Timestamp.now(tz=tz)

    start_year_min = _start_year_for_timestamp(data_start, boundary)
    start_year_max = _start_year_for_timestamp(data_end, boundary)

    results: list[AnnualCompleteness] = []
    for start_year in range(start_year_min, start_year_max + 1):
        win_start, win_end = year_window(start_year, boundary, tz=str(tz))
        if win_start >= now:
            continue
        expected_start = win_start + pd.Timedelta(hours=1)
        expected_end = min(win_end, now.floor("h"))
        expected_index = pd.date_range(
            start=expected_start,
            end=expected_end,
            freq="h",
            inclusive="both",
            tz=str(tz),
        )
        expected_hours = len(expected_index)
        if expected_hours == 0:
            continue
        window_valid = valid_mask.reindex(expected_index, fill_value=False).fillna(False).astype(bool)
        valid_hours = int(window_valid.sum())
        missing_hours = expected_hours - valid_hours
        completeness = 100.0 * valid_hours / expected_hours if expected_hours else 0.0
        has_reset = (
            bool(state_reset_mask.reindex(expected_index, fill_value=False).fillna(False).any())
            if state_reset_mask is not None
            else False
        )

        reasons: list[str] = []
        is_incomplete_start = expected_start < data_start <= win_end
        is_incomplete_end = expected_start <= data_end < win_end and win_end <= now
        is_ongoing_latest = win_end > now
        if is_incomplete_start and exclude_incomplete_start_year:
            reasons.append("観測開始を含む不完全年")
        if is_incomplete_end and exclude_incomplete_end_year:
            reasons.append("データ終了を含む不完全年")
        if is_ongoing_latest and exclude_ongoing_latest_year:
            reasons.append("実行時点で終了していない最新年区分")
        if completeness < completeness_threshold_percent and exclude_below_completeness:
            reasons.append(f"データ完全率が{completeness_threshold_percent}%未満")
        if has_reset and exclude_state_reset_unreliable:
            reasons.append("大きな欠測により状態量が再初期化された区間を含む")

        is_eligible = len(reasons) == 0

        results.append(
            AnnualCompleteness(
                year_label=year_label(start_year, boundary),
                start_datetime=win_start.to_pydatetime(),
                end_datetime=win_end.to_pydatetime(),
                expected_hours=expected_hours,
                valid_hours=valid_hours,
                missing_hours=missing_hours,
                completeness_percent=completeness,
                has_state_reset=has_reset,
                is_eligible_default=is_eligible,
                exclusion_reasons=reasons,
            )
        )
    return results
