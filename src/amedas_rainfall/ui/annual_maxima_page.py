"""年最大値時系列グラフ画面。

Excel「r_max_c(manual ver.).xlsm」のrp_inシートに埋め込まれた棒グラフ
（各指標の年最大値を年ごとに並べたもの）と同等の図を、指標を選んで
表示・画像出力できるようにする。
"""

from __future__ import annotations

import datetime as dt

import streamlit as st

from amedas_rainfall.config import AppConfig
from amedas_rainfall.pipeline import compute_annual_maxima_all_boundaries, normalized_hourly_path
from amedas_rainfall.ui.common import ensure_indices_loaded, render_interactive_chart
from amedas_rainfall.visualization.annual_maxima import build_annual_maxima_figure
from amedas_rainfall.visualization.export import build_export_filename, export_figure, save_plot_settings
from amedas_rainfall.visualization.styles import PlotStyle

INDICATOR_LABELS_JA = {
    "rainfall_raw_mm": "時雨量（年最大時間雨量）",
    "continuous_rainfall_12h_mm": "12時間無降雨リセット連続雨量",
    "rolling_rainfall_24h_mm": "24時間移動雨量",
    "effective_rainfall_3h_mm": "実効雨量(半減期3時間)",
    "effective_rainfall_6h_mm": "実効雨量(半減期6時間)",
    "effective_rainfall_24h_mm": "実効雨量(半減期24時間)",
    "soil_rainfall_mm": "土壌雨量",
}
BOUNDARY_LABELS = {"calendar": "暦年", "fiscal": "年度", "june_start": "6月始まり年"}


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

    c1, c2 = st.columns(2)
    with c1:
        indicator = st.selectbox(
            "指標", list(INDICATOR_LABELS_JA.keys()), index=0, format_func=lambda c: INDICATOR_LABELS_JA[c],
            key="am_indicator",
        )
    with c2:
        boundary_key = st.selectbox(
            "年区切り", list(BOUNDARY_LABELS.keys()), format_func=lambda k: BOUNDARY_LABELS[k],
            key="am_boundary_key",
        )

    maxima_all = compute_annual_maxima_all_boundaries(indices_df, columns=[indicator])
    maxima_df = maxima_all[boundary_key].get(indicator)
    if maxima_df is None or maxima_df.empty:
        st.warning("年最大値を計算できませんでした。")
        return

    with st.expander("グラフ調整"):
        style_key = f"am_style_{station_code}"
        if style_key not in st.session_state:
            st.session_state[style_key] = PlotStyle(
                title=f"{station_name} 年最大値（{INDICATOR_LABELS_JA[indicator]}・{BOUNDARY_LABELS[boundary_key]}）"
            )
        style: PlotStyle = st.session_state[style_key]

        cc1, cc2, cc3 = st.columns(3)
        with cc1:
            style.size_unit = st.radio(
                "単位", ["px", "mm"], index=0 if style.size_unit == "px" else 1, key="am_size_unit"
            )
            style.width = st.number_input("図幅", value=float(style.width), key="am_fig_width")
            style.height = st.number_input("図高", value=float(style.height), key="am_fig_height")
        with cc2:
            style.dpi = st.selectbox(
                "DPI(PNG用)", [300, 600, 1200],
                index=[300, 600, 1200].index(style.dpi) if style.dpi in (300, 600, 1200) else 0,
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
        maxima_df, style, y_axis_label=f"{INDICATOR_LABELS_JA[indicator]} [mm]"
    )
    render_interactive_chart(fig, key=f"am_chart_{station_code}")

    st.subheader("年最大値一覧")
    st.dataframe(
        maxima_df[["year_label", "max_value", "max_datetime"]], use_container_width=True, height=300
    )

    st.subheader("画像出力")
    fmt = st.selectbox("形式", ["png", "svg", "pdf"], key="am_fmt")
    if st.button("画像を生成してダウンロード用に保存", key="am_export_button"):
        today = dt.date.today()
        filename = build_export_filename(
            station_name,
            "年最大値",
            f"{INDICATOR_LABELS_JA[indicator]}_{BOUNDARY_LABELS[boundary_key]}",
            today,
            today,
            fmt,
        )
        out_dir = config.resolved_path("paths.output_dir") / "figures"
        out_path = out_dir / filename
        export_figure(fig, out_path, fmt, style.width_px(), style.height_px(), dpi=style.dpi)
        st.success(f"保存しました: {out_path}")
        with open(out_path, "rb") as f:
            st.download_button("ダウンロード", f.read(), file_name=filename, key="am_dl")

    if st.button("グラフ設定を保存(JSON)", key="am_save_settings_button"):
        settings_path = (
            config.resolved_path("paths.output_dir")
            / "plot_settings"
            / f"{station_code}_annual_maxima_settings.json"
        )
        save_plot_settings(
            style,
            {"indicator": indicator, "boundary_key": boundary_key},
            settings_path,
        )
        st.success(f"保存しました: {settings_path}")
