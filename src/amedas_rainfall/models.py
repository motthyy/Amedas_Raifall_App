"""アプリ全体で共有するデータモデル定義。"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum


class StationType(str, Enum):
    """地点種別。"""

    AMEDAS = "アメダス"
    OBSERVATORY = "気象官署"
    OTHER = "その他"


@dataclass
class Station:
    """観測地点マスタの1レコード。"""

    prefecture: str
    """都道府県名。"""
    station_name: str
    """地点名。"""
    station_code: str
    """気象庁の内部地点コード（block_no など、地点を一意に識別するコード）。"""
    request_prec_no: str
    """ダウンロード要求で使う「prec_no」相当のパラメータ値。"""
    request_block_no: str
    """ダウンロード要求で使う「block_no」相当のパラメータ値。"""
    station_type: StationType
    latitude: float | None
    longitude: float | None
    elevation_m: float | None
    is_currently_observing: bool
    is_discontinued: bool
    has_precipitation_observation: bool
    metadata_fetched_at: dt.datetime
    hourly_precip_start_hint: dt.date | None = None
    """地点メタデータ上に示された、時別降水量の開始日候補（未確定・要検証）。"""

    @property
    def display_label(self) -> str:
        suffix = "（廃止）" if self.is_discontinued else ""
        return f"{self.prefecture} / {self.station_name}{suffix}"


class JobStatus(str, Enum):
    """ダウンロードジョブの状態。"""

    PENDING = "PENDING"
    DOWNLOADING = "DOWNLOADING"
    SUCCESS = "SUCCESS"
    VALIDATED = "VALIDATED"
    RETRY_WAIT = "RETRY_WAIT"
    SPLIT = "SPLIT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class DownloadJob:
    """1期間分のダウンロードジョブ状態。"""

    station_code: str
    start_date: dt.date
    end_date: dt.date
    status: JobStatus = JobStatus.PENDING
    attempt_count: int = 0
    saved_file: str | None = None
    row_count: int | None = None
    file_size_bytes: int | None = None
    file_sha256: str | None = None
    min_datetime: dt.datetime | None = None
    max_datetime: dt.datetime | None = None
    error_message: str | None = None
    last_attempt_at: dt.datetime | None = None
    next_attempt_at: dt.datetime | None = None
    job_id: int | None = None


class QualityFlag(str, Enum):
    """気象庁CSVの品質情報相当の区分。"""

    NORMAL = "正常"
    QUASI_NORMAL = "準正常"
    MISSING = "欠測"
    UNKNOWN = "不明"


@dataclass
class HourlyRecord:
    """正規化後の時別データ1レコード（内部処理用の参考モデル）。"""

    datetime_jst: dt.datetime
    rainfall_raw_mm: float | None
    quality_flag: QualityFlag
    is_missing: bool
    source_file: str
    is_conflicting: bool = False


@dataclass
class YearBoundaryDefinition:
    """年区切り定義（暦年／年度／6月始まり年）。"""

    key: str
    label: str
    start_month: int
    start_day: int


@dataclass
class AnnualCompleteness:
    """年区分ごとのデータ完全性情報。"""

    year_label: str
    start_datetime: dt.datetime
    end_datetime: dt.datetime
    expected_hours: int
    valid_hours: int
    missing_hours: int
    completeness_percent: float
    has_state_reset: bool
    is_eligible_default: bool
    exclusion_reasons: list[str] = field(default_factory=list)
