"""ダウンロードジョブの再試行(異常終了からの復旧を含む)を検証する。"""

from __future__ import annotations

import datetime as dt

from amedas_rainfall.jma.download_manager import DownloadManager
from amedas_rainfall.models import JobStatus
from amedas_rainfall.storage.repositories import JobRepository


class _StubClient:
    def download_hourly_precipitation_csv(self, *args, **kwargs) -> bytes:  # pragma: no cover
        raise AssertionError("この検証では呼び出されない想定")


def _make_manager(tmp_path) -> tuple[DownloadManager, JobRepository]:
    job_repo = JobRepository(tmp_path / "jobs.sqlite3")
    manager = DownloadManager(_StubClient(), job_repo, tmp_path / "raw")
    return manager, job_repo


def test_retry_failed_resets_failed_jobs(tmp_path):
    manager, job_repo = _make_manager(tmp_path)
    job_id = job_repo.create_job_if_absent("47662", dt.date(2020, 1, 1), dt.date(2020, 12, 31))
    job_repo.update_job(job_id, status=JobStatus.FAILED.value, error_message="boom")

    n = manager.retry_failed("47662")

    assert n == 1
    job = job_repo.get_jobs_for_station("47662")[0]
    assert job.status == JobStatus.PENDING
    assert job.error_message is None


def test_retry_failed_recovers_stuck_downloading_jobs(tmp_path):
    """アプリの強制終了等でDOWNLOADINGのまま残ったジョブも再試行対象に戻せること。"""
    manager, job_repo = _make_manager(tmp_path)
    job_id = job_repo.create_job_if_absent("47662", dt.date(2020, 1, 1), dt.date(2020, 12, 31))
    job_repo.update_job(job_id, status=JobStatus.DOWNLOADING.value)

    # DOWNLOADING状態は get_actionable_jobs の対象外のため、自動再開されない。
    assert job_repo.get_actionable_jobs("47662") == []
    assert job_repo.get_stuck_downloading_jobs("47662")[0].job_id == job_id

    n = manager.retry_failed("47662")

    assert n == 1
    job = job_repo.get_jobs_for_station("47662")[0]
    assert job.status == JobStatus.PENDING
    assert job_repo.get_actionable_jobs("47662")[0].job_id == job_id
