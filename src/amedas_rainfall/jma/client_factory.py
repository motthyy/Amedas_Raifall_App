"""設定に基づく気象庁クライアント選択とフォールバック。"""

from __future__ import annotations

import logging
import time
from typing import Any

from amedas_rainfall.config import AppConfig
from amedas_rainfall.jma.direct_client import JmaDirectClient
from amedas_rainfall.jma.playwright_client import JmaPlaywrightClient
from amedas_rainfall.storage.repositories import JobRepository

logger = logging.getLogger(__name__)


class JmaClientRouter:
    def __init__(self, config: AppConfig, *, global_throttle: bool = True):
        self.mode = config.get("download.mode", "direct")
        fallback = config.get("download.fallback", "playwright")
        self.fallback_mode = None if fallback in (None, "none", self.mode) else fallback
        timeout_seconds = float(config.get("download.request_timeout_seconds", 30))
        user_agent = config.get("download.user_agent")
        self._direct = JmaDirectClient(user_agent=user_agent, timeout_seconds=timeout_seconds)
        self._playwright = JmaPlaywrightClient(timeout_ms=timeout_seconds * 1000)
        self._playwright_open = False
        self._throttle_interval = max(
            float(config.get("download.normal_wait_seconds", 3.0)),
            float(config.get("download.min_wait_seconds", 2.0)),
        )
        self._throttle_repo = (
            JobRepository(config.resolved_path("paths.jobs_db")) if global_throttle else None
        )

    def __enter__(self) -> "JmaClientRouter":
        if self.mode == "playwright":
            self._ensure_playwright()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        if self._playwright_open:
            self._playwright.__exit__(None, None, None)
            self._playwright_open = False
        self._direct.session.close()

    def _ensure_playwright(self) -> JmaPlaywrightClient:
        if not self._playwright_open:
            self._playwright.__enter__()
            self._playwright_open = True
        return self._playwright

    def _client(self, mode: str):
        return self._direct if mode == "direct" else self._ensure_playwright()

    def _wait_for_request_slot(self) -> None:
        if self._throttle_repo is not None:
            wait_seconds = self._throttle_repo.reserve_request_slot(
                "jma_obsdl", self._throttle_interval
            )
            if wait_seconds > 0:
                time.sleep(wait_seconds)

    def _call(self, method: str, *args, **kwargs) -> Any:
        self._wait_for_request_slot()
        try:
            return getattr(self._client(self.mode), method)(*args, **kwargs)
        except Exception:
            if self.fallback_mode is None:
                raise
            logger.exception(
                "気象庁クライアント%sが失敗したため%sへ切り替えます。",
                self.mode,
                self.fallback_mode,
            )
            self._wait_for_request_slot()
            return getattr(self._client(self.fallback_mode), method)(*args, **kwargs)

    def fetch_prefecture_codes(self):
        return self._call("fetch_prefecture_codes")

    def fetch_stations_for_prefecture(self, prid: str):
        return self._call("fetch_stations_for_prefecture", prid)

    def download_hourly_precipitation_csv(self, *args, **kwargs):
        return self._call("download_hourly_precipitation_csv", *args, **kwargs)


def create_jma_client(config: AppConfig, *, global_throttle: bool = True) -> JmaClientRouter:
    return JmaClientRouter(config, global_throttle=global_throttle)
