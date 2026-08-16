"""時別降水量を取得できる最古の有効日時を探索する（4節）。

二分探索だけに依存せず、観測休止・長期欠測により「データの存在」が
年に対して単調にならない可能性を考慮した探索手順を実装する。

推奨探索手順（仕様書4節）に対応:
    1. 地点種別・メタデータから探索下限候補を取得
    2. 候補年の時別降水量が存在するか確認
    3. 10年単位→5年単位→1年単位で範囲を絞る
    4. 最古の有効年の前後を検証（安全マージンとして数年分を追加確認）
    5. その年のデータを取得
    6. 最初の有効な時別降水量日時を確定
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass, field
from typing import Protocol

from amedas_rainfall.jma.csv_parser import parse_jma_hourly_precipitation_csv

logger = logging.getLogger(__name__)

EARLIEST_POSSIBLE_YEAR = 1875
COARSE_STEPS = (10, 5, 1)
SAFETY_MARGIN_YEARS = 2


class StartDateProbeError(RuntimeError):
    """通信・CSV形式エラー。観測データなしとは区別して呼び出し側へ返す。"""


class SupportsCsvDownload(Protocol):
    def download_hourly_precipitation_csv(
        self,
        stid: str,
        start_year: int,
        start_month: int,
        start_day: int,
        end_year: int,
        end_month: int,
        end_day: int,
    ) -> bytes: ...


@dataclass
class StartDateSearchResult:
    earliest_valid_datetime: dt.datetime | None
    candidate_years_checked: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _probe_year_has_data(client: SupportsCsvDownload, station_code: str, year: int) -> bool:
    """指定年全体を取得し、欠測/非対象ではない雨量が存在するか確認する。"""
    try:
        raw = client.download_hourly_precipitation_csv(station_code, year, 1, 1, year, 12, 31)
        parsed = parse_jma_hourly_precipitation_csv(raw)
    except Exception as exc:
        logger.exception("探索用プローブに失敗しました（station=%s, year=%s）", station_code, year)
        raise StartDateProbeError(
            f"{year}年の確認に失敗しました。通信状態または気象庁CSV形式を確認してください。"
        ) from exc
    quality = parsed.frame["quality_code"]
    valid = parsed.frame["rainfall_raw_mm"].notna() & quality.notna() & ~quality.isin(["0", "1"])
    return bool(valid.any())


def find_earliest_valid_year(
    client: SupportsCsvDownload,
    station_code: str,
    candidate_year_hint: int,
    current_year: int,
    wait_seconds: float = 3.0,
) -> StartDateSearchResult:
    """時別降水量が最初に存在する年を探索する（非単調な欠測パターンを考慮）。"""
    checked: list[int] = []
    notes: list[str] = []
    probe_cache: dict[int, bool] = {}

    def probe(year: int) -> bool:
        year = max(EARLIEST_POSSIBLE_YEAR, min(year, current_year))
        if year in probe_cache:
            return probe_cache[year]
        result = _probe_year_has_data(client, station_code, year)
        probe_cache[year] = result
        checked.append(year)
        time.sleep(wait_seconds)
        return result

    hint = max(EARLIEST_POSSIBLE_YEAR, min(candidate_year_hint, current_year))
    hint_has_data = probe(hint)

    if hint_has_data:
        # 10年刻みで「データなし/あり」の境界を作り、その区間を年単位で必ず再走査する。
        high = hint
        low = EARLIEST_POSSIBLE_YEAR - 1
        candidate = hint - COARSE_STEPS[0]
        while candidate >= EARLIEST_POSSIBLE_YEAR:
            if probe(candidate):
                high = candidate
                candidate -= COARSE_STEPS[0]
            else:
                low = candidate
                break
        scan_start = max(EARLIEST_POSSIBLE_YEAR, low + 1)
        found = [year for year in range(scan_start, high + 1) if probe(year)]
        earliest_year = min(found) if found else high
        for candidate in range(
            max(EARLIEST_POSSIBLE_YEAR, low - SAFETY_MARGIN_YEARS), low + 1
        ):
            if probe(candidate):
                notes.append(f"粗探索境界より前の{candidate}年にもデータが存在しました。")
                earliest_year = min(earliest_year, candidate)
    else:
        # 前方の粗探索で見つかった上限までを1年ずつ再走査する。
        high: int | None = None
        candidate = hint + COARSE_STEPS[0]
        while candidate <= current_year:
            if probe(candidate):
                high = candidate
                break
            candidate += COARSE_STEPS[0]
        if high is None and probe(current_year):
            high = current_year
        if high is None:
            return StartDateSearchResult(
                earliest_valid_datetime=None,
                candidate_years_checked=checked,
                notes=["候補年から現在年までデータが確認できませんでした。手動で開始年を指定してください。"],
            )
        found = [year for year in range(hint + 1, high + 1) if probe(year)]
        earliest_year = min(found) if found else high

    earliest_valid_datetime = _find_first_valid_hour_in_year(client, station_code, earliest_year, wait_seconds)
    return StartDateSearchResult(
        earliest_valid_datetime=earliest_valid_datetime,
        candidate_years_checked=checked,
        notes=notes,
    )


def _find_first_valid_hour_in_year(
    client: SupportsCsvDownload, station_code: str, year: int, wait_seconds: float
) -> dt.datetime | None:
    try:
        raw = client.download_hourly_precipitation_csv(station_code, year, 1, 1, year, 12, 31)
        time.sleep(wait_seconds)
        parsed = parse_jma_hourly_precipitation_csv(raw)
    except Exception as exc:
        raise StartDateProbeError(f"{year}年の最初の有効時刻を確定できませんでした。") from exc
    valid = parsed.frame[
        parsed.frame["rainfall_raw_mm"].notna()
        & parsed.frame["quality_code"].notna()
        & ~parsed.frame["quality_code"].isin(["0", "1"])
    ]
    if valid.empty:
        return None
    return valid.index.min().to_pydatetime()


def default_start_year_hint(station_type_is_amedas: bool) -> int:
    """地点種別からの探索下限候補（一般的な観測開始年の目安）。"""
    # AMeDASは1974年11月から順次運用開始、気象官署はそれ以前から観測している場合が多い。
    return 1974 if station_type_is_amedas else 1875
