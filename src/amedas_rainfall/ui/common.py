"""複数画面で共通して使うヘルパー関数。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from amedas_rainfall.config import AppConfig
from amedas_rainfall.pipeline import (
    indices_cache_signature,
    is_indices_cache_valid,
    load_or_compute_all_indices,
    normalized_hourly_path,
)
from amedas_rainfall.visualization.styles import PlotStyle, detect_japanese_font


def default_plot_style(config: AppConfig, title: str = "") -> PlotStyle:
    """設定ファイルの画像寸法・DPI・フォントを反映した初期スタイルを作る。"""
    preferred_fonts = config.get("fonts.preferred_japanese_fonts", None)
    return PlotStyle(
        title=title,
        size_unit="mm",
        width=float(config.get("figure_export.default_width_mm", 160)),
        height=float(config.get("figure_export.default_height_mm", 100)),
        dpi=int(config.get("figure_export.default_dpi", 300)),
        font_family=detect_japanese_font(preferred_fonts),
    )


def ensure_indices_loaded(config: AppConfig, station_code: str, force_recompute: bool = False) -> pd.DataFrame:
    """指標データフレームをセッション内にロードする。

    キャッシュ（data/calculated/{地点コード}/indices.parquet）があれば瞬時に読み込み、
    なければ計算しながら進捗バーを表示する。計算結果はディスクとセッションの両方へ保存する。
    """
    cache_key = f"indices_df_{station_code}"
    signature_key = f"indices_signature_{station_code}"
    current_signature = indices_cache_signature(config, station_code)
    if (
        not force_recompute
        and cache_key in st.session_state
        and st.session_state.get(signature_key) == current_signature
    ):
        return st.session_state[cache_key]

    hourly_path = normalized_hourly_path(config, station_code)
    needs_compute = force_recompute or not hourly_path.exists() or not is_indices_cache_valid(
        config, station_code
    )

    if needs_compute:
        status = st.empty()
        progress = st.progress(0.0)
        percent = st.empty()
        status.info("指標を計算しています（初回のみ時間がかかります。次回以降はキャッシュを再利用します）...")

        def _progress(fraction: float, message: str) -> None:
            ratio = min(max(fraction, 0.0), 1.0)
            progress.progress(ratio)
            percent.text(f"{message}（{ratio * 100:.0f}%）")

        indices_df = load_or_compute_all_indices(
            config, station_code, force_recompute=force_recompute, progress_callback=_progress
        )
        status.empty()
        progress.empty()
        percent.empty()
    else:
        indices_df = load_or_compute_all_indices(config, station_code)

    st.session_state[cache_key] = indices_df
    st.session_state[signature_key] = indices_cache_signature(config, station_code)
    return indices_df


def apply_plot_style_to_session(prefix: str, style: PlotStyle) -> None:
    """JSONから読み込んだPlotStyleを既存ウィジェット状態へ反映する。"""
    mapping = {
        "size_unit": style.size_unit,
        "fig_width": float(style.width),
        "fig_height": float(style.height),
        "fig_dpi": style.dpi,
        "font_size": style.font_size,
        "line_width": float(style.line_width),
        "grayscale": style.grayscale,
        "show_grid": style.show_grid,
        "show_missing_markers": style.show_missing_markers,
        "title": style.title,
        "subtitle": style.subtitle,
        "note": style.note,
    }
    for suffix, value in mapping.items():
        st.session_state[f"{prefix}_{suffix}"] = value


def render_interactive_chart(fig, key: str) -> None:
    """グラフをインタラクティブ表示する（マウスホイールでズーム、点クリックで日時・値を表示）。

    画像出力（export_figure）には渡したfigをそのまま使うため、ここで行う表示上の
    工夫（scrollZoom, on_select）はエクスポート画像の見た目に一切影響しない。
    """
    event = st.plotly_chart(
        fig,
        width="stretch",
        theme=None,
        config={"scrollZoom": True},
        on_select="rerun",
        selection_mode=["points"],
        key=key,
    )
    points = event.selection.points if event and event.selection else []
    if points:
        lines = []
        for p in points:
            curve_number = p.get("curve_number")
            trace_name = fig.data[curve_number].name if curve_number is not None else None
            x = p.get("x")
            y = p.get("y")
            label = f"{trace_name}: " if trace_name else ""
            lines.append(f"{label}日時={x} / 値={y}")
        st.info("選択した点:\n" + "\n".join(lines))
    else:
        st.caption("ヒント: グラフ上の点をクリックすると日時と値が表示されます。マウスホイールでズームできます。")
