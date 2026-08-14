"""ダウンロードジョブの計画・実行・分割・再試行を管理する（5.2〜5.5節）。"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from amedas_rainfall.models import JobStatus
from amedas_rainfall.storage.repositories import JobRepository

logger = logging.getLogger(__name__)

JST = dt.timezone(dt.timedelta(hours=9))


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
class DownloadManagerConfig:
    normal_wait_seconds: float = 3.0
    min_wait_seconds: float = 2.0
    retry_wait_seconds: tuple[float, ...] = (10.0, 30.0, 120.0)
    backoff_multiplier: float = 2.0
    max_retries_per_span: int = 5
    split_sequence: tuple[str, ...] = ("1year", "6month", "3month", "1month", "7day")

    def __post_init__(self) -> None:
        if self.normal_wait_seconds < self.min_wait_seconds:
            self.normal_wait_seconds = self.min_wait_seconds


def _add_months(d: dt.date, months: int) -> dt.date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                       31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return dt.date(year, month, day)


def split_into_year_spans(start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.date]]:
    """全期間を1年単位に分割する（5.2節）。"""
    spans = []
    cur = start
    while cur <= end:
        year_end = dt.date(cur.year, 12, 31)
        span_end = min(year_end, end)
        spans.append((cur, span_end))
        cur = span_end + dt.timedelta(days=1)
    return spans


_SPAN_DAYS = {"6month": 183, "3month": 91, "1month": 31, "7day": 7}


def split_span_further(start: dt.date, end: dt.date, split_key: str) -> list[tuple[dt.date, dt.date]]:
    """失敗した期間をより小さい単位へ分割する（5.2節: 6か月→3か月→1か月→7日）。"""
    if split_key == "6month":
        mid = _add_months(start, 6)
        first_end = min(mid - dt.timedelta(days=1), end)
    elif split_key == "3month":
        mid = _add_months(start, 3)
        first_end = min(mid - dt.timedelta(days=1), end)
    elif split_key == "1month":
        mid = _add_months(start, 1)
        first_end = min(mid - dt.timedelta(days=1), end)
    elif split_key == "7day":
        first_end = min(start + dt.timedelta(days=6), end)
    else:
        raise ValueError(f"未知の分割キー: {split_key}")

    spans = []
    cur = start
    step_days = _SPAN_DAYS[split_key]
    while cur <= end:
        step_end = min(cur + dt.timedelta(days=step_days - 1), end)
        spans.append((cur, step_end))
        cur = step_end + dt.timedelta(days=1)
    return spans


def next_split_key(current_span_days: int, split_sequence: tuple[str, ...]) -> str | None:
    thresholds = {"1year": 366, "6month": 183, "3month": 91, "1month": 31, "7day": 7}
    for key in split_sequence:
        if thresholds[key] < current_span_days:
            return key
    return None


class DownloadManager:
    """1地点分の全期間ダウンロードを、SQLiteジョブ管理のもとで実行する。"""

    def __init__(
        self,
        client: SupportsCsvDownload,
        job_repo: JobRepository,
        raw_dir: Path,
        config: DownloadManagerConfig | None = None,
    ):
        self.client = client
        self.job_repo = job_repo
        self.raw_dir = raw_dir
        self.config = config or DownloadManagerConfig()

    def plan_jobs(self, station_code: str, start: dt.date, end: dt.date) -> list[int]:
        """1年単位の初期ジョブを計画する（既存ジョブは再作成しない）。"""
        job_ids = []
        for span_start, span_end in split_into_year_spans(start, end):
            job_ids.append(self.job_repo.create_job_if_absent(station_code, span_start, span_end))
        return job_ids

    def run(
        self,
        station_code: str,
        station_name: str,
        progress_callback: Callable[[str], None] | None = None,
        stop_flag: Callable[[], bool] | None = None,
        max_jobs: int | None = None,
    ) -> None:
        """未完了ジョブを順に実行する。中断された場合、次回呼び出しで続きから再開できる。

        Args:
            max_jobs: 1回の呼び出しで処理するジョブ数の上限。Noneの場合は全件を処理する。
                Streamlit UIから呼び出す場合、件数が多いと1回のスクリプト実行が長時間ブロック
                され画面が無応答になるため、小さい値を指定して複数回に分けて呼び出すこと
                （ui/station_page.pyの自動継続ループを参照）。
        """
        station_dir = self.raw_dir / f"{station_code}_{station_name}"
        station_dir.mkdir(parents=True, exist_ok=True)

        processed = 0
        while max_jobs is None or processed < max_jobs:
            if stop_flag and stop_flag():
                logger.info("ユーザー操作により一時停止しました。")
                return
            jobs = self.job_repo.get_actionable_jobs(station_code)
            if not jobs:
                return
            job = jobs[0]
            self._execute_job(job, station_dir, progress_callback)
            processed += 1
            time.sleep(max(self.config.normal_wait_seconds, self.config.min_wait_seconds))
            if max_jobs is not None and processed >= max_jobs:
                return

    def _execute_job(self, job, station_dir: Path, progress_callback: Callable[[str], None] | None) -> None:
        assert job.job_id is not None
        span_days = (job.end_date - job.start_date).days + 1
        now = dt.datetime.now(tz=JST)

        self.job_repo.update_job(
            job.job_id, status=JobStatus.DOWNLOADING.value, last_attempt_at=now.isoformat()
        )
        if progress_callback:
            progress_callback(f"{job.station_code}: {job.start_date} 〜 {job.end_date} を取得中...")

        try:
            content = self.client.download_hourly_precipitation_csv(
                job.station_code,
                job.start_date.year,
                job.start_date.month,
                job.start_date.day,
                job.end_date.year,
                job.end_date.month,
                job.end_date.day,
            )
            filename = f"{job.station_code}_{job.start_date.isoformat()}_{job.end_date.isoformat()}.csv"
            filepath = station_dir / filename
            filepath.write_bytes(content)

            self.job_repo.update_job(
                job.job_id,
                status=JobStatus.SUCCESS.value,
                saved_file=str(filepath),
                file_size_bytes=len(content),
                attempt_count=job.attempt_count + 1,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ダウンロード失敗 (station=%s, %s〜%s): %s", job.station_code, job.start_date, job.end_date, exc)
            self._handle_failure(job, span_days, str(exc))

    def _handle_failure(self, job, span_days: int, error_message: str) -> None:
        assert job.job_id is not None
        split_key = next_split_key(span_days, self.config.split_sequence)
        if split_key is not None:
            spans = split_span_further(job.start_date, job.end_date, split_key)
            if len(spans) > 1 or spans[0] != (job.start_date, job.end_date):
                self.job_repo.add_split_children(job.station_code, job.job_id, spans)
                self.job_repo.update_job(job.job_id, error_message=error_message)
                return

        attempt_count = job.attempt_count + 1
        if attempt_count > self.config.max_retries_per_span:
            self.job_repo.update_job(
                job.job_id,
                status=JobStatus.FAILED.value,
                attempt_count=attempt_count,
                error_message=error_message,
            )
            return

        wait_index = min(attempt_count - 1, len(self.config.retry_wait_seconds) - 1)
        wait_seconds = self.config.retry_wait_seconds[wait_index] * (
            self.config.backoff_multiplier ** max(0, attempt_count - len(self.config.retry_wait_seconds))
        )
        self.job_repo.update_job(
            job.job_id,
            status=JobStatus.RETRY_WAIT.value,
            attempt_count=attempt_count,
            error_message=error_message,
        )
        time.sleep(wait_seconds)
        self.job_repo.update_job(job.job_id, status=JobStatus.PENDING.value)

    def retry_failed(self, station_code: str) -> int:
        """FAILED状態、および異常終了で中断されたDOWNLOADING状態のジョブをPENDINGへ戻す。

        DOWNLOADING状態はダウンロード処理の途中でのみ設定され、正常終了時は
        必ずSUCCESSかFAILEDへ遷移する。ジョブ一覧を取得した時点でDOWNLOADING
        状態のジョブが残っている場合、アプリのクラッシュや強制終了により中断
        されたものとみなし、再試行対象に含める。
        """
        failed = self.job_repo.get_failed_jobs(station_code)
        stuck = self.job_repo.get_stuck_downloading_jobs(station_code)
        for job in failed + stuck:
            self.job_repo.update_job(job.job_id, status=JobStatus.PENDING.value, error_message=None)
        return len(failed) + len(stuck)
