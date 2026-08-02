"""気象庁「地域気象観測所一覧」PDF解析のテスト。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from amedas_rainfall.jma.ame_master_pdf import (
    attach_precip_start_dates,
    parse_ame_master_pdf,
    parse_other_elements_start_date,
    parse_precip_start_date,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# --- 観測開始年月日欄の解析（PDF不要、文字列入力のみ） ---------------------------


def test_plain_date_is_precip_start_for_all_elements() -> None:
    assert parse_precip_start_date("昭52.10.19", "四") == dt.date(1977, 10, 19)
    assert parse_other_elements_start_date("昭52.10.19") == dt.date(1977, 10, 19)


def test_hash_prefixed_date_means_precip_started_at_amedas_launch() -> None:
    # 降水量は運用開始日(1974-11-01)から、表示日付は他の要素の開始日。
    assert parse_precip_start_date("#昭52.10.14", "四") == dt.date(1974, 11, 1)
    assert parse_other_elements_start_date("#昭52.10.14") == dt.date(1977, 10, 14)


def test_bare_hash_means_precip_only_started_at_amedas_launch() -> None:
    assert parse_precip_start_date("#", "雨") == dt.date(1974, 11, 1)
    assert parse_other_elements_start_date("#") is None


def test_parenthesized_date_gives_distinct_precip_and_other_start_dates() -> None:
    assert parse_precip_start_date("(昭50.5.29)昭52.10.24", "四") == dt.date(1975, 5, 29)
    assert parse_other_elements_start_date("(昭50.5.29)昭52.10.24") == dt.date(1977, 10, 24)


def test_snow_only_station_type_has_no_precipitation_observation() -> None:
    assert parse_precip_start_date("昭55.10.30", "雪") is None


def test_gannen_year_notation_is_first_year_of_era() -> None:
    assert parse_precip_start_date("平元.9.22", "官") == dt.date(1989, 9, 22)


def test_unparseable_text_returns_none() -> None:
    assert parse_precip_start_date("該当なし", "四") is None
    assert parse_precip_start_date(None, "四") is None


# --- attach_precip_start_dates()の結合ロジック（合成DataFrame） -----------------


def _ame_master_row(prefecture: str, station_name: str, precip_start_date: dt.date) -> dict:
    return {
        "prefecture": prefecture,
        "station_number": "00000",
        "station_type": "四",
        "station_name": station_name,
        "raw_start_date_text": "",
        "precip_start_date": precip_start_date,
        "other_elements_start_date": precip_start_date,
    }


def test_attach_matches_by_prefecture_and_name() -> None:
    station_master = pd.DataFrame(
        [{"prefecture": "上川", "station_name": "中川", "station_code": "a1201"}]
    )
    ame_master = pd.DataFrame([_ame_master_row("上川", "中川", dt.date(1977, 10, 19))])

    merged = attach_precip_start_dates(station_master, ame_master)

    assert merged.loc[0, "precip_start_date"] == dt.date(1977, 10, 19)


def test_attach_falls_back_to_name_only_when_prefecture_label_differs() -> None:
    # 地点マスタ側の管区表示(例:「網走・北見・紋別」)がPDFの振興局表示(例:「オホーツク」)
    # と一致しない場合でも、地点名がPDF全体で一意なら突き合わせる。
    station_master = pd.DataFrame(
        [{"prefecture": "網走・北見・紋別", "station_name": "常呂", "station_code": "a0068"}]
    )
    ame_master = pd.DataFrame([_ame_master_row("ｵﾎｰﾂｸ", "常呂", dt.date(1977, 10, 21))])

    merged = attach_precip_start_dates(station_master, ame_master)

    assert merged.loc[0, "precip_start_date"] == dt.date(1977, 10, 21)


def test_attach_leaves_blank_when_name_is_ambiguous_across_prefectures() -> None:
    # 同名地点が異なる都道府県に存在する場合、地点名のみでの突き合わせは行わない。
    station_master = pd.DataFrame(
        [{"prefecture": "愛知", "station_name": "新城", "station_code": "a1541"}]
    )
    ame_master = pd.DataFrame(
        [
            _ame_master_row("空知", "新城", dt.date(1984, 10, 5)),
            # 「愛知/新城」自体はPDF側に存在しない（都道府県ラベルも地点名一致もしないケース）。
            _ame_master_row("石狩", "新城", dt.date(2000, 1, 1)),
        ]
    )

    merged = attach_precip_start_dates(station_master, ame_master)

    assert pd.isna(merged.loc[0, "precip_start_date"])


def test_attach_takes_latest_date_when_same_prefecture_name_has_multiple_rows() -> None:
    # 気象台の移転等で同一(都道府県,地点名)に複数行(旧番号・新番号)が存在する場合は、
    # 開始日が最も新しい行(現行の観測所番号に対応する可能性が高い)を採用する。
    station_master = pd.DataFrame(
        [{"prefecture": "愛知", "station_name": "名古屋", "station_code": "s47636"}]
    )
    ame_master = pd.DataFrame(
        [
            _ame_master_row("愛知", "名古屋", dt.date(1974, 11, 1)),
            _ame_master_row("愛知", "名古屋", dt.date(1999, 1, 20)),
        ]
    )

    merged = attach_precip_start_dates(station_master, ame_master)

    assert merged.loc[0, "precip_start_date"] == dt.date(1999, 1, 20)


# --- 実PDFの一部を切り出した固定ファイルでのend-to-end抽出テスト -----------------


def _load_fixture_pdf() -> bytes:
    return (FIXTURES_DIR / "ame_master_sample.pdf").read_bytes()


def test_parses_real_pdf_pages_with_plain_and_hash_and_parenthesized_dates() -> None:
    df = parse_ame_master_pdf(_load_fixture_pdf())
    assert len(df) >= 80

    def row(station_number: str) -> pd.Series:
        matches = df[df["station_number"] == station_number]
        assert len(matches) == 1, f"station_number={station_number} not found uniquely"
        return matches.iloc[0]

    # 通常の単一日付表記（新篠津は「#」付き、山口は「#」付き四要素地点。まず単純な例を確認）。
    hamamasu = row("14026")
    assert hamamasu["station_name"] == "浜益"
    assert hamamasu["precip_start_date"] == dt.date(1977, 10, 12)

    # 「#」+日付: 降水量はアメダス運用開始日、他要素は表示日付。
    shinshinotsu = row("14101")
    assert shinshinotsu["precip_start_date"] == dt.date(1974, 11, 1)
    assert shinshinotsu["other_elements_start_date"] == dt.date(1978, 10, 23)

    # 「#」単独（雨量のみの地点）。
    kogane_yu = row("14191")
    assert kogane_yu["station_type"] == "雨"
    assert kogane_yu["precip_start_date"] == dt.date(1974, 11, 1)
    assert kogane_yu["other_elements_start_date"] is None

    # 括弧付き日付: 降水量開始日と他要素開始日が異なる。
    tsukigata = row("15311")
    assert tsukigata["precip_start_date"] == dt.date(1975, 5, 16)
    assert tsukigata["other_elements_start_date"] == dt.date(1977, 10, 6)

    # 「雪」種別: 降水量観測なし。
    kaidakogen = row("48940")
    assert kaidakogen["station_type"] == "雪"
    assert kaidakogen["precip_start_date"] is None

    # 元号「元年」表記。
    suttsu = row("16252")
    assert suttsu["precip_start_date"] == dt.date(1989, 9, 22)

    # 12列(気象官署等をまとめた縮小レイアウト)ページの列位置解決。
    nagoya = row("51900")
    assert nagoya["station_name"] == "名古屋"
    assert nagoya["precip_start_date"] == dt.date(1999, 1, 20)
