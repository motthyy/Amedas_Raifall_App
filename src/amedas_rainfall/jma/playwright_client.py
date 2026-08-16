"""Playwrightによるブラウザ操作を用いた予備ダウンロード手段（5.4節）。

direct_client（生HTTPリクエスト）が気象庁サイトの仕様変更等で機能しなくなった
場合の予備手段。実際にChromiumブラウザでサイトを開いてセッションを確立し、
ブラウザのコンテキストが保持するCookieを用いてリクエストを実行することで、
direct_clientよりも実ブラウザに近い挙動を再現する。

本モジュールは direct_client.JmaDirectClient と同様のメソッド群を提供し、
download_manager から透過的に差し替えられるようにする。
"""

from __future__ import annotations

import logging
import os

from amedas_rainfall.jma.ca_bundle import ensure_ca_bundle_path
from amedas_rainfall.jma.direct_client import (
    HOURLY_AGGREGATION_PERIOD,
    HOURLY_PRECIPITATION_ELEMENT_CODE,
    INDEX_URL,
    SHOW_TABLE_URL,
    STATION_URL,
    RawStationEntry,
    parse_station_title_text,
)

logger = logging.getLogger(__name__)


class JmaPlaywrightClientError(RuntimeError):
    """Playwright予備手段の実行時エラー。"""


class JmaPlaywrightClient:
    """Playwrightのブラウザコンテキストを用いてobsdlサイトへアクセスするクライアント。

    direct_clientと同じHTTPエンドポイントを使用するが、実Chromiumの
    BrowserContext.request（Playwright APIRequestContext）経由でリクエストする
    ことで、Cookie管理やTLSフィンガープリント等をブラウザに委ねる。
    """

    def __init__(self, headless: bool = True, timeout_ms: float = 30000):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise JmaPlaywrightClientError(
                "playwrightがインストールされていません。install.batを再実行するか、"
                "'pip install playwright' 及び 'playwright install chromium' を実行してください。"
            ) from exc
        self._sync_playwright_factory = sync_playwright
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    def __enter__(self) -> "JmaPlaywrightClient":
        # Playwrightの内部通信(APIRequestContext含む)はNode.js製ドライバが
        # 担うため、社内SSLインスペクションプロキシ配下ではNODE_EXTRA_CA_CERTS
        # でWindows証明書ストア由来のCAを渡す必要がある。ドライバ起動前に
        # 環境変数として設定しておく。
        ca_bundle_path = ensure_ca_bundle_path()
        if ca_bundle_path is not None:
            os.environ.setdefault("NODE_EXTRA_CA_CERTS", ca_bundle_path)
        try:
            self._pw = self._sync_playwright_factory().start()
            self._browser = self._pw.chromium.launch(headless=self.headless)
            self._context = self._browser.new_context()
            self._page = self._context.new_page()
            self._page.goto(INDEX_URL, timeout=self.timeout_ms)
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._context is not None:
            self._context.close()
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()
        self._page = None
        self._context = None
        self._browser = None
        self._pw = None

    def fetch_prefecture_codes(self) -> list[tuple[str, str]]:
        resp = self._context.request.post(STATION_URL, form={"pd": "00"})
        if not resp.ok:
            raise JmaPlaywrightClientError(f"都道府県一覧の取得に失敗しました: HTTP {resp.status}")
        html = resp.text()
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
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
            raise JmaPlaywrightClientError("都道府県一覧を取得できませんでした。")
        return results

    def fetch_stations_for_prefecture(self, prid: str) -> list[RawStationEntry]:
        resp = self._context.request.post(STATION_URL, form={"pd": prid})
        if not resp.ok:
            raise JmaPlaywrightClientError(f"地点一覧の取得に失敗しました: HTTP {resp.status}")
        html = resp.text()
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
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
            raise JmaPlaywrightClientError(
                f"都道府県コード{prid}の地点一覧を解析できませんでした。"
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
        resp = self._context.request.post(SHOW_TABLE_URL, form=payload)
        if not resp.ok:
            raise JmaPlaywrightClientError(f"CSV取得に失敗しました: HTTP {resp.status}")
        body = resp.body()
        if not body:
            raise JmaPlaywrightClientError("空のレスポンスが返されました。")
        prefix = body.lstrip()[:32].lower()
        if prefix.startswith((b"<!doctype html", b"<html")):
            raise JmaPlaywrightClientError("CSVの代わりにHTML応答が返されました。")
        return body
