"""気象庁「地域気象観測所一覧」PDFの取得・解析。

https://www.jma.go.jp/jma/kishou/know/amedas/ame_master.pdf には、全国の
アメダス観測所・気象官署について、観測種目ごとの観測開始年月日が官公式情報として
記載されている。

地点マスタ（station_catalog.py）の実測プロービング（start_date_finder.py）は
気象庁サイトへ実際に年全体のCSVをリクエストして有効値を探す。通常は本PDFに記載された
公式の観測開始年月日を初期値として利用し、必要な場合だけ実測探索で確認する。

PDF内の「観測開始年月日」欄の表記規則（PDF内の凡例および実データ確認による）:
    - 単純な日付のみ（例 ``昭52.10.19``）: 降水量を含む全観測要素がその日付から開始。
    - ``#`` 単独（例 ``#``）: 降水量の観測はアメダス運用開始日
      （``AMEDAS_LAUNCH_DATE`` = 1974-11-01）から開始（降水量のみを観測する
      「雨」種別の地点で見られる表記）。
    - ``#`` + 日付（例 ``#昭52.10.14``）: 降水量の観測は運用開始日（1974-11-01）から
      開始しており、表示されている日付は気温・風向風速等「他の要素」の観測開始日。
    - 括弧付き日付 + 日付（例 ``(昭50.5.29)昭52.10.24``）: 括弧内の日付が降水量の
      観測開始日、括弧外の日付が「他の要素」の観測開始日。
    - 「雪」種別（積雪深計のみ）: 降水量観測を行わないため対象外。
"""

from __future__ import annotations

import datetime as dt
import io
import logging
import re
from pathlib import Path

import pandas as pd
import pdfplumber
import requests

from amedas_rainfall.jma.ca_bundle import ensure_ca_bundle_path
from amedas_rainfall.storage.files import atomic_write_parquet

logger = logging.getLogger(__name__)

DEFAULT_URL = "https://www.jma.go.jp/jma/kishou/know/amedas/ame_master.pdf"
DEFAULT_USER_AGENT = (
    "amedas-rainfall-research-tool/0.1 (contact: local-research-use; "
    "respects JMA terms of use; low-frequency automated access)"
)
DEFAULT_TIMEOUT_SECONDS = 30.0

AMEDAS_LAUNCH_DATE = dt.date(1974, 11, 1)
"""アメダス運用開始日。「#」表記の降水量観測開始日として使用する。"""

NO_PRECIPITATION_TYPES = {"雪"}
"""降水量観測を行わない種類（積雪深計のみ設置）。"""

AME_MASTER_PDF_COLUMNS = [
    "prefecture",
    "station_number",
    "station_type",
    "station_name",
    "raw_start_date_text",
    "precip_start_date",
    "other_elements_start_date",
]

_HEADER_KEYS: dict[str, list[str]] = {
    "prefecture": ["都府県", "振興局"],
    "station_number": ["観測所", "番号"],
    "station_type": ["種類"],
    "station_name": ["観測所名"],
    "start_date": ["観測開始", "年月日"],
}
_REQUIRED_HEADER_FIELDS = {"station_number", "station_type", "station_name", "start_date"}

_STATION_NUMBER_RE = re.compile(r"^\d{5}$")
_ERA_FIRST_YEAR = {"明": 1868, "大": 1912, "昭": 1926, "平": 1989, "令": 2019}
_ERA_DATE_FRAGMENT = r"([明大昭平令])\.?(元|\d{1,2})\.(\d{1,2})\.(\d{1,2})"
_SIMPLE_DATE_RE = re.compile(rf"^(#?){_ERA_DATE_FRAGMENT}$")
_PAREN_DATE_RE = re.compile(rf"^\({_ERA_DATE_FRAGMENT}\){_ERA_DATE_FRAGMENT}$")
_HASH_ONLY_RE = re.compile(r"^#$")


class AmeMasterPdfError(RuntimeError):
    """観測所一覧PDFの取得・解析エラー。"""


