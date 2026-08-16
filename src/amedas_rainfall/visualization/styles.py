"""グラフの見た目に関する設定（13節: グラフ調整機能）。"""

from __future__ import annotations

import platform
from dataclasses import asdict, dataclass, field
from pathlib import Path

BASE_CSS_DPI = 96.0

PREFERRED_JAPANESE_FONTS = ["Yu Gothic", "Meiryo", "Noto Sans CJK JP", "sans-serif"]

_WINDOWS_FONT_FILE_HINTS = {
    "Yu Gothic": ["YuGothM.ttc", "YuGothB.ttc", "yugothic.ttf"],
    "Meiryo": ["meiryo.ttc"],
    "Noto Sans CJK JP": ["NotoSansCJKjp-Regular.otf", "NotoSansJP-Regular.otf", "NotoSansJP-Regular.ttf"],
}


def detect_japanese_font(preferred: list[str] | None = None) -> str:
    """利用可能な日本語フォントを自動検出する。見つからない場合はsans-serifへフォールバックする。"""
    preferred = preferred or PREFERRED_JAPANESE_FONTS
    if platform.system() == "Windows":
        fonts_dir = Path(r"C:\Windows\Fonts")
        if fonts_dir.exists():
            for name in preferred:
                hints = _WINDOWS_FONT_FILE_HINTS.get(name, [])
                for hint in hints:
                    if (fonts_dir / hint).exists():
                        return name
    return "sans-serif"


# グレースケールでも判別できるよう、色に加えて線種・マーカーも変化させる系列パレット
SERIES_STYLE_CYCLE: list[dict] = [
    {"color": "#1f77b4", "dash": "solid", "symbol": "circle"},
    {"color": "#d62728", "dash": "dash", "symbol": "square"},
    {"color": "#2ca02c", "dash": "dot", "symbol": "diamond"},
    {"color": "#9467bd", "dash": "dashdot", "symbol": "triangle-up"},
    {"color": "#ff7f0e", "dash": "longdash", "symbol": "x"},
    {"color": "#8c564b", "dash": "longdashdot", "symbol": "cross"},
]

GRAYSCALE_STYLE_CYCLE: list[dict] = [
    {"color": "#111111", "dash": "solid", "symbol": "circle"},
    {"color": "#444444", "dash": "dash", "symbol": "square"},
    {"color": "#777777", "dash": "dot", "symbol": "diamond"},
    {"color": "#999999", "dash": "dashdot", "symbol": "triangle-up"},
    {"color": "#555555", "dash": "longdash", "symbol": "x"},
    {"color": "#222222", "dash": "longdashdot", "symbol": "cross"},
]


@dataclass
class PlotStyle:
    """ダッシュボード上で調整可能なグラフスタイル一式。"""

    width: float = 900.0
    height: float = 500.0
    size_unit: str = "px"  # "px" | "mm"
    dpi: int = 300

    font_family: str = field(default_factory=detect_japanese_font)
    font_size: int = 13
    axis_label_size: int = 14
    tick_size: int = 12
    legend_size: int = 12
    font_color: str = "#111111"

    line_width: float = 2.0
    bar_width: float = 0.8
    marker_size: float = 6.0

    grayscale: bool = False
    background_color: str = "#ffffff"
    show_grid: bool = True
    show_minor_grid: bool = False
    show_frame: bool = True
    show_crosshair: bool = True
    legend_position: str = "top"  # top | bottom | right

    title: str = ""
    subtitle: str = ""
    note: str = ""

    x_range: tuple[float, float] | None = None
    y_range: tuple[float, float] | None = None
    date_format: str = "%Y-%m-%d"

    margin_top: int = 60
    margin_bottom: int = 60
    margin_left: int = 70
    margin_right: int = 30

    horizontal_lines: list[dict] = field(default_factory=list)  # [{"y":..,"label":..,"color":..}]
    vertical_lines: list[dict] = field(default_factory=list)  # [{"x":..,"label":..,"color":..}]
    show_missing_markers: bool = True

    def style_cycle(self) -> list[dict]:
        return GRAYSCALE_STYLE_CYCLE if self.grayscale else SERIES_STYLE_CYCLE

    def width_px(self) -> float:
        if self.size_unit == "mm":
            # Plotlyへ渡す寸法はCSSピクセル(96dpi)。PNGの実DPI倍率は
            # export_figure側で一度だけ適用する。
            return self.width / 25.4 * BASE_CSS_DPI
        return self.width

    def height_px(self) -> float:
        if self.size_unit == "mm":
            return self.height / 25.4 * BASE_CSS_DPI
        return self.height

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PlotStyle":
        valid_keys = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        if "x_range" in filtered and filtered["x_range"] is not None:
            filtered["x_range"] = tuple(filtered["x_range"])
        if "y_range" in filtered and filtered["y_range"] is not None:
            filtered["y_range"] = tuple(filtered["y_range"])
        return cls(**filtered)
