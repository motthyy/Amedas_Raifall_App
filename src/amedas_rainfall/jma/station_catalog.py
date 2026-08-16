"""気象庁の地点マスタ（都道府県・地点一覧）の取得とローカルキャッシュ管理（3.1節）。"""

from __future__ import annotations

import datetime as dt
import logging
import time
from pathlib import Path
from typing import Callable

import pandas as pd

from amedas_rainfall.jma.direct_client import JmaDirectClient, RawStationEntry
from amedas_rainfall.models import Station, StationType
from amedas_rainfall.storage.files import atomic_write_parquet

logger = logging.getLogger(__name__)


class StationMasterBuildError(RuntimeError):
    """一部地域の取得失敗により完全な地点マスタを構築できない。"""

STATION_MASTER_COLUMNS = [
    "prefecture",
    "station_name",
    "station_code",
    "request_prec_no",
    "request_block_no",
    "station_type",
    "latitude",
    "longitude",
    "elevation_m",
    "is_currently_observing",
    "is_discontinued",
    "has_precipitation_observation",
    "metadata_fetched_at",
    "discontinued_date_text",
    "precip_start_date",
]


def _station_type_for(entry: RawStationEntry) -> str:
    if entry.is_observatory:
        return StationType.OBSERVATORY.value
    if entry.is_amedas:
        return StationType.AMEDAS.value
    return StationType.OTHER.value


def _raw_entry_to_row(entry: RawStationEntry, prefecture_label: str, fetched_at: dt.datetime) -> dict:
    display_name = entry.name_from_title or entry.stname
    return {
        "prefecture": prefecture_label,
        "station_name": display_name,
        "station_code": entry.stid,
        "request_prec_no": entry.prid,
        "request_block_no": entry.stid,
        "station_type": _station_type_for(entry),
        "latitude": entry.latitude,
        "longitude": entry.longitude,
        "elevation_m": entry.elevation_m,
        "is_currently_observing": not entry.is_discontinued,
        "is_discontinued": entry.is_discontinued,
        "has_precipitation_observation": entry.observes_precipitation,
        "metadata_fetched_at": fetched_at,
        "discontinued_date_text": entry.discontinued_text,
    }


def build_station_master(
    client: JmaDirectClient,
    wait_seconds: float = 3.0,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> pd.DataFrame:
    """全都道府県を巡回して地点マスタを構築する。

    気象庁サイトへの負荷対策として、都道府県ごとのリクエスト間に
    ``wait_seconds`` 秒の待機を挟む（直列実行、並列アクセスは行わない）。
    """
    fetched_at = dt.datetime.now(tz=dt.timezone(dt.timedelta(hours=9)))
    prefectures = client.fetch_prefecture_codes()
    rows: list[dict] = []
    failed_prefectures: list[str] = []
    total = len(prefectures)
    for i, (prid, label) in enumerate(prefectures):
        if progress_callback:
            progress_callback(i + 1, total, label)
        try:
            entries = client.fetch_stations_for_prefecture(prid)
        except Exception:
            logger.exception("都道府県コード %s の地点一覧取得に失敗しました。", prid)
            failed_prefectures.append(f"{label}({prid})")
            entries = []
        for entry in entries:
            rows.append(_raw_entry_to_row(entry, label, fetched_at))
        if i < total - 1:
            time.sleep(wait_seconds)

    if failed_prefectures:
        raise StationMasterBuildError(
            "地点一覧を取得できなかった地域があります。既存キャッシュは維持されます: "
            + "、".join(failed_prefectures)
        )
    df = pd.DataFrame(rows, columns=STATION_MASTER_COLUMNS)
    df = df.drop_duplicates(subset=["station_code"]).reset_index(drop=True)
    return df


def save_station_master(df: pd.DataFrame, path: Path) -> None:
    atomic_write_parquet(df, path, index=False)


def load_station_master(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)


def station_master_cache_exists(path: Path) -> bool:
    return path.exists()


def row_to_station(row: pd.Series) -> Station:
    precip_start_date = row["precip_start_date"] if "precip_start_date" in row else None
    hourly_precip_start_hint = None
    if precip_start_date is not None and pd.notna(precip_start_date):
        if isinstance(precip_start_date, dt.date) and not isinstance(precip_start_date, dt.datetime):
            hourly_precip_start_hint = precip_start_date
        else:
            hourly_precip_start_hint = pd.Timestamp(precip_start_date).date()

    return Station(
        prefecture=row["prefecture"],
        station_name=row["station_name"],
        station_code=row["station_code"],
        request_prec_no=row["request_prec_no"],
        request_block_no=row["request_block_no"],
        station_type=StationType(row["station_type"]),
        latitude=row["latitude"] if pd.notna(row["latitude"]) else None,
        longitude=row["longitude"] if pd.notna(row["longitude"]) else None,
        elevation_m=row["elevation_m"] if pd.notna(row["elevation_m"]) else None,
        is_currently_observing=bool(row["is_currently_observing"]),
        is_discontinued=bool(row["is_discontinued"]),
        has_precipitation_observation=bool(row["has_precipitation_observation"]),
        metadata_fetched_at=row["metadata_fetched_at"],
        hourly_precip_start_hint=hourly_precip_start_hint,
    )
