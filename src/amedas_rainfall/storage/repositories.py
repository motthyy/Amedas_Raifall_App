"""ダウンロードジョブのリポジトリ（SQLite CRUD）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from amedas_rainfall.models import DownloadJob, JobStatus
from amedas_rainfall.storage.database import get_connection


class JobRepository:
    """download_jobs テーブルへのアクセスを提供する。"""

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def create_job_if_absent(self, station_code: str, start_date: dt.date, end_date: dt.date) -> int:
        with get_connection(self.db_path) as conn:
            cur = conn.execute(
                "SELECT id FROM download_jobs WHERE station_code=? AND start_date=? AND end_date=?",
                (station_code, start_date.isoformat(), end_date.isoformat()),
            )
            row = cur.fetchone()
            if row:
                return row["id"]
            cur = conn.execute(
                """INSERT INTO download_jobs (station_code, start_date, end_date, status)
                   VALUES (?, ?, ?, ?)""",
                (station_code, start_date.isoformat(), end_date.isoformat(), JobStatus.PENDING.value),
            )
            return cur.lastrowid

    def add_split_children(
        self, station_code: str, parent_id: int, spans: list[tuple[dt.date, dt.date]]
    ) -> list[int]:
        ids = []
        with get_connection(self.db_path) as conn:
            conn.execute(
                "UPDATE download_jobs SET status=? WHERE id=?", (JobStatus.SPLIT.value, parent_id)
            )
            for start, end in spans:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO download_jobs
                       (station_code, start_date, end_date, status, parent_job_id)
                       VALUES (?, ?, ?, ?, ?)""",
                    (station_code, start.isoformat(), end.isoformat(), JobStatus.PENDING.value, parent_id),
                )
                if cur.lastrowid:
                    ids.append(cur.lastrowid)
        return ids

    def update_job(self, job_id: int, **fields) -> None:
        if not fields:
            return
        columns = ", ".join(f"{k}=?" for k in fields)
        values = list(fields.values()) + [job_id]
        with get_connection(self.db_path) as conn:
            conn.execute(f"UPDATE download_jobs SET {columns} WHERE id=?", values)

    def get_jobs_for_station(self, station_code: str) -> list[DownloadJob]:
        with get_connection(self.db_path) as conn:
            cur = conn.execute(
                "SELECT * FROM download_jobs WHERE station_code=? ORDER BY start_date", (station_code,)
            )
            return [self._row_to_job(r) for r in cur.fetchall()]

    def get_actionable_jobs(self, station_code: str) -> list[DownloadJob]:
        """PENDING または RETRY_WAIT 状態のジョブを取得する（再開対象）。"""
        with get_connection(self.db_path) as conn:
            cur = conn.execute(
                """SELECT * FROM download_jobs WHERE station_code=?
                   AND status IN (?, ?) ORDER BY start_date""",
                (station_code, JobStatus.PENDING.value, JobStatus.RETRY_WAIT.value),
            )
            return [self._row_to_job(r) for r in cur.fetchall()]

    def get_failed_jobs(self, station_code: str) -> list[DownloadJob]:
        with get_connection(self.db_path) as conn:
            cur = conn.execute(
                "SELECT * FROM download_jobs WHERE station_code=? AND status=? ORDER BY start_date",
                (station_code, JobStatus.FAILED.value),
            )
            return [self._row_to_job(r) for r in cur.fetchall()]

    def get_stuck_downloading_jobs(self, station_code: str) -> list[DownloadJob]:
        """DOWNLOADING状態のまま止まっているジョブを取得する。

        アプリの異常終了やプロセスの強制終了により、DOWNLOADING状態の更新が
        SUCCESS/FAILEDへ進まないまま残ったジョブは`get_actionable_jobs`の
        対象外(PENDING/RETRY_WAITではない)のため、自動再開されない。
        """
        with get_connection(self.db_path) as conn:
            cur = conn.execute(
                "SELECT * FROM download_jobs WHERE station_code=? AND status=? ORDER BY start_date",
                (station_code, JobStatus.DOWNLOADING.value),
            )
            return [self._row_to_job(r) for r in cur.fetchall()]

    def get_successful_jobs(self, station_code: str) -> list[DownloadJob]:
        with get_connection(self.db_path) as conn:
            cur = conn.execute(
                """SELECT * FROM download_jobs WHERE station_code=?
                   AND status IN (?, ?) ORDER BY start_date""",
                (station_code, JobStatus.SUCCESS.value, JobStatus.VALIDATED.value),
            )
            return [self._row_to_job(r) for r in cur.fetchall()]

    @staticmethod
    def _row_to_job(row) -> DownloadJob:
        return DownloadJob(
            job_id=row["id"],
            station_code=row["station_code"],
            start_date=dt.date.fromisoformat(row["start_date"]),
            end_date=dt.date.fromisoformat(row["end_date"]),
            status=JobStatus(row["status"]),
            attempt_count=row["attempt_count"],
            saved_file=row["saved_file"],
            row_count=row["row_count"],
            file_size_bytes=row["file_size_bytes"],
            min_datetime=dt.datetime.fromisoformat(row["min_datetime"]) if row["min_datetime"] else None,
            max_datetime=dt.datetime.fromisoformat(row["max_datetime"]) if row["max_datetime"] else None,
            error_message=row["error_message"],
            last_attempt_at=dt.datetime.fromisoformat(row["last_attempt_at"]) if row["last_attempt_at"] else None,
        )
