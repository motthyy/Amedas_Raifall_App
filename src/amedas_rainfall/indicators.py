"""雨量指標の列名・表示名を一元管理する。"""

from __future__ import annotations

import re
from collections.abc import Iterable


def format_hours(hours: float | int) -> str:
    value = float(hours)
    return str(int(value)) if value.is_integer() else f"{value:g}"


def continuous_column(dry_hours_reset: int | float) -> str:
    return f"continuous_rainfall_{format_hours(dry_hours_reset)}h_mm"


def rolling_column(window_hours: int | float) -> str:
    return f"rolling_rainfall_{format_hours(window_hours)}h_mm"


def effective_column(half_life_hours: int | float) -> str:
    return f"effective_rainfall_{format_hours(half_life_hours)}h_mm"


DEFAULT_CONTINUOUS_COLUMN = continuous_column(12)
DEFAULT_ROLLING_COLUMN = rolling_column(24)
DEFAULT_EFFECTIVE_COLUMNS = [effective_column(v) for v in (3, 6, 24)]

STATIC_INDICATOR_LABELS = {
    "rainfall_raw_mm": "時雨量",
    "soil_rainfall_mm": "土壌雨量",
    "soil_tank_1_mm": "第1タンク貯留量",
    "soil_tank_2_mm": "第2タンク貯留量",
    "soil_tank_3_mm": "第3タンク貯留量",
}

_CONTINUOUS_RE = re.compile(r"^continuous_rainfall_(.+)h_mm$")
_ROLLING_RE = re.compile(r"^rolling_rainfall_(.+)h_mm$")
_EFFECTIVE_RE = re.compile(r"^effective_rainfall_(.+)h_mm$")


def indicator_label(column: str, *, with_unit: bool = False, annual: bool = False) -> str:
    suffix = " [mm]" if with_unit else ""
    if column == "rainfall_raw_mm":
        label = "時雨量（年最大時間雨量）" if annual else "時雨量"
        return label + (" [mm/h]" if with_unit and not annual else suffix)
    if column in STATIC_INDICATOR_LABELS:
        return STATIC_INDICATOR_LABELS[column] + suffix
    for pattern, template in (
        (_CONTINUOUS_RE, "{hours}時間無降雨リセット連続雨量"),
        (_ROLLING_RE, "{hours}時間移動雨量"),
        (_EFFECTIVE_RE, "実効雨量（半減期{hours}時間）"),
    ):
        match = pattern.match(column)
        if match:
            return template.format(hours=match.group(1)) + suffix
    return column + suffix


def is_annual_indicator(column: str) -> bool:
    return (
        column == "rainfall_raw_mm"
        or column == "soil_rainfall_mm"
        or _CONTINUOUS_RE.match(column) is not None
        or _ROLLING_RE.match(column) is not None
        or _EFFECTIVE_RE.match(column) is not None
    )


def annual_indicator_columns(columns: Iterable[str]) -> list[str]:
    return [column for column in columns if is_annual_indicator(column)]


def indicator_requires_state_continuity(column: str) -> bool:
    """欠測で内部状態が失われ、年最大値の信頼性へ影響する指標か。"""
    return (
        column == "soil_rainfall_mm"
        or _CONTINUOUS_RE.match(column) is not None
        or _EFFECTIVE_RE.match(column) is not None
    )
