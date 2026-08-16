from __future__ import annotations

import datetime as dt

import pytest

from amedas_rainfall.jma.start_date_finder import StartDateProbeError, find_earliest_valid_year


def _csv(year: int, has_data: bool) -> bytes:
    value = "1.0" if has_data else ""
    quality = "8" if has_data else "1"
    text = "\n".join(
        [
            "ダウンロードした時刻：2026/01/01 00:00:00",
            ",,,,テスト,テスト,テスト",
            "年,月,日,時,降水量(mm),降水量(mm),降水量(mm)",
            ",,,,,品質情報,均質番号",
            f"{year},1,1,1,{value},{quality},1",
        ]
    )
    return text.encode("cp932")


class _NonMonotonicClient:
    def __init__(self, valid_years: set[int]):
        self.valid_years = valid_years

    def download_hourly_precipitation_csv(
        self, stid, start_year, start_month, start_day, end_year, end_month, end_day
    ) -> bytes:
        return _csv(start_year, start_year in self.valid_years)


def test_forward_coarse_search_rescans_each_year_without_skipping_first_valid_year():
    # ヒント1974の次の粗探索点1984が有効でも、その間を年単位で再走査する。
    client = _NonMonotonicClient({1979, 1984})
    result = find_earliest_valid_year(
        client, "a0001", candidate_year_hint=1974, current_year=1990, wait_seconds=0
    )

    assert result.earliest_valid_datetime == dt.datetime(
        1979, 1, 1, 1, tzinfo=result.earliest_valid_datetime.tzinfo
    )
    assert 1979 in result.candidate_years_checked


def test_probe_transport_failure_is_not_misreported_as_no_observation_data():
    class BrokenClient:
        def download_hourly_precipitation_csv(self, *args, **kwargs):
            raise TimeoutError("timeout")

    with pytest.raises(StartDateProbeError):
        find_earliest_valid_year(
            BrokenClient(), "a0001", candidate_year_hint=1980, current_year=1990, wait_seconds=0
        )