def fetch_ame_master_pdf_bytes(
    url: str = DEFAULT_URL,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> bytes:
    """観測所一覧PDFを取得する。"""
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    ca_bundle_path = ensure_ca_bundle_path()
    if ca_bundle_path is not None:
        session.verify = ca_bundle_path
    resp = session.get(url, timeout=timeout_seconds)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    if "pdf" not in content_type and not resp.content.startswith(b"%PDF"):
        raise AmeMasterPdfError(f"想定外のContent-Typeです: {content_type}")
    return resp.content


def _normalize_cell(cell: object) -> str:
    if cell is None:
        return ""
    return re.sub(r"\s+", "", str(cell))


def _find_header_map(row: list) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(row):
        text = _normalize_cell(cell)
        if not text:
            continue
        for field, keys in _HEADER_KEYS.items():
            if field not in mapping and all(key in text for key in keys):
                mapping[field] = idx
    return mapping


def _era_to_gregorian(era: str, year_text: str, month: str, day: str) -> dt.date | None:
    year_in_era = 1 if year_text == "元" else int(year_text)
    first_year = _ERA_FIRST_YEAR[era]
    gregorian_year = first_year + year_in_era - 1
    try:
        return dt.date(gregorian_year, int(month), int(day))
    except ValueError:
        return None


def parse_precip_start_date(raw_text: str | None, station_type: str | None) -> dt.date | None:
    """「観測開始年月日」欄のテキストから、降水量観測開始日（西暦）を求める。

    「雪」種別（積雪深計のみ）は降水量観測を行わないため常に ``None`` を返す。
    解析できない表記の場合も ``None`` を返す（呼び出し側は従来のヒントにフォール
    バックする）。
    """
    if station_type in NO_PRECIPITATION_TYPES:
        return None
    if not raw_text:
        return None
    text = raw_text.strip()

    if _HASH_ONLY_RE.match(text):
        return AMEDAS_LAUNCH_DATE

    m = _SIMPLE_DATE_RE.match(text)
    if m:
        hash_prefix, era, year_text, month, day = m.groups()
        if hash_prefix:
            return AMEDAS_LAUNCH_DATE
        return _era_to_gregorian(era, year_text, month, day)

    m = _PAREN_DATE_RE.match(text)
    if m:
        precip_era, precip_year, precip_month, precip_day = m.groups()[:4]
        return _era_to_gregorian(precip_era, precip_year, precip_month, precip_day)

    logger.warning("観測開始年月日の解析に失敗しました: %r", raw_text)
    return None


def parse_other_elements_start_date(raw_text: str | None) -> dt.date | None:
    """「観測開始年月日」欄のテキストから、気温等「他の要素」の観測開始日を求める。"""
    if not raw_text:
        return None
    text = raw_text.strip()

    m = _SIMPLE_DATE_RE.match(text)
    if m:
        _, era, year_text, month, day = m.groups()
        return _era_to_gregorian(era, year_text, month, day)

    m = _PAREN_DATE_RE.match(text)
    if m:
        other_era, other_year, other_month, other_day = m.groups()[4:]
        return _era_to_gregorian(other_era, other_year, other_month, other_day)

    return None


def parse_ame_master_pdf(pdf_bytes: bytes) -> pd.DataFrame:
    """観測所一覧PDFを解析し、地点ごとの観測開始日一覧をDataFrameで返す。

    ページごとに列構成（列数・列順）が異なるため、各表の先頭付近からヘッダー行を
    探して列位置を都度特定する（固定の列インデックスには依存しない）。
    """
    rows: list[dict] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables():
                    if not table:
                        continue
                    header_map = None
                    for row in table:
                        candidate = _find_header_map(row)
                        if _REQUIRED_HEADER_FIELDS <= set(candidate):
                            header_map = candidate
                            break
                    if header_map is None:
                        continue
                    for row in table:
                        rows.append(_row_to_record(row, header_map))
    except Exception as exc:  # pdfplumberの内部例外を含め、解析失敗として統一的に扱う
        raise AmeMasterPdfError(f"PDFの解析に失敗しました: {exc}") from exc

    records = [r for r in rows if r is not None]
    df = pd.DataFrame(records, columns=AME_MASTER_PDF_COLUMNS)
    if len(df) < 1000:
        logger.warning(
            "観測所一覧PDFから抽出できた地点数が想定より少ない可能性があります（%d件）。"
            "PDFのレイアウトが変更されていないか確認してください。",
            len(df),
        )
    return df


def _row_to_record(row: list, header_map: dict[str, int]) -> dict | None:
    number_idx = header_map["station_number"]
    if number_idx >= len(row):
        return None
    number = _normalize_cell(row[number_idx])
    if not _STATION_NUMBER_RE.match(number):
        return None

    def cell(field: str) -> str | None:
        idx = header_map.get(field)
        if idx is None or idx >= len(row):
            return None
        value = row[idx]
        return str(value).strip() if value is not None else None

    prefecture = cell("prefecture")
    station_type = cell("station_type")
    station_name = cell("station_name")
    start_text = cell("start_date")

    return {
        "prefecture": prefecture,
        "station_number": number,
        "station_type": station_type,
        "station_name": station_name,
        "raw_start_date_text": start_text,
        "precip_start_date": parse_precip_start_date(start_text, station_type),
        "other_elements_start_date": parse_other_elements_start_date(start_text),
    }


def save_ame_master_pdf_cache(df: pd.DataFrame, path: Path) -> None:
    atomic_write_parquet(df, path, index=False)


def load_ame_master_pdf_cache(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)


def ame_master_pdf_cache_exists(path: Path) -> bool:
    return path.exists()


def attach_precip_start_dates(
    station_master_df: pd.DataFrame, ame_master_df: pd.DataFrame
) -> pd.DataFrame:
    """地点マスタに観測所一覧PDF由来の降水量観測開始日を付加する。

    突き合わせは第一に ``(prefecture, station_name)`` の組で行う。気象庁の内部地点
    コード体系（``station_code``）と本PDFの5桁「観測所番号」は単純な文字列変換では
    対応しないため使用しない。降水量観測を行わない行（「雪」種別など、
    ``precip_start_date`` が欠損の行）は突き合わせ対象から除外する（同一敷地内の
    積雪深計に別の観測所番号が割り当てられているだけで、キーの衝突ではないため）。

    同一 ``(prefecture, station_name)`` の行が複数存在する場合（気象台の移転等で
    観測所番号が更新され、旧番号・新番号の行が両方掲載されているケースが大半）は、
    観測開始日が最も新しい行（＝現行の観測所番号に対応する可能性が高い行）を採用する。

    地点マスタの ``prefecture`` は気象庁obsdlサイトの管区表示（例:
    「網走・北見・紋別」のような複数地域を束ねた気象台管内名）に由来し、本PDFの
    振興局単位の表示（例:「オホーツク」）と一致しない場合がある。その場合に備え、
    第一段階で一致しなかった地点は ``station_name`` のみでの突き合わせを試みる。
    こちらは地点名の衝突（同名だが別地域の地点）のリスクがあるため、PDF全体で
    その地点名が一意に決まる場合に限って採用し、一意に決まらない場合は突き合わせ
    を行わず当該地点の値は欠損のままとする。
    """
    lookup = ame_master_df.dropna(subset=["prefecture", "station_name", "precip_start_date"])

    by_pref_name = lookup.groupby(["prefecture", "station_name"], as_index=False)[
        "precip_start_date"
    ].max()

    name_dup = lookup.duplicated(subset=["station_name"], keep=False)
    by_name_only = lookup[~name_dup][["station_name", "precip_start_date"]].rename(
        columns={"precip_start_date": "precip_start_date_by_name"}
    )

    station_master_df = station_master_df.drop(columns=["precip_start_date"], errors="ignore")
    merged = station_master_df.merge(by_pref_name, on=["prefecture", "station_name"], how="left")
    merged = merged.merge(by_name_only, on=["station_name"], how="left")
    merged["precip_start_date"] = merged["precip_start_date"].where(
        merged["precip_start_date"].notna(), merged["precip_start_date_by_name"]
    )
    merged = merged.drop(columns=["precip_start_date_by_name"])
    return merged
