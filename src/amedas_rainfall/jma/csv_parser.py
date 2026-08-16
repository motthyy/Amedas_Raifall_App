"""気象庁「過去の気象データ・ダウンロード」CSVのパーサー。

現行の気象庁CSV形式（2026年7月時点、docs/jma_download.md参照）:
    行1: "ダウンロードした時刻：yyyy/mm/dd hh:mm:ss"
    行2: 空行
    行3: 地点名がデータ列ごとに繰り返される行（先頭は日付列の数だけ空セル）
    行4: 日付列見出し（"年,月,日,時" または "年月日時"）＋要素名（単位付き）
    行5: 副見出し行（値列は空、"現象なし情報"/"品質情報"/"均質番号"が該当列に入る）
    行6以降: データ行

本アプリのダウンロードクライアントは ``ymdLiteral=0`` を指定するため、日付は
年・月・日・時の4列に分割されて格納される（時は1〜24。24は「24:00」を表す）。
"""

from __future__ import annotations

import csv as csv_module
import io
import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)

JST = "Asia/Tokyo"

QUALITY_CODE_NORMAL = "8"
QUALITY_CODE_QUASI_NORMAL_ENOUGH = "5"
QUALITY_CODE_INSUFFICIENT = "4"
QUALITY_CODE_QUESTIONABLE = "2"
QUALITY_CODE_MISSING = "1"
QUALITY_CODE_NOT_APPLICABLE = "0"

DATE_PART_HEADERS = {"年", "月", "日", "時", "年月日時"}
QUALITY_LABEL = "品質情報"
HOMOGENEITY_LABEL = "均質番号"
PHENOMENON_LABEL = "現象なし情報"


class JmaCsvFormatError(ValueError):
    """気象庁CSVの想定外フォーマットを検出した際の例外。"""


@dataclass
class ParsedJmaCsv:
    """パース結果。"""

    frame: pd.DataFrame
    station_name: str | None
    download_timestamp_text: str | None


