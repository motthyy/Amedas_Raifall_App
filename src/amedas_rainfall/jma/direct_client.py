"""気象庁「過去の気象データ・ダウンロード」サイトへの直接アクセスクライアント。

現行サイト（2026年7月時点、docs/jma_download.md に詳細を記載）の実際の
リクエスト方式を再現する。サイト仕様が変更された場合は、このモジュールと
docs/jma_download.md を更新すること。

主要エンドポイント（すべて ``https://www.data.jma.go.jp/risk/obsdl/`` 配下。
HTMLページ自体は ``gmd/risk/obsdl/index.php`` にあるが、AJAX/CSV系は
``gmd`` を含まないパス）:
    - GET  index.php                : セッションCookie(ci_session)を確立
    - POST top/station  (pd=00)     : 都道府県一覧HTML
    - POST top/station  (pd=<prid>) : 地点一覧HTML（地図座標・地点コード等）
    - POST show/table                : プレビュー/CSVダウンロード（downloadFlagで切替）
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from amedas_rainfall.jma.ca_bundle import ensure_ca_bundle_path

logger = logging.getLogger(__name__)

INDEX_URL = "https://www.data.jma.go.jp/gmd/risk/obsdl/index.php"
ROOT_URL = "https://www.data.jma.go.jp/risk/obsdl/"
STATION_URL = ROOT_URL + "top/station"
SHOW_TABLE_URL = ROOT_URL + "show/table"

HOURLY_PRECIPITATION_ELEMENT_CODE = "101"
HOURLY_AGGREGATION_PERIOD = "9"

DEFAULT_USER_AGENT = (
    "amedas-rainfall-research-tool/0.1 (contact: local-research-use; "
    "respects JMA terms of use; low-frequency automated access)"
)


class JmaDirectClientError(RuntimeError):
    """直接アクセスクライアントの実行時エラー。"""


@dataclass
class RawStationEntry:
    """地点一覧HTMLから抽出した1地点分の生データ。"""

    stid: str
    stname: str
    prid: str
    kansoku: str
    name_from_title: str | None
    kana: str | None
    lat_deg: float | None
    lat_min: float | None
    lon_deg: float | None
    lon_min: float | None
    elevation_m: float | None
    discontinued_text: str | None

    @property
    def latitude(self) -> float | None:
        if self.lat_deg is None or self.lat_min is None:
            return None
        return round(self.lat_deg + self.lat_min / 60.0, 6)

    @property
    def longitude(self) -> float | None:
        if self.lon_deg is None or self.lon_min is None:
            return None
        return round(self.lon_deg + self.lon_min / 60.0, 6)

    @property
    def is_amedas(self) -> bool:
        return self.stid.startswith("a")

    @property
    def is_observatory(self) -> bool:
        return self.stid.startswith("s")

    @property
    def observes_precipitation(self) -> bool:
        return len(self.kansoku) > 0 and self.kansoku[0] in ("1", "2")

    @property
    def is_discontinued(self) -> bool:
        return self.discontinued_text is not None


_TITLE_NAME_RE = re.compile(r"地点名[：:]\s*(.+?)(?=\s+(?:カナ|北緯|東経|標高)[：:]|$)")
_TITLE_KANA_RE = re.compile(r"カナ[：:]\s*(.+?)(?=\s+(?:北緯|東経|標高)[：:]|$)")
_TITLE_LAT_RE = re.compile(r"北緯[：:]\s*([0-9.]+)度\s*([0-9.]+)分")
_TITLE_LON_RE = re.compile(r"東経[：:]\s*([0-9.]+)度\s*([0-9.]+)分")
_TITLE_ELEV_RE = re.compile(r"標高[：:]\s*([0-9.\-]+)m")
_TITLE_DISCONTINUED_RE = re.compile(r"(\d{4}年\d{1,2}月\d{1,2}日).*観測終了")


def parse_station_title_text(title: str) -> dict:
    name_m = _TITLE_NAME_RE.search(title)
    kana_m = _TITLE_KANA_RE.search(title)
    lat_m = _TITLE_LAT_RE.search(title)
    lon_m = _TITLE_LON_RE.search(title)
    elev_m = _TITLE_ELEV_RE.search(title)
    disc_m = _TITLE_DISCONTINUED_RE.search(title)
    return {
        "name_from_title": name_m.group(1).strip() if name_m else None,
        "kana": kana_m.group(1).strip() if kana_m else None,
        "lat_deg": float(lat_m.group(1)) if lat_m else None,
        "lat_min": float(lat_m.group(2)) if lat_m else None,
        "lon_deg": float(lon_m.group(1)) if lon_m else None,
        "lon_min": float(lon_m.group(2)) if lon_m else None,
        "elevation_m": float(elev_m.group(1)) if elev_m else None,
        "discontinued_text": disc_m.group(1) if disc_m else None,
    }


class JmaDirectClient:
    """気象庁obsdlサイトへの直接HTTPクライアント。"""

    def __init__(self, user_agent: str = DEFAULT_USER_AGENT, timeout_seconds: float = 30.0):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Referer": INDEX_URL,
            }
        )
        # 社内SSLインスペクションプロキシ配下ではcertifi同梱のCA一覧だけでは
        # 検証に失敗するため、Windows証明書ストアも信頼元に加える。
        # プロキシが存在しない環境ではNoneが返り、requests標準の検証(certifi)
        # にフォールバックする。
        ca_bundle_path = ensure_ca_bundle_path()
        if ca_bundle_path is not None:
            self.session.verify = ca_bundle_path
        self.timeout_seconds = timeout_seconds
        self._session_ready = False

    def ensure_session(self) -> None:
        """トップページへアクセスし、セッションCookie(ci_session)を確立する。"""
        if self._session_ready:
            return
        resp = self.session.get(INDEX_URL, timeout=self.timeout_seconds)
        resp.raise_for_status()
        if "ci_session" not in self.session.cookies.get_dict():
            logger.warning("ci_sessionクッキーが取得できませんでした。サイト仕様が変更された可能性があります。")
        self._session_ready = True

    def fetch_prefecture_codes(self) -> list[tuple[str, str]]:
        """都道府県コード一覧 [(prid, 表示名候補)] を取得する。"""
        self.ensure_session()
        resp = self.session.post(STATION_URL, data={"pd": "00"}, timeout=self.timeout_seconds)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        results: list[tuple[str, str]] = []
        seen = set()
        for div in soup.find_all("div", class_="prefecture"):
            hidden = div.find("input", attrs={"name": "prid"})
            if hidden is None:
                continue
            prid = hidden.get("value", "").strip()
            label = div.get_text(strip=True)
            if prid and prid not in seen:
                seen.add(prid)
                results.append((prid, label))
        if not results:
            raise JmaDirectClientError(
                "都道府県一覧を取得できませんでした。サイト仕様が変更された可能性があります。"
            )
        return results

    def fetch_stations_for_prefecture(self, prid: str) -> list[RawStationEntry]:
        """指定した都道府県コードに属する地点一覧を取得する。"""
        self.ensure_session()
        resp = self.session.post(STATION_URL, data={"pd": prid}, timeout=self.timeout_seconds)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        entries: dict[str, RawStationEntry] = {}
        for div in soup.find_all("div", class_="station"):
            stid_input = div.find("input", attrs={"name": "stid"})
            if stid_input is None:
                continue
            stid = stid_input.get("value", "").strip()
            if not stid or stid in entries:
                continue
            stname_input = div.find("input", attrs={"name": "stname"})
            prid_input = div.find("input", attrs={"name": "prid"})
            kansoku_input = div.find("input", attrs={"name": "kansoku"})
            title = div.get("title", "") or ""
            parsed_title = parse_station_title_text(title)

            entries[stid] = RawStationEntry(
                stid=stid,
                stname=stname_input.get("value", "").strip() if stname_input else "",
                prid=prid_input.get("value", "").strip() if prid_input else prid,
                kansoku=kansoku_input.get("value", "").strip() if kansoku_input else "",
                **parsed_title,
            )
        if not entries:
            raise JmaDirectClientError(
                f"都道府県コード{prid}の地点一覧を解析できませんでした。サイト仕様を確認してください。"
            )
        return list(entries.values())

    def download_hourly_precipitation_csv(
        self,
        stid: str,
        start_year: int,
        start_month: int,
        start_day: int,
        end_year: int,
        end_month: int,
        end_day: int,
    ) -> bytes:
        """指定地点・期間の時別降水量CSVを取得する（生のCP932バイト列を返す）。

        ``ymdLiteral=0`` を指定し、年・月・日・時が分割された列でCSVを取得する。
        これにより24時表記（"24"）がそのまま得られ、アプリ側の正規化処理
        （processing.normalization / jma.csv_parser）で明示的に翌日0時へ変換する。
        """
        self.ensure_session()
        payload = {
            "stationNumList": f'["{stid}"]',
            "aggrgPeriod": HOURLY_AGGREGATION_PERIOD,
            "elementNumList": f'[["{HOURLY_PRECIPITATION_ELEMENT_CODE}",""]]',
            "interAnnualType": "1",
            "ymdList": (
                f'["{start_year}","{end_year}","{start_month}","{end_month}",'
                f'"{start_day}","{end_day}"]'
            ),
            "optionNumList": "[]",
            "downloadFlag": "true",
            "rmkFlag": "1",
            "disconnectFlag": "1",
            "csvFlag": "1",
            "ymdLiteral": "0",
            "youbiFlag": "0",
            "fukenFlag": "0",
            "kijiFlag": "0",
            "jikantaiFlag": "0",
            "jikantaiList": "[]",
        }
        resp = self.session.post(SHOW_TABLE_URL, data=payload, timeout=self.timeout_seconds)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        if "html" in content_type.lower():
            raise JmaDirectClientError("CSVの代わりにHTML応答が返されました。")
        if "octet-stream" not in content_type and "text" not in content_type:
            raise JmaDirectClientError(f"想定外のContent-Typeです: {content_type}")
        if len(resp.content) == 0:
            raise JmaDirectClientError("空のレスポンスが返されました。")
        prefix = resp.content.lstrip()[:32].lower()
        if prefix.startswith((b"<!doctype html", b"<html")):
            raise JmaDirectClientError("CSVの代わりにHTML応答が返されました。")
        return resp.content
