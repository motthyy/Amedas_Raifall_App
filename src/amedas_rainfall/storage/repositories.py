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
                """SELECT id, status, saved_file, file_sha256 FROM download_jobs
                   WHERE station_code=? AND start_date=? AND end_date=?""",
                (station_code, start_date.isoformat(), end_date.isoformat()),
            )
            row = cur.fetchone()
            if row:
                restored_status = (
                    JobStatus.VALIDATED.value
                    if row["saved_file"] and Path(row["saved_file"]).exists() and row["file_sha256"]
                    else JobStatus.PENDING.value
                )
                conn.execute(
                    """UPDATE download_jobs SET status=?, error_message=NULL, next_attempt_at=NULL
                       WHERE id=? AND status=?""",
                    (restored_status, row["id"], JobStatus.CANCELLED.value),
                )
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
        allowed = {
            "status", "attempt_count", "saved_file", "row_count", "file_size_bytes",
            "file_sha256", "min_datetime", "max_datetime", "error_message",
            "last_attempt_at", "next_attempt_at", "parent_job_id",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"更新できないジョブ列です: {sorted(unknown)}")
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
        """現在実行可能なPENDING/RETRY_WAITジョブを取得する。"""
        now = dt.datetime.now(tz=dt.timezone(dt.timedelta(hours=9))).isoformat()
        with get_connection(self.db_path) as conn:
            cur = conn.execute(
                """SELECT * FROM download_jobs WHERE station_code=?
                   AND status IN (?, ?)
                   AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY start_date""",
                (station_code, JobStatus.PENDING.value, JobStatus.RETRY_WAIT.value, now),
            )
            return [self._row_to_job(r) for r in cur.fetchall()]

    def claim_next_actionable_job(self, station_code: str) -> DownloadJob | None:
        """次のジョブをトランザクション内で1ワーカーだけが確保する。"""
        now = dt.datetime.now(tz=dt.timezone(dt.timedelta(hours=9)))
        with get_connection(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT * FROM download_jobs WHERE station_code=?
                   AND status IN (?, ?)
                   AND (next_attempt_at IS NULL OR next_attempt_at<=?)
                   ORDER BY start_date LIMIT 1""",
                (
                    station_code,
                    JobStatus.PENDING.value,
                    JobStatus.RETRY_WAIT.value,
                    now.isoformat(),
                ),
            ).fetchone()
            if row is None:
                return None
            updated = conn.execute(
                """UPDATE download_jobs SET status=?, last_attempt_at=?, next_attempt_at=NULL
                   WHERE id=? AND status IN (?, ?)""",
                (
                    JobStatus.DOWNLOADING.value,
                    now.isoformat(),
                    row["id"],
                    JobStatus.PENDING.value,
                    JobStatus.RETRY_WAIT.value,
                ),
            )
            if updated.rowcount != 1:
                return None
            claimed = conn.execute("SELECT * FROM download_jobs WHERE id=?", (row["id"],)).fetchone()
            return self._row_to_job(claimed)

    def get_waiting_retry_jobs(self, station_code: str) -> list[DownloadJob]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """SELECT * FROM download_jobs WHERE station_code=? AND status=?
                   ORDER BY next_attempt_at, start_date""",
                (station_code, JobStatus.RETRY_WAIT.value),
            ).fetchall()
            return [self._row_to_job(row) for row in rows]

    def cancel_jobs_outside_range(
        self, station_code: str, start_date: dt.date, end_date: dt.date
    ) -> int:
        with get_connection(self.db_path) as conn:
            result = conn.execute(
                """UPDATE download_jobs SET status=?, error_message='現在の計画範囲外'
                   WHERE station_code=? AND status!=?
                   AND (end_date<? OR start_date>?)""",
                (
                    JobStatus.CANCELLED.value,
                    station_code,
                    JobStatus.DOWNLOADING.value,
                    start_date.isoformat(),
                    end_date.isoformat(),
                ),
            )
            return result.rowcount

    def reserve_request_slot(self, throttle_key: str, min_interval_seconds: float) -> float:
        """複数プロセス間で共有する次回リクエスト枠を予約し、待ち秒数を返す。"""
        now = dt.datetime.now(tz=dt.timezone.utc)
        with get_connection(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT reserved_until FROM request_throttle WHERE throttle_key=?", (throttle_key,)
            ).fetchone()
            previous = dt.datetime.fromisoformat(row["reserved_until"]) if row else now
            slot = max(now, previous)
            reserved_until = slot + dt.timedelta(seconds=max(0.0, min_interval_seconds))
            conn.execute(
                """INSERT INTO request_throttle(throttle_key, reserved_until) VALUES(?, ?)
                   ON CONFLICT(throttle_key) DO UPDATE SET reserved_until=excluded.reserved_until""",
                (throttle_key, reserved_until.isoformat()),
            )
            return max(0.0, (slot - now).total_seconds())

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
            file_sha256=row["file_sha256"],
            min_datetime=dt.datetime.fromisoformat(row["min_datetime"]) if row["min_datetime"] else None,
            max_datetime=dt.datetime.fromisoformat(row["max_datetime"]) if row["max_datetime"] else None,
            error_message=row["error_message"],
            last_attempt_at=dt.datetime.fromisoformat(row["last_attempt_at"]) if row["last_attempt_at"] else None,
            next_attempt_at=dt.datetime.fromisoformat(row["next_attempt_at"]) if row["next_attempt_at"] else None,
        )