def _decode_bytes(raw: bytes) -> str:
    """CP932（Shift_JIS拡張）を優先し、失敗時はUTF-8にフォールバックしてデコードする。"""
    for encoding in ("cp932", "utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise JmaCsvFormatError("CP932/UTF-8のいずれでもデコードできませんでした。")


def _split_csv_lines(text: str) -> list[list[str]]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    reader = csv_module.reader(io.StringIO(text))
    return [row for row in reader]


def _find_date_header_row(rows: list[list[str]]) -> int:
    for i, row in enumerate(rows):
        if not row:
            continue
        first = row[0].strip()
        if first in ("年月日時", "年"):
            return i
    raise JmaCsvFormatError("日付見出し行（年月日時 または 年）が見つかりません。")


def _count_date_columns(header_row: list[str]) -> int:
    if header_row[0].strip() == "年月日時":
        return 1
    count = 0
    for cell in header_row:
        if cell.strip() in ("年", "月", "日", "時"):
            count += 1
        else:
            break
    return count


def parse_jma_hourly_precipitation_csv(raw_bytes: bytes) -> ParsedJmaCsv:
    """気象庁CSV（時別降水量、1地点・1要素）をパースする。

    Returns:
        ParsedJmaCsv: 正規化前の生データフレーム（列: datetime_jst, rainfall_raw_mm,
        quality_code, homogeneity_number, phenomenon_code）。
    """
    text = _decode_bytes(raw_bytes)
    rows = _split_csv_lines(text)
    rows = [r for r in rows if any(cell.strip() != "" for cell in r)]
    if not rows:
        raise JmaCsvFormatError("空のCSVです。")

    download_ts_text = rows[0][0] if rows and rows[0] else None

    date_header_idx = _find_date_header_row(rows)
    if date_header_idx < 1:
        raise JmaCsvFormatError("地点名行が見つかりません。")

    station_row = rows[date_header_idx - 1]
    station_name = next((c.strip() for c in station_row if c.strip()), None)

    date_header_row = rows[date_header_idx]
    n_date_cols = _count_date_columns(date_header_row)

    sub_header_row = rows[date_header_idx + 1] if date_header_idx + 1 < len(rows) else []
    data_start_idx = date_header_idx + 2

    element_cols = date_header_row[n_date_cols:]
    n_element_group_cols = len(element_cols)
    if n_element_group_cols not in (3, 4):
        raise JmaCsvFormatError(
            f"想定外の列数です（日付列={n_date_cols}, 要素列={n_element_group_cols}）。"
        )
    has_phenomenon = n_element_group_cols == 4

    sub_labels = [c.strip() for c in sub_header_row[n_date_cols:]] if sub_header_row else []

    if has_phenomenon:
        value_offset, phenom_offset, quality_offset, homog_offset = 0, 1, 2, 3
    else:
        value_offset, quality_offset, homog_offset = 0, 1, 2
        phenom_offset = None

    if sub_labels:
        for idx, label in enumerate(sub_labels):
            if label == QUALITY_LABEL:
                quality_offset = idx
            elif label == HOMOGENEITY_LABEL:
                homog_offset = idx
            elif label == PHENOMENON_LABEL:
                phenom_offset = idx
                has_phenomenon = True

    records = []
    for row_idx in range(data_start_idx, len(rows)):
        row = rows[row_idx]
        if len(row) < n_date_cols + n_element_group_cols:
            logger.warning("列数不足の行をスキップします: %s", row)
            continue

        date_cells = row[:n_date_cols]
        element_cells = row[n_date_cols : n_date_cols + n_element_group_cols]

        try:
            if n_date_cols == 4:
                year, month, day, hour = (int(c) for c in date_cells)
            else:
                year, month, day, hour_text = _parse_combined_datetime(date_cells[0])
                hour = hour_text
        except (ValueError, TypeError) as exc:
            logger.warning("日付列の解析に失敗した行をスキップします: %s (%s)", row, exc)
            continue

        value_text = element_cells[value_offset].strip()
        quality_text = element_cells[quality_offset].strip() if quality_offset < len(element_cells) else ""
        homog_text = element_cells[homog_offset].strip() if homog_offset < len(element_cells) else ""
        phenom_text = (
            element_cells[phenom_offset].strip()
            if has_phenomenon and phenom_offset is not None and phenom_offset < len(element_cells)
            else ""
        )

        value = _to_float_or_none(value_text)
        records.append(
            {
                "year": year,
                "month": month,
                "day": day,
                "hour": hour,
                "rainfall_raw_mm": value,
                "quality_code": quality_text or None,
                "homogeneity_number": _to_int_or_none(homog_text),
                "phenomenon_code": phenom_text or None,
            }
        )

    if not records:
        raise JmaCsvFormatError("データ行が1件も解析できませんでした。")

    df = pd.DataFrame.from_records(records)
    df["datetime_jst"] = df.apply(
        lambda r: _hour24_to_datetime(int(r["year"]), int(r["month"]), int(r["day"]), int(r["hour"])),
        axis=1,
    )
    df["datetime_jst"] = pd.to_datetime(df["datetime_jst"]).dt.tz_localize(
        JST, ambiguous="NaT", nonexistent="shift_forward"
    )
    df = df.drop(columns=["year", "month", "day", "hour"])
    df = df.set_index("datetime_jst").sort_index()

    return ParsedJmaCsv(frame=df, station_name=station_name, download_timestamp_text=download_ts_text)


def _parse_combined_datetime(text: str) -> tuple[int, int, int, int]:
    """"yyyy/m/d h:mm:ss" 形式（ymdLiteral=1相当）から年月日時を取り出す。"""
    text = text.strip()
    date_part, time_part = text.split(" ")
    year_s, month_s, day_s = date_part.split("/")
    hour_s = time_part.split(":")[0]
    return int(year_s), int(month_s), int(day_s), int(hour_s)


def _hour24_to_datetime(year: int, month: int, day: int, hour: int) -> pd.Timestamp:
    """24時表記を翌日0時へ正規化する（仕様6節）。"""
    if hour == 24:
        base = pd.Timestamp(year=year, month=month, day=day) + pd.Timedelta(days=1)
        return base
    if not 0 <= hour <= 23:
        raise JmaCsvFormatError(f"時の値が範囲外です: {hour}")
    return pd.Timestamp(year=year, month=month, day=day, hour=hour)


def _to_float_or_none(text: str) -> float | None:
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_int_or_none(text: str) -> int | None:
    if text == "":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def classify_quality(quality_code: str | None) -> str:
    """気象庁の品質情報コードをアプリ内の品質区分へ分類する。"""
    if quality_code is None:
        return "不明"
    if quality_code == QUALITY_CODE_NORMAL:
        return "正常"
    if quality_code in (QUALITY_CODE_QUASI_NORMAL_ENOUGH, QUALITY_CODE_INSUFFICIENT, QUALITY_CODE_QUESTIONABLE):
        return "準正常"
    if quality_code == QUALITY_CODE_MISSING:
        return "欠測"
    if quality_code == QUALITY_CODE_NOT_APPLICABLE:
        return "欠測"
    return "不明"
