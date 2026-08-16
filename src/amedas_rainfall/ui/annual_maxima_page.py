"""年最大値時系列グラフ画面。

Excel「r_max_c(manual ver.).xlsm」のrp_inシートに埋め込まれた棒グラフ
（各指標の年最大値を年ごとに並べたもの）と同等の図を、指標を選んで
表示・画像出力できるようにする。
"""

from __future__ import annotations

import datetime as dt

import streamlit as st

from amedas_rainfall.config import AppConfig
from amedas_rainfall.indicators import annual_indicator_columns, indicator_label
from amedas_rainfall.pipeline import build_annual_analysis, normalized_hourly_path, year_boundaries
from amedas_rainfall.ui.common import (
    apply_plot_style_to_session,
    default_plot_style,
    ensure_indices_loaded,
    render_interactive_chart,
)
from amedas_rainfall.visualization.annual_maxima import build_annual_maxima_figure
from amedas_rainfall.visualization.export import (
    build_export_filename,
    export_figure,
    load_plot_settings,
    save_plot_settings,
)
from amedas_rainfall.visualization.styles import PlotStyle


def render_annual_maxima_page(config: AppConfig) -> None:
    st.header("年最大値時系列グラフ")
    st.caption(
        "各指標について、年ごとの最大値を棒グラフで表示します"
        "（Excel版「rp_in」シートのグラフに相当します）。"
    )

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
    indicator_options = annual_indicator_columns(indices_df.columns)
    boundaries = year_boundaries(config)
    if not indicator_options or not boundaries:
        st.warning("表示できる年最大値指標または年区切り設定がありません。")
        return

    settings_path = (
        config.resolved_path("paths.output_dir")
        / "plot_settings"
        / f"{station_code}_annual_maxima_settings.json"
    )
    if settings_path.exists() and st.button("保存したグラフ設定を読み込む", key="am_load_settings_button"):
        style, extra = load_plot_settings(settings_path)
        st.session_state[f"am_style_{station_code}"] = style
        apply_plot_style_to_session("am", style)
        saved_indicator = extra.get("indicator")
        saved_boundary = extra.get("boundary_key")
        if saved_indicator in indicator_options:
            st.session_state["am_indicator"] = saved_indicator
        if saved_boundary in boundaries:
            st.session_state["am_boundary_key"] = saved_boundary
        st.rerun()

    c1, c2 = st.columns(2)
    with c1:
        indicator = st.selectbox(
            "指標", indicator_options, index=0, format_func=lambda c: indicator_label(c, annual=True),
            key="am_indicator",
        )
    with c2:
        boundary_key = st.selectbox(
            "年区切り", list(boundaries), format_func=lambda k: boundaries[k].label,
            key="am_boundary_key",
        )

    maxima_df = build_annual_analysis(indices_df, indicator, boundary_key, config=config)
    if maxima_df is None or maxima_df.empty:
        st.warning("年最大値を計算できませんでした。")
        return


    show_excluded = st.checkbox(
        "除外対象の年もグラフに表示する", value=False, key="am_show_excluded"
    )
    plot_df = maxima_df if show_excluded else maxima_df[maxima_df["is_eligible_default"]]
    if plot_df.empty:
        st.warning("採用条件を満たす年がありません。除外対象の表示を有効にして内容を確認してください。")
        plot_df = maxima_df

    with st.expander("グラフ調整"):
        style_key = f"am_style_{station_code}"
        automatic_title = (
            f"{station_name} 年最大値（{indicator_label(indicator, annual=True)}・"
            f"{boundaries[boundary_key].label}）"
        )
        if style_key not in st.session_state:
            st.session_state[style_key] = default_plot_style(
                config,
                title=automatic_title,
            )
        style: PlotStyle = st.session_state[style_key]
        previous_automatic_title = st.session_state.get(f"am_automatic_title_{station_code}")
        if previous_automatic_title is None or style.title == previous_automatic_title:
            style.title = automatic_title
            st.session_state["am_title"] = automatic_title
        st.session_state[f"am_automatic_title_{station_code}"] = automatic_title

        cc1, cc2, cc3 = st.columns(3)
        with cc1:
            style.size_unit = st.radio(
                "単位", ["px", "mm"], index=0 if style.size_unit == "px" else 1, key="am_size_unit"
            )
            style.width = st.number_input("図幅", value=float(style.width), key="am_fig_width")
            style.height = st.number_input("図高", value=float(style.height), key="am_fig_height")
        with cc2:
            dpi_choices = list(config.get("figure_export.default_dpi_choices", [300, 600, 1200]))
            if style.dpi not in dpi_choices:
                dpi_choices.append(style.dpi)
            style.dpi = st.selectbox(
                "DPI(PNG用)", dpi_choices,
                index=dpi_choices.index(style.dpi),
                key="am_fig_dpi",
            )
            style.font_size = st.number_input("基本フォントサイズ", value=style.font_size, key="am_font_size")
        with cc3:
            style.grayscale = st.checkbox("白黒モード", value=style.grayscale, key="am_grayscale")
            style.show_grid = st.checkbox("グリッド表示", value=style.show_grid, key="am_show_grid")

        style.title = st.text_input("タイトル", value=style.title, key="am_title")
        style.subtitle = st.text_input("サブタイトル", value=style.subtitle, key="am_subtitle")
        style.note = st.text_area("注記", value=style.note, key="am_note")

    fig = build_annual_maxima_figure(
        plot_df, style, y_axis_label=indicator_label(indicator, with_unit=True, annual=True)
    )
    render_interactive_chart(fig, key=f"am_chart_{station_code}")

    st.subheader("年最大値一覧")
    st.dataframe(
        maxima_df[
            [
                "year_label",
                "max_value",
                "max_datetime",
                "completeness_percent",
                "is_eligible_default",
                "exclusion_reasons",
            ]
        ],
        width="stretch",
        height=300,
    )

    st.subheader("画像出力")
    fmt = st.selectbox("形式", ["png", "svg", "pdf"], key="am_fmt")
    if st.button("画像を生成してダウンロード用に保存", key="am_export_button"):
        today = dt.date.today()
        filename = build_export_filename(
            station_name,
            "年最大値",
            f"{indicator_label(indicator, annual=True)}_{boundaries[boundary_key].label}",
            today,
            today,
            fmt,
        )
        out_dir = config.resolved_path("paths.output_dir") / "figures"
        out_path = out_dir / filename
        export_figure(fig, out_path, fmt, style.width_px(), style.height_px(), dpi=style.dpi)
        st.session_state[f"am_image_export_{station_code}"] = out_path
        st.success(f"保存しました: {out_path}")
    image_path = st.session_state.get(f"am_image_export_{station_code}")
    if image_path and image_path.exists():
        st.download_button(
            "画像をダウンロード", image_path.read_bytes(), file_name=image_path.name,
            key=f"am_dl_{station_code}",
            on_click="ignore",
        )

    if st.button("グラフ設定を保存(JSON)", key="am_save_settings_button"):
        save_plot_settings(
            style,
            {"indicator": indicator, "boundary_key": boundary_key},
            settings_path,
        )
        st.success(f"保存しました: {settings_path}")
