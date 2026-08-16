"""時系列グラフ画面（12.3節）。"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from amedas_rainfall.config import AppConfig
from amedas_rainfall.indicators import annual_indicator_columns, indicator_label
from amedas_rainfall.pipeline import normalized_hourly_path
from amedas_rainfall.ui.common import (
    apply_plot_style_to_session,
    default_plot_style,
    ensure_indices_loaded,
    render_interactive_chart,
)
from amedas_rainfall.visualization.export import (
    build_export_filename,
    export_figure,
    load_plot_settings,
    save_plot_settings,
)
from amedas_rainfall.visualization.styles import PlotStyle
from amedas_rainfall.visualization.timeseries import build_timeseries_figure

PERIOD_PRESETS = ["最新31日", "今月", "前月", "全期間", "任意期間"]


def _resolve_period(preset: str, min_ts: pd.Timestamp, max_ts: pd.Timestamp) -> tuple[dt.date, dt.date]:
    today = max_ts.date()
    if preset == "最新31日":
        return today - dt.timedelta(days=31), today
    if preset == "今月":
        start = today.replace(day=1)
        return start, today
    if preset == "前月":
        first_this_month = today.replace(day=1)
        last_month_end = first_this_month - dt.timedelta(days=1)
        return last_month_end.replace(day=1), last_month_end
    if preset == "全期間":
        return min_ts.date(), today
    return today - dt.timedelta(days=31), today


def render_timeseries_page(config: AppConfig) -> None:
    st.header("時系列グラフ")

    station = st.session_state.get("selected_station")
    if not station:
        st.info("「地点選択・ダウンロード」タブで地点を選択してください。")
        return
    station_code = station["station_code"]
    station_name = station["station_name"]

    if not normalized_hourly_path(config, station_code).exists():
        st.warning("正規化済みデータがありません。先にダウンロードと正規化データの再構築を行ってください。")
        return

    indices_df = ensure_indices_loaded(config, station_code)
    indicator_options = [
        column
        for column in [
            *annual_indicator_columns(indices_df.columns),
            "soil_tank_1_mm",
            "soil_tank_2_mm",
            "soil_tank_3_mm",
        ]
        if column in indices_df.columns and column != "rainfall_raw_mm"
    ]

    settings_path = (
        config.resolved_path("paths.output_dir")
        / "plot_settings"
        / f"{station_code}_timeseries_settings.json"
    )
    if settings_path.exists() and st.button("保存したグラフ設定を読み込む", key="ts_load_settings_button"):
        style, extra = load_plot_settings(settings_path)
        st.session_state[f"ts_style_{station_code}"] = style
        apply_plot_style_to_session("ts", style)
        saved_indicators = [
            value for value in extra.get("indicators", []) if value in indicator_options
        ]
        if saved_indicators:
            st.session_state["ts_selected_indicators"] = saved_indicators
        if extra.get("preset") in PERIOD_PRESETS:
            st.session_state["ts_period_preset"] = extra["preset"]
        st.rerun()

    if st.button("指標を再計算する", key="ts_recompute_button"):
        ensure_indices_loaded(config, station_code, force_recompute=True)
        st.rerun()

    st.subheader("表示期間")
    preset = st.radio("期間プリセット", PERIOD_PRESETS, horizontal=True, key="ts_period_preset")
    min_ts = indices_df.index.min()
    max_ts = indices_df.index.max()
    data_min_date = min_ts.date()
    data_max_date = max_ts.date()
    default_start, default_end = _resolve_period(preset, min_ts, max_ts)

    # st.date_inputは、2回目以降のスクリプト実行ではvalue引数よりsession_state[key]を
    # 優先するため、プリセット切り替え時に明示的にsession_stateを上書きしないと
    # 表示・グラフに反映されない。プリセットが変わった回のみ上書きする。
    if st.session_state.get("ts_period_preset_prev") != preset:
        st.session_state["ts_start_date"] = default_start
        st.session_state["ts_end_date"] = default_end
        st.session_state["ts_period_preset_prev"] = preset

    # min_value/max_valueを明示しないと、st.date_inputは初回表示時の値から前後10年を
    # 暗黙のうちに選択可能範囲としてしまう。実データの範囲（数十年分）より狭いと、
    # 任意期間でそれより古い日付を入力してもエラー表示のまま無反応になり、
    # 「フリーズしたように見える」別の不具合の原因になっていた。
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input(
            "開始日時",
            disabled=(preset != "任意期間"),
            min_value=data_min_date,
            max_value=data_max_date,
            key="ts_start_date",
        )
    with c2:
        end_date = st.date_input(
            "終了日時",
            disabled=(preset != "任意期間"),
            min_value=data_min_date,
            max_value=data_max_date,
            key="ts_end_date",
        )

    if preset == "任意期間" and start_date > end_date:
        st.error("開始日時が終了日時より後になっています。日付を見直してください。")
        return

    mask = (indices_df.index.date >= start_date) & (indices_df.index.date <= end_date)
    view = indices_df.loc[mask]

    st.subheader("表示項目")
    bar_column = "rainfall_raw_mm"
    default_indicators = ["soil_rainfall_mm"] if "soil_rainfall_mm" in indicator_options else indicator_options[:1]
    selected_indicators = st.multiselect(
        "下段（折れ線グラフ、複数選択可）",
        indicator_options,
        default=default_indicators,
        format_func=lambda c: indicator_label(c, with_unit=True),
        key="ts_selected_indicators",
    )

    with st.expander("グラフ調整"):
        style_key = f"ts_style_{station_code}"
        if style_key not in st.session_state:
            st.session_state[style_key] = default_plot_style(config, title=f"{station_name} 時系列")
        style: PlotStyle = st.session_state[style_key]

        cc1, cc2, cc3 = st.columns(3)
        with cc1:
            style.size_unit = st.radio(
                "単位", ["px", "mm"], index=0 if style.size_unit == "px" else 1, key="ts_size_unit"
            )
            style.width = st.number_input("図幅", value=float(style.width), key="ts_fig_width")
            style.height = st.number_input("図高", value=float(style.height), key="ts_fig_height")
        with cc2:
            dpi_choices = list(config.get("figure_export.default_dpi_choices", [300, 600, 1200]))
            if style.dpi not in dpi_choices:
                dpi_choices.append(style.dpi)
            style.dpi = st.selectbox(
                "DPI(PNG用)", dpi_choices,
                index=dpi_choices.index(style.dpi),
                key="ts_fig_dpi",
            )
            style.font_size = st.number_input("基本フォントサイズ", value=style.font_size, key="ts_font_size")
            style.line_width = st.number_input("線幅", value=float(style.line_width), key="ts_line_width")
        with cc3:
            style.grayscale = st.checkbox("白黒モード", value=style.grayscale, key="ts_grayscale")
            style.show_grid = st.checkbox("グリッド表示", value=style.show_grid, key="ts_show_grid")
            style.show_missing_markers = st.checkbox(
                "欠測箇所を表示", value=style.show_missing_markers, key="ts_show_missing_markers"
            )

        style.title = st.text_input("タイトル", value=style.title, key="ts_title")
        style.subtitle = st.text_input("サブタイトル", value=style.subtitle, key="ts_subtitle")
        style.note = st.text_area("注記", value=style.note, key="ts_note")

    missing_mask = view["rainfall_raw_mm"].isna()
    if "is_missing" in view.columns:
        missing_mask = missing_mask | view["is_missing"].fillna(False).astype(bool)
    fig = build_timeseries_figure(view, bar_column, selected_indicators, style, missing_mask=missing_mask)
    render_interactive_chart(fig, key=f"ts_chart_{station_code}")

    st.subheader("画像出力")
    fmt = st.selectbox("形式", ["png", "svg", "pdf"], key="ts_fmt")
    detail = "_".join(selected_indicators) if selected_indicators else "指標未選択"
    if st.button("画像を生成してダウンロード用に保存", key="ts_export_button"):
        filename = build_export_filename(station_name, "時系列", detail, start_date, end_date, fmt)
        out_dir = config.resolved_path("paths.output_dir") / "figures"
        out_path = out_dir / filename
        export_figure(fig, out_path, fmt, style.width_px(), style.height_px(), dpi=style.dpi)
        st.session_state[f"ts_image_export_{station_code}"] = out_path
        st.success(f"保存しました: {out_path}")
    image_path = st.session_state.get(f"ts_image_export_{station_code}")
    if image_path and image_path.exists():
        st.download_button(
            "画像をダウンロード", image_path.read_bytes(), file_name=image_path.name,
            key=f"ts_dl_{station_code}",
            on_click="ignore",
        )

    if st.button("グラフ設定を保存(JSON)", key="ts_save_settings_button"):
        save_plot_settings(
            style,
            {"bar_column": bar_column, "indicators": selected_indicators, "preset": preset},
            settings_path,
        )
        st.success(f"保存しました: {settings_path}")
