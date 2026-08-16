"""ダウンロードジョブの再試行(異常終了からの復旧を含む)を検証する。"""

from __future__ import annotations

import datetime as dt
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from amedas_rainfall.jma.download_manager import (
    DownloadManager,
    DownloadManagerConfig,
    split_span_further,
)
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


class _FixtureClient:
    def __init__(self, content: bytes):
        self.content = content

    def download_hourly_precipitation_csv(self, *args, **kwargs) -> bytes:
        return self.content


def test_download_is_parsed_hashed_and_validated_before_success(tmp_path):
    content = (Path(__file__).parent / "fixtures" / "sample_normal_cp932.csv").read_bytes()
    repo = JobRepository(tmp_path / "jobs.sqlite3")
    manager = DownloadManager(
        _FixtureClient(content),
        repo,
        tmp_path / "raw",
        DownloadManagerConfig(normal_wait_seconds=0, min_wait_seconds=0),
    )
    manager.plan_jobs("a0001", dt.date(2024, 1, 1), dt.date(2024, 1, 1))

    manager.run("a0001", "地点名", max_jobs=1)

    job = repo.get_jobs_for_station("a0001")[0]
    assert job.status == JobStatus.VALIDATED
    assert job.row_count == 24
    assert job.file_sha256 == hashlib.sha256(content).hexdigest()
    assert Path(job.saved_file).parent == tmp_path / "raw" / "a0001"
    assert Path(job.saved_file).read_bytes() == content

    Path(job.saved_file).write_bytes(b"corrupt")
    assert manager.reconcile_validated_files("a0001") == 1
    assert repo.get_jobs_for_station("a0001")[0].status == JobStatus.PENDING


def test_invalid_html_response_is_rejected_before_saving():
    with pytest.raises(ValueError):
        DownloadManager._validate_download(
            b"<html>temporarily unavailable</html>",
            dt.date(2024, 1, 1),
            dt.date(2024, 1, 1),
        )


def test_truncated_csv_is_rejected_even_when_rows_are_inside_requested_range():
    content = (Path(__file__).parent / "fixtures" / "sample_overlap_cp932.csv").read_bytes()
    with pytest.raises(ValueError, match="完全には覆っていません"):
        DownloadManager._validate_download(
            content,
            dt.date(2024, 1, 1),
            dt.date(2024, 1, 2),
        )


def test_job_claim_is_atomic_across_workers(tmp_path):
    repo = JobRepository(tmp_path / "jobs.sqlite3")
    repo.create_job_if_absent("a0001", dt.date(2024, 1, 1), dt.date(2024, 1, 1))

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(executor.map(lambda _: repo.claim_next_actionable_job("a0001"), range(2)))

    assert sum(job is not None for job in claimed) == 1


def test_month_splits_follow_calendar_month_boundaries():
    spans = split_span_further(
        dt.date(2024, 1, 1), dt.date(2024, 12, 31), "3month"
    )

    assert spans == [
        (dt.date(2024, 1, 1), dt.date(2024, 3, 31)),
        (dt.date(2024, 4, 1), dt.date(2024, 6, 30)),
        (dt.date(2024, 7, 1), dt.date(2024, 9, 30)),
        (dt.date(2024, 10, 1), dt.date(2024, 12, 31)),
    ]
