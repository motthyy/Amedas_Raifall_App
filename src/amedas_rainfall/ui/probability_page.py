"""確率雨量グラフ画面（12.4節）。"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import streamlit as st

from amedas_rainfall.config import AppConfig
from amedas_rainfall.indicators import annual_indicator_columns, indicator_label
from amedas_rainfall.pipeline import (
    build_annual_analysis,
    normalized_hourly_path,
    year_boundaries,
)
from amedas_rainfall.statistics.bootstrap import bootstrap_return_period_ci, sample_size_warnings
from amedas_rainfall.ui.common import (
    apply_plot_style_to_session,
    default_plot_style,
    ensure_indices_loaded,
    render_interactive_chart,
)
from amedas_rainfall.statistics.gumbel import (
    STANDARD_RETURN_PERIODS,
    analyze_gumbel,
    return_period_from_value,
)
from amedas_rainfall.reporting import export_probability_results
from amedas_rainfall.visualization.export import (
    build_export_filename,
    export_figure,
    load_plot_settings,
    sanitize_filename_part,
    save_plot_settings,
)
from amedas_rainfall.visualization.probability import build_probability_figure
from amedas_rainfall.visualization.styles import PlotStyle

@st.cache_data(show_spinner=False)
def _cached_bootstrap(
    values: tuple[float, ...],
    return_periods: tuple[float, ...],
    method: str,
    iterations: int,
    confidence_level: float,
    seed: int,
):
    return bootstrap_return_period_ci(
        np.asarray(values, dtype=float),
        list(return_periods),
        method=method,
        n_iterations=iterations,
        confidence_level=confidence_level,
        random_seed=seed,
    )


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
    indicator_options = annual_indicator_columns(indices_df.columns)
    boundaries = year_boundaries(config)
    if not indicator_options or not boundaries:
        st.warning("確率解析に使える指標または年区切り設定がありません。")
        return

    settings_path = (
        config.resolved_path("paths.output_dir")
        / "plot_settings"
        / f"{station_code}_probability_settings.json"
    )
    if settings_path.exists() and st.button(
        "保存したグラフ設定を読み込む", key="prob_load_settings_button"
    ):
        style, extra = load_plot_settings(settings_path)
        st.session_state[f"prob_style_{station_code}"] = style
        apply_plot_style_to_session("prob", style)
        for widget_key, extra_key, allowed in (
            ("prob_indicator", "indicator", indicator_options),
            ("prob_boundary_key", "boundary_key", list(boundaries)),
            ("prob_method", "method", ["mle", "moments"]),
            (
                "prob_plotting_position",
                "plotting_position",
                ["gringorten", "weibull", "cunnane"],
            ),
        ):
            if extra.get(extra_key) in allowed:
                st.session_state[widget_key] = extra[extra_key]
        if isinstance(extra.get("x_log"), bool):
            st.session_state["prob_x_log"] = extra["x_log"]
        st.rerun()

    c1, c2 = st.columns(2)
    with c1:
        indicator = st.selectbox(
            "指標", indicator_options, index=0,
            format_func=lambda c: indicator_label(c, annual=True),
            key="prob_indicator",
        )
    with c2:
        boundary_key = st.selectbox(
            "年区切り", list(boundaries.keys()), format_func=lambda k: boundaries[k].label,
            key="prob_boundary_key",
        )

    threshold = st.slider(
        "採用可否の完全率閾値(%)", min_value=50.0, max_value=100.0,
        value=float(config.get("annual_maxima.completeness_threshold_percent", 95.0)), step=0.5,
        key="prob_completeness_threshold",
    )
    maxima_df = build_annual_analysis(
        indices_df,
        indicator,
        boundary_key,
        config=config,
        completeness_threshold_percent=threshold,
    )
    if maxima_df.empty:
        st.warning("年最大値を計算できませんでした。")
        return

    maxima_df = maxima_df.rename(
        columns={"is_eligible_default": "採用可否(既定)", "exclusion_reasons": "除外理由"}
    )

    st.subheader("採用年の選択")
    selection_key = f"annual_selection_{station_code}_{indicator}_{boundary_key}"
    if st.button("採用選択を既定に戻す", key="prob_reset_annual_selection"):
        st.session_state.pop(selection_key, None)
        st.rerun()
    previous_selection = st.session_state.get(selection_key, {})
    if previous_selection:
        maxima_df["採用可否(既定)"] = maxima_df.apply(
            lambda row: previous_selection.get(
                row["year_label"], bool(row["採用可否(既定)"])
            ),
            axis=1,
        )
    edited = st.data_editor(
        maxima_df[
            [
                "year_label",
                "max_value",
                "max_datetime",
                "completeness_percent",
                "採用可否(既定)",
                "除外理由",
            ]
        ],
        column_config={"採用可否(既定)": st.column_config.CheckboxColumn("採用する")},
        disabled=["year_label", "max_value", "max_datetime", "completeness_percent", "除外理由"],
        width="stretch",
        key=f"editor_{station_code}_{indicator}_{boundary_key}_{threshold:g}",
    )
    included = edited[edited["採用可否(既定)"]]
    excluded = edited[~edited["採用可否(既定)"]]
    annual_maxima_values = included["max_value"].dropna().to_numpy()
    st.session_state[selection_key] = dict(
        zip(edited["year_label"], edited["採用可否(既定)"].astype(bool), strict=True)
    )

    if len(annual_maxima_values) < 2:
        st.error("ガンベル分布の推定には少なくとも2年分の採用年最大値が必要です。")
        return

    st.subheader("推定条件")
    c3, c4, c5 = st.columns(3)
    with c3:
        method = st.radio(
            "推定法", ["mle", "moments"],
            index=0 if config.get("gumbel.default_estimation_method", "mle") == "mle" else 1,
            format_func=lambda m: "最尤法" if m == "mle" else "積率法",
            key="prob_method",
        )
    with c4:
        plotting_position = st.radio(
            "プロッティングポジション", ["gringorten", "weibull", "cunnane"],
            index=["gringorten", "weibull", "cunnane"].index(
                config.get("gumbel.default_plotting_position", "gringorten")
            ), key="prob_plotting_position"
        )
    with c5:
        x_log = st.checkbox("横軸を対数表示", value=True, key="prob_x_log")

    return_periods = list(config.get("gumbel.return_periods_years", STANDARD_RETURN_PERIODS))
    if config.get("gumbel.always_include_1_year_as_na", True) and 1 not in return_periods:
        return_periods.insert(0, 1)
    warning_cfg = config.get("gumbel.warnings", {}) or {}
    for w in sample_size_warnings(
        len(annual_maxima_values),
        max(return_periods),
        short_record_years=int(warning_cfg.get("short_record_years", 10)),
        uncertain_record_years=int(warning_cfg.get("uncertain_record_years", 20)),
        extrapolation_factor=float(warning_cfg.get("extrapolation_factor", 3.0)),
    ):
        st.warning(w)

    try:
        gumbel_result = analyze_gumbel(
            annual_maxima_values,
            method=method,
            plotting_position=plotting_position,
            return_periods_years=return_periods,
        )
    except ValueError as exc:
        st.error(str(exc))
        return

    bootstrap_cfg = config.get("gumbel.bootstrap", {}) or {}
    show_ci = st.checkbox("ブートストラップ信頼区間を表示", value=False, key="prob_show_ci")
    confidence_intervals = None
    if show_ci:
        ci1, ci2 = st.columns(2)
        with ci1:
            iterations = st.number_input(
                "反復回数", min_value=100, max_value=10000,
                value=int(bootstrap_cfg.get("default_iterations", 1000)), step=100,
                key="prob_bootstrap_iterations",
            )
        with ci2:
            confidence_level = st.slider(
                "信頼水準", 0.80, 0.99,
                float(bootstrap_cfg.get("default_confidence_level", 0.95)), 0.01,
                key="prob_bootstrap_confidence",
            )
        with st.spinner("ブートストラップ信頼区間を計算しています..."):
            confidence_intervals = _cached_bootstrap(
                tuple(float(value) for value in annual_maxima_values),
                tuple(float(value) for value in return_periods),
                method,
                int(iterations),
                float(confidence_level),
                int(bootstrap_cfg.get("random_seed", 42)),
            )

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
        style_key = f"prob_style_{station_code}"
        automatic_title = (
            f"{station_name} 確率雨量（{indicator_label(indicator, annual=True)}・"
            f"{boundaries[boundary_key].label}）"
        )
        if style_key not in st.session_state:
            st.session_state[style_key] = default_plot_style(
                config,
                title=automatic_title,
            )
        style: PlotStyle = st.session_state[style_key]
        previous_automatic_title = st.session_state.get(f"prob_automatic_title_{station_code}")
        if previous_automatic_title is None or style.title == previous_automatic_title:
            style.title = automatic_title
            st.session_state["prob_title"] = automatic_title
        st.session_state[f"prob_automatic_title_{station_code}"] = automatic_title
        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            style.size_unit = st.radio(
                "単位", ["px", "mm"], index=0 if style.size_unit == "px" else 1,
                key="prob_size_unit",
            )
            style.width = st.number_input("図幅", value=float(style.width), key="prob_fig_width")
            style.height = st.number_input("図高", value=float(style.height), key="prob_fig_height")
        with sc2:
            dpi_choices = list(config.get("figure_export.default_dpi_choices", [300, 600, 1200]))
            if style.dpi not in dpi_choices:
                dpi_choices.append(style.dpi)
            style.dpi = st.selectbox(
                "DPI(PNG用)", dpi_choices, index=dpi_choices.index(style.dpi), key="prob_fig_dpi"
            )
            style.font_size = st.number_input(
                "基本フォントサイズ", value=style.font_size, key="prob_font_size"
            )
            style.line_width = st.number_input(
                "線幅", value=float(style.line_width), key="prob_line_width"
            )
        with sc3:
            style.grayscale = st.checkbox(
                "白黒モード", value=style.grayscale, key="prob_grayscale"
            )
            style.show_grid = st.checkbox(
                "グリッド表示", value=style.show_grid, key="prob_show_grid"
            )
            show_observed = st.checkbox("観測点表示", value=True, key="prob_show_observed")
            show_fit_line = st.checkbox("適合線表示", value=True, key="prob_show_fit_line")
        style.title = st.text_input("タイトル", value=style.title, key="prob_title")
        style.subtitle = st.text_input("サブタイトル", value=style.subtitle, key="prob_subtitle")
        style.note = st.text_area("注記", value=style.note, key="prob_note")

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
    else:
        style.horizontal_lines = []
        style.vertical_lines = []

    fig = build_probability_figure(
        annual_maxima_values,
        gumbel_result,
        style,
        plotting_position=plotting_position,
        show_observed=show_observed,
        show_fit_line=show_fit_line,
        x_log=x_log,
        indicator_label=indicator_label(indicator, annual=True),
        confidence_intervals=confidence_intervals,
    )
    render_interactive_chart(fig, key=f"prob_chart_{station_code}")

    st.subheader("確率雨量表（確率年1〜30・50・100年）")
    table_rows = []
    for t, x in zip(gumbel_result.return_periods, gumbel_result.estimates_mm, strict=True):
        table_rows.append(
            {
                "確率年": t,
                # 列を文字列で統一する（"算出不可"とfloatが混在するとpyarrowが
                # Arrow変換に失敗し、テーブル描画がクラッシュするため）。
                "確率雨量[mm]": "算出不可" if not np.isfinite(x) else f"{x:.1f}",
                "信頼区間下限[mm]": (
                    "" if not confidence_intervals or t not in confidence_intervals
                    else f"{confidence_intervals[t].lower:.1f}"
                ),
                "信頼区間上限[mm]": (
                    "" if not confidence_intervals or t not in confidence_intervals
                    else f"{confidence_intervals[t].upper:.1f}"
                ),
            }
        )
    probability_df = pd.DataFrame(table_rows)
    st.dataframe(probability_df, width="stretch", height=300)

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
        st.dataframe(excluded[["year_label", "除外理由"]], width="stretch")

    if st.button("確率雨量結果をCSV/JSON/Excelへ出力", key="prob_data_export_button"):
        parameters_df = pd.DataFrame(
            [
                {
                    "mu": gumbel_result.parameters.loc_mu,
                    "beta": gumbel_result.parameters.scale_beta,
                    "method": method,
                    "plotting_position": plotting_position,
                    "sample_count": len(annual_maxima_values),
                    "indicator": indicator,
                    "year_boundary": boundary_key,
                    "completeness_threshold_percent": threshold,
                }
            ]
        )
        basename = sanitize_filename_part(
            f"{station_code}_{station_name}_{indicator}_{boundary_key}_{method}"
        )
        paths = export_probability_results(
            probability_df,
            parameters_df,
            config.resolved_path("paths.output_dir") / "csv",
            config.resolved_path("paths.output_dir") / "excel",
            basename,
        )
        st.session_state[f"prob_export_paths_{station_code}"] = paths
        st.success("確率雨量結果を出力しました。")
    probability_paths = st.session_state.get(f"prob_export_paths_{station_code}", {})
    if probability_paths:
        columns = st.columns(len(probability_paths))
        for column, (kind, path) in zip(columns, probability_paths.items(), strict=True):
            with column:
                mime = {
                    "csv": "text/csv",
                    "json": "application/json",
                    "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                }.get(kind)
                st.download_button(
                    f"{kind.upper()}をダウンロード",
                    data=path.read_bytes(),
                    file_name=path.name,
                    mime=mime,
                    key=f"prob_data_download_{station_code}_{kind}",
                    width="stretch",
                    on_click="ignore",
                )

    if st.button("グラフ設定を保存(JSON)", key="prob_save_settings_button"):
        save_plot_settings(
            style,
            {
                "indicator": indicator,
                "boundary_key": boundary_key,
                "method": method,
                "plotting_position": plotting_position,
                "x_log": x_log,
            },
            settings_path,
        )
        st.success(f"保存しました: {settings_path}")

    st.subheader("画像出力")
    fmt = st.selectbox("形式", ["png", "svg", "pdf"], key="prob_fmt")
    if st.button("画像を生成してダウンロード用に保存", key="prob_export_button"):
        import datetime as dt

        filename = build_export_filename(
            station_name,
            "ガンベル",
            f"{indicator_label(indicator, annual=True)}_{boundaries[boundary_key].label}_{method.upper()}",
            dt.date.today(),
            dt.date.today(),
            fmt,
        )
        out_dir = config.resolved_path("paths.output_dir") / "figures"
        out_path = out_dir / filename
        export_figure(fig, out_path, fmt, style.width_px(), style.height_px(), dpi=style.dpi)
        st.session_state[f"prob_image_export_{station_code}"] = out_path
        st.success(f"保存しました: {out_path}")
    image_path = st.session_state.get(f"prob_image_export_{station_code}")
    if image_path and image_path.exists():
        st.download_button(
            "画像をダウンロード", image_path.read_bytes(), file_name=image_path.name,
            key=f"prob_dl_{station_code}",
            on_click="ignore",
        )
