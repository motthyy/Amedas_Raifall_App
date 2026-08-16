"""Excel出力のタイムゾーン処理に関する回帰テスト。

実際の運用で発生した不具合: タイムゾーン付き日時列（地点マスタのmetadata_fetched_at、
年最大値のmax_datetime、時別データの日時インデックス等）を含むDataFrameをExcelへ
書き出そうとすると、xlsxwriterが「Excel does not support datetimes with timezones」で
例外を送出していた。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from amedas_rainfall.reporting import _strip_timezone_for_excel, build_full_excel_workbook


def test_strip_timezone_removes_tz_from_index() -> None:
    idx = pd.date_range("2020-01-01", periods=3, freq="h", tz="Asia/Tokyo")
    df = pd.DataFrame({"value": [1, 2, 3]}, index=idx)
    result = _strip_timezone_for_excel(df)
    assert result.index.tz is None
    assert list(result.index) == [ts.tz_localize(None) for ts in idx]


def test_strip_timezone_removes_tz_from_datetime_column() -> None:
    df = pd.DataFrame(
        {
            "max_datetime": pd.to_datetime(["2020-01-01 12:00", "2021-06-01 08:00"]).tz_localize(
                "Asia/Tokyo"
            ),
            "value": [1.0, 2.0],
        }
    )
    result = _strip_timezone_for_excel(df)
    assert result["max_datetime"].dt.tz is None


def test_strip_timezone_handles_object_column_with_tz_aware_datetimes() -> None:
    import datetime as dt

    tz = dt.timezone(dt.timedelta(hours=9))
    df = pd.DataFrame({"metadata_fetched_at": [dt.datetime(2026, 1, 1, tzinfo=tz)]})
    result = _strip_timezone_for_excel(df)
    assert result["metadata_fetched_at"].iloc[0].tzinfo is None


def test_build_full_excel_workbook_with_timezone_aware_data_does_not_raise(tmp_path: Path) -> None:
    idx = pd.date_range("2020-01-01", periods=5, freq="h", tz="Asia/Tokyo")
    hourly_df = pd.DataFrame({"rainfall_raw_mm": [0.0, 1.0, 2.0, 0.0, 0.0]}, index=idx)

    station_info = pd.DataFrame(
        [{"station_code": "a0001", "metadata_fetched_at": pd.Timestamp("2026-01-01", tz="Asia/Tokyo")}]
    )
    maxima = pd.DataFrame(
        {
            "year_label": ["2020年"],
            "max_value": [2.0],
            "max_datetime": pd.to_datetime(["2020-01-01 03:00"]).tz_localize("Asia/Tokyo"),
        }
    )

    out_path = tmp_path / "workbook.xlsx"
    result_path = build_full_excel_workbook(
        out_path,
        station_info,
        hourly_df,
        {"calendar": maxima},
        pd.DataFrame({"確率年": [10], "確率雨量[mm]": [50.0]}),
        pd.DataFrame({"mu": [1.0], "beta": [1.0]}),
        pd.DataFrame(columns=["year_label", "除外理由"]),
        hourly_df.iloc[:0],
        pd.DataFrame({"項目": ["a"], "値": ["b"]}),
    )
    assert result_path.exists()
    assert result_path.stat().st_size > 0
