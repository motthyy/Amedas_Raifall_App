"""時別降水量データの正規化処理（7節、24:00表記の正規化など）。"""

from __future__ import annotations

import datetime as dt

import pandas as pd

JST = "Asia/Tokyo"

RAW_COLUMN = "rainfall_raw_mm"


def normalize_24h_timestamp(date_part: dt.date, hour_text: str) -> dt.datetime:
    """気象庁CSVの「24:00」表記を翌日「00:00」へ正規化する。

    気象庁の時別値は各時刻の「終わり」を表す1〜24時表記が使われることがある。
    24時は当日24:00＝翌日00:00として扱う。
    """
    hour_text = hour_text.strip()
    hour = int(hour_text.split(":")[0])
    if hour == 24:
        base = dt.datetime.combine(date_part, dt.time(0, 0)) + dt.timedelta(days=1)
    else:
        base = dt.datetime.combine(date_part, dt.time(hour % 24, 0))
    return base


def build_continuous_hourly_index(
    start: dt.datetime,
    end: dt.datetime,
    tz: str = JST,
) -> pd.DatetimeIndex:
    """開始・終了時刻を含む1時間刻みの連続インデックスを生成する。"""
    return pd.date_range(start=start, end=end, freq="h", tz=tz)


def reindex_to_continuous_hourly(df: pd.DataFrame, tz: str = JST) -> pd.DataFrame:
    """既存データを、期間内の連続1時間インデックスへ再インデックスする。

    元々存在しない時刻はすべての値がNaN（欠測）として扱われる。
    """
    if df.empty:
        return df
    full_index = build_continuous_hourly_index(df.index.min(), df.index.max(), tz=tz)
    return df.reindex(full_index)
