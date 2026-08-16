"""設定ファイル(YAML)の読み込みとアクセスを提供するモジュール。"""

from __future__ import annotations

import functools
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.yaml"
TANK_MODEL_CONFIG_PATH = PROJECT_ROOT / "config" / "tank_model.yaml"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


@dataclass
class AppConfig:
    """アプリ全体の設定を保持するコンテナ。"""

    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None, overrides: dict[str, Any] | None = None) -> "AppConfig":
        config_path = path or DEFAULT_CONFIG_PATH
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if overrides:
            raw = _deep_merge(raw, overrides)
        return cls(raw=raw)

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """"a.b.c" 形式のキーで設定値を取得する。"""
        node: Any = self.raw
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def resolved_path(self, dotted_key: str) -> Path:
        """設定内の相対パスをプロジェクトルート基準の絶対パスへ変換する。"""
        value = self.get(dotted_key)
        if value is None:
            raise KeyError(f"設定キーが見つかりません: {dotted_key}")
        p = Path(value)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    def validate(self) -> None:
        """利用者が編集できる設定値を起動時に検証する。"""
        errors: list[str] = []

        def positive_number(key: str) -> None:
            value = self.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                errors.append(f"{key} は0より大きい数値で指定してください。")

        positive_number("download.normal_wait_seconds")
        positive_number("download.min_wait_seconds")
        positive_number("download.request_timeout_seconds")
        positive_number("rainfall.dry_hours_reset")
        positive_number("rainfall.rolling_window_hours")
        positive_number("download.backoff_multiplier")
        positive_number("figure_export.default_width_mm")
        positive_number("figure_export.default_height_mm")
        positive_number("figure_export.default_dpi")
        backoff = self.get("download.backoff_multiplier")
        if isinstance(backoff, (int, float)) and not isinstance(backoff, bool) and backoff < 1:
            errors.append("download.backoff_multiplier は1以上にしてください。")
        normal_wait = self.get("download.normal_wait_seconds")
        min_wait = self.get("download.min_wait_seconds")
        if (
            isinstance(normal_wait, (int, float))
            and not isinstance(normal_wait, bool)
            and isinstance(min_wait, (int, float))
            and not isinstance(min_wait, bool)
            and normal_wait < min_wait
        ):
            errors.append("download.normal_wait_seconds は min_wait_seconds 以上にしてください。")

        retries = self.get("download.retry_wait_seconds")
        if not isinstance(retries, list) or not retries or any(
            not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0
            for value in retries
        ):
            errors.append("download.retry_wait_seconds は0以上の数値の空でないリストにしてください。")
        max_retries = self.get("download.max_retries_per_span")
        if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
            errors.append("download.max_retries_per_span は0以上の整数にしてください。")
        allowed_splits = {"1year", "6month", "3month", "1month", "7day"}
        split_sequence = self.get("download.split_sequence")
        if (
            not isinstance(split_sequence, list)
            or not split_sequence
            or any(value not in allowed_splits for value in split_sequence)
        ):
            errors.append("download.split_sequence に未対応の期間単位があります。")
        elif len(set(split_sequence)) != len(split_sequence) or split_sequence != sorted(
            split_sequence,
            key={"1year": 366, "6month": 183, "3month": 91, "1month": 31, "7day": 7}.get,
            reverse=True,
        ):
            errors.append("download.split_sequence は大きい期間から順に重複なく指定してください。")

        half_lives = self.get("rainfall.effective_half_lives_hours")
        if not isinstance(half_lives, list) or not half_lives:
            errors.append("rainfall.effective_half_lives_hours は空でないリストにしてください。")
        elif any(not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0 for v in half_lives):
            errors.append("rainfall.effective_half_lives_hours は0より大きい数値だけにしてください。")

        mode = self.get("download.mode")
        fallback = self.get("download.fallback")
        if mode not in ("direct", "playwright"):
            errors.append("download.mode は direct または playwright にしてください。")
        if fallback not in (None, "none", "direct", "playwright"):
            errors.append("download.fallback は none/direct/playwright のいずれかにしてください。")
        if self.get("rainfall.ten_minute_disaggregation") != "equal_split":
            errors.append("rainfall.ten_minute_disaggregation は equal_split のみ対応しています。")

        threshold = self.get("annual_maxima.completeness_threshold_percent")
        if not isinstance(threshold, (int, float)) or not 0 <= threshold <= 100:
            errors.append("annual_maxima.completeness_threshold_percent は0〜100で指定してください。")

        for key, node in (self.get("year_boundaries") or {}).items():
            if not isinstance(node, dict):
                errors.append(f"year_boundaries.{key} はマッピングで指定してください。")
                continue
            month, day = node.get("start_month"), node.get("start_day")
            if not isinstance(month, int) or not 1 <= month <= 12:
                errors.append(f"year_boundaries.{key}.start_month が不正です。")
            if not isinstance(day, int) or not 1 <= day <= 31:
                errors.append(f"year_boundaries.{key}.start_day が不正です。")
            if isinstance(month, int) and isinstance(day, int):
                try:
                    dt.date(2000, month, day)
                except ValueError:
                    errors.append(f"year_boundaries.{key} は実在しない月日です。")

        method = self.get("gumbel.default_estimation_method")
        if method not in ("mle", "moments"):
            errors.append("gumbel.default_estimation_method は mle または moments にしてください。")
        plotting_position = self.get("gumbel.default_plotting_position")
        if plotting_position not in ("gringorten", "weibull", "cunnane"):
            errors.append(
                "gumbel.default_plotting_position は gringorten/weibull/cunnane にしてください。"
            )
        return_periods = self.get("gumbel.return_periods_years")
        if not isinstance(return_periods, list) or not return_periods or any(
            not isinstance(value, (int, float)) or isinstance(value, bool) or value < 1
            for value in return_periods
        ):
            errors.append("gumbel.return_periods_years は1以上の数値のリストにしてください。")

        dpi_choices = self.get("figure_export.default_dpi_choices")
        if not isinstance(dpi_choices, list) or not dpi_choices or any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in dpi_choices
        ):
            errors.append("figure_export.default_dpi_choices は正の整数のリストにしてください。")
        elif self.get("figure_export.default_dpi") not in dpi_choices:
            errors.append("figure_export.default_dpi は default_dpi_choices に含めてください。")

        bootstrap_iterations = self.get("gumbel.bootstrap.default_iterations")
        if (
            not isinstance(bootstrap_iterations, int)
            or isinstance(bootstrap_iterations, bool)
            or bootstrap_iterations < 1
        ):
            errors.append("gumbel.bootstrap.default_iterations は1以上の整数にしてください。")
        confidence = self.get("gumbel.bootstrap.default_confidence_level")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 < confidence < 1:
            errors.append("gumbel.bootstrap.default_confidence_level は0より大きく1未満にしてください。")

        backup_days = self.get("logging.backup_days")
        if not isinstance(backup_days, int) or isinstance(backup_days, bool) or backup_days < 1:
            errors.append("logging.backup_days は1以上の整数にしてください。")
        if str(self.get("logging.level", "INFO")).upper() not in {
            "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
        }:
            errors.append("logging.level が不正です。")

        if errors:
            raise ValueError("設定ファイルに不正な値があります:\n- " + "\n- ".join(errors))


@functools.lru_cache(maxsize=1)
def get_default_config() -> AppConfig:
    config = AppConfig.load()
    config.validate()
    return config


def load_tank_model_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or TANK_MODEL_CONFIG_PATH
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
