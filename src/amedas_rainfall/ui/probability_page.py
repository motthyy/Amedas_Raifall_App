"""確率雨量グラフ画面（12.4節）。"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import streamlit as st

from amedas_rainfall.config import AppConfig
from amedas_rainfall.pipeline import (
    compute_annual_maxima_all_boundaries,
    compute_completeness_all_boundaries,
    normalized_hourly_path,
)
from amedas_rainfall.statistics.bootstrap import sample_size_warnings
from amedas_rainfall.ui.common import ensure_indices_loaded, render_interactive_chart
from amedas_rainfall.statistics.gumbel import (
    STANDARD_RETURN_PERIODS,
    analyze_gumbel,
    return_period_from_value,
)
from amedas_rainfall.visualization.export import build_export_filename, export_figure
from amedas_rainfall.visualization.probability import build_probability_figure
from amedas_rainfall.visualization.styles import PlotStyle

INDICATOR_LABELS_JA = {
    "rainfall_raw_mm": "時雨量（年最大時間雨量、比較用）",
    "continuous_rainfall_12h_mm": "12時間無降雨リセット連続雨量",
    "rolling_rainfall_24h_mm": "24時間移動雨量",
    "effective_rainfall_3h_mm": "実効雨量(半減期3時間)",
    "effective_rainfall_6h_mm": "実効雨量(半減期6時間)",
    "effective_rainfall_24h_mm": "実効雨量(半減期24時間)",
    "soil_rainfall_mm": "土壌雨量",
}
BOUNDARY_LABELS = {"calendar": "暦年", "fiscal": "年度", "june_start": "6月始まり年"}


def render_probability_page(config: AppConfig) -> None:
    st.header("確率雨量グラフ（ガンベル分布）")

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
            key="prob_indicator",
        )
    with c2:
        boundary_key = st.selectbox(
            "年区切り", list(BOUNDARY_LABELS.keys()), format_func=lambda k: BOUNDARY_LABELS[k],
            key="prob_boundary_key",
        )

    threshold = st.slider(
        "採用可否の完全率閾値(%)", min_value=50.0, max_value=100.0, value=95.0, step=0.5,
        key="prob_completeness_threshold",
    )
    completeness = compute_completeness_all_boundaries(indices_df, completeness_threshold_percent=threshold)
    maxima_all = compute_annual_maxima_all_boundaries(indices_df, columns=[indicator])
    maxima_df = maxima_all[boundary_key].get(indicator)
    if maxima_df is None or maxima_df.empty:
        st.warning("年最大値を計算できませんでした。")
        return

    completeness_rows = {c.year_label: c for c in completeness[boundary_key]}
    maxima_df = maxima_df.copy()
    maxima_df["採用可否(既定)"] = maxima_df["year_label"].map(
        lambda lbl: completeness_rows[lbl].is_eligible_default if lbl in completeness_rows else False
    )
    maxima_df["除外理由"] = maxima_df["year_label"].map(
        lambda lbl: "、".join(completeness_rows[lbl].exclusion_reasons) if lbl in completeness_rows else ""
    )

    st.subheader("採用年の選択")
    edited = st.data_editor(
        maxima_df[["year_label", "max_value", "max_datetime", "採用可否(既定)", "除外理由"]],
        column_config={"採用可否(既定)": st.column_config.CheckboxColumn("採用する")},
        disabled=["year_label", "max_value", "max_datetime", "除外理由"],
        use_container_width=True,
        key=f"editor_{station_code}_{indicator}_{boundary_key}",
    )
    included = edited[edited["採用可否(既定)"]]
    excluded = edited[~edited["採用可否(既定)"]]
    annual_maxima_values = included["max_value"].dropna().to_numpy()

    if len(annual_maxima_values) < 2:
        st.error("ガンベル分布の推定には少なくとも2年分の採用年最大値が必要です。")
        return

    st.subheader("推定条件")
    c3, c4, c5 = st.columns(3)
    with c3:
        method = st.radio(
            "推定法", ["mle", "moments"], format_func=lambda m: "最尤法" if m == "mle" else "積率法",
            key="prob_method",
        )
    with c4:
        plotting_position = st.radio(
            "プロッティングポジション", ["gringorten", "weibull", "cunnane"], key="prob_plotting_position"
        )
    with c5:
        x_log = st.checkbox("横軸を対数表示", value=True, key="prob_x_log")

    for w in sample_size_warnings(len(annual_maxima_values), max(STANDARD_RETURN_PERIODS)):
        st.warning(w)

    gumbel_result = analyze_gumbel(annual_maxima_values, method=method, plotting_position=plotting_position)

    st.subheader("任意雨量の再現確率")
    input_value = st.number_input(
        "検討する雨量[mm]（0のときは非表示）", min_value=0.0, value=0.0, step=1.0,
        key="prob_input_value",
    )
    input_return_period = None
    if input_value > 0:
        input_return_period = return_period_from_value(
            gumbel_result.parameters.loc_mu, gumbel_result.parameters.scale_beta, input_value
        )
        if math.isfinite(input_return_period) and input_return_period > 1:
            st.success(
                f"{input_value:.1f} mm の再現確率年は 約{input_return_period:.1f}年"
                f"（年超過確率 約{100 / input_return_period:.2f}%）です。"
            )
        else:
            st.warning("この雨量は算出範囲外のため再現確率年を計算できませんでした。")

    with st.expander("グラフ調整"):
        style = PlotStyle(title=f"{station_name} 確率雨量（{INDICATOR_LABELS_JA[indicator]}・{BOUNDARY_LABELS[boundary_key]}）")
        style.width = st.number_input("図幅(px)", value=float(style.width), key="prob_fig_width")
        style.height = st.number_input("図高(px)", value=float(style.height), key="prob_fig_height")
        style.dpi = st.selectbox("DPI(PNG用)", [300, 600, 1200], key="prob_fig_dpi")
        show_observed = st.checkbox("観測点表示", value=True, key="prob_show_observed")
        show_fit_line = st.checkbox("適合線表示", value=True, key="prob_show_fit_line")

    if input_value > 0 and input_return_period is not None and math.isfinite(input_return_period) and input_return_period > 1:
        style.horizontal_lines = [
            {
                "y": input_value,
                "label": f"再現確率 約{input_return_period:.1f}年",
                "position": "left",
                "color": "#e377c2",
            }
        ]
        style.vertical_lines = [{"x": input_return_period, "color": "#e377c2"}]

    fig = build_probability_figure(
        annual_maxima_values,
        gumbel_result,
        style,
        plotting_position=plotting_position,
        show_observed=show_observed,
        show_fit_line=show_fit_line,
        x_log=x_log,
        indicator_label=INDICATOR_LABELS_JA[indicator],
    )
    render_interactive_chart(fig, key=f"prob_chart_{station_code}")

    st.subheader("確率雨量表（確率年1〜30・50・100年）")
    table_rows = []
    for t, x in zip(gumbel_result.return_periods, gumbel_result.estimates_mm):
        table_rows.append(
            {
                "確率年": t,
                # 列を文字列で統一する（"算出不可"とfloatが混在するとpyarrowが
                # Arrow変換に失敗し、テーブル描画がクラッシュするため）。
                "確率雨量[mm]": "算出不可" if np.isnan(x) else f"{x:.1f}",
            }
        )
    st.dataframe(pd.DataFrame(table_rows), use_container_width=True, height=300)

    st.subheader("推定パラメータ・適合度")
    gof = gumbel_result.goodness_of_fit
    st.json(
        {
            "位置パラメータmu": gumbel_result.parameters.loc_mu,
            "尺度パラメータbeta": gumbel_result.parameters.scale_beta,
            "採用標本数": gof.n_samples,
            "除外年数": len(excluded),
            "AIC": gof.aic,
            "KS統計量": gof.ks_statistic,
            "RMSE": gof.rmse,
            "相関係数": gof.correlation,
        }
    )

    if len(excluded) > 0:
        st.caption("除外年一覧")
        st.dataframe(excluded[["year_label", "除外理由"]], use_container_width=True)

    st.subheader("画像出力")
    fmt = st.selectbox("形式", ["png", "svg", "pdf"], key="prob_fmt")
    if st.button("画像を生成してダウンロード用に保存", key="prob_export_button"):
        import datetime as dt

        filename = build_export_filename(
            station_name,
            "ガンベル",
            f"{INDICATOR_LABELS_JA[indicator]}_{BOUNDARY_LABELS[boundary_key]}_{method.upper()}",
            dt.date.today(),
            dt.date.today(),
            fmt,
        )
        out_dir = config.resolved_path("paths.output_dir") / "figures"
        out_path = out_dir / filename
        export_figure(fig, out_path, fmt, style.width_px(), style.height_px(), dpi=style.dpi)
        st.success(f"保存しました: {out_path}")
        with open(out_path, "rb") as f:
            st.download_button("ダウンロード", f.read(), file_name=filename, key="prob_dl")
