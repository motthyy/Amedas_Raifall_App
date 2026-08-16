"""データ出力画面。時別データ・全指標の年最大値・確率雨量を出力する。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import streamlit as st

from amedas_rainfall.config import AppConfig
from amedas_rainfall.indicators import annual_indicator_columns, indicator_label
from amedas_rainfall.pipeline import build_annual_analysis, normalized_hourly_path, year_boundaries
from amedas_rainfall.reporting import (
    build_full_excel_workbook,
    export_annual_maxima,
    export_hourly_data,
)
from amedas_rainfall.statistics.gumbel import STANDARD_RETURN_PERIODS, analyze_gumbel
from amedas_rainfall.ui.common import ensure_indices_loaded
from amedas_rainfall.visualization.export import sanitize_filename_part


def _annual_tables(
    config: AppConfig, indices_df: pd.DataFrame, indicators: list[str]
) -> dict[str, pd.DataFrame]:
    """全指標の年最大値と採否情報を年区切りごとにまとめる。"""
    result: dict[str, pd.DataFrame] = {}
    for boundary_key in year_boundaries(config):
        frames = []
        for indicator in indicators:
            table = build_annual_analysis(indices_df, indicator, boundary_key, config=config)
            if not table.empty:
                frames.append(
                    table.assign(
                        indicator=indicator,
                        indicator_label=indicator_label(indicator, annual=True),
                    )
                )
        if frames:
            result[boundary_key] = pd.concat(frames, ignore_index=True)
    return result


def _show_downloads(paths: dict[str, Path | None], key_prefix: str) -> None:
    available = [(kind, path) for kind, path in paths.items() if path is not None and path.exists()]
    if not available:
        return
    columns = st.columns(len(available))
    mime_types = {
        "parquet": "application/octet-stream",
        "csv": "text/csv",
        "json": "application/json",
        "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    for column, (kind, path) in zip(columns, available, strict=True):
        with column:
            st.download_button(
                f"{kind.upper()}をダウンロード",
                data=path.read_bytes(),
                file_name=path.name,
                mime=mime_types.get(kind),
                key=f"{key_prefix}_{kind}",
                width="stretch",
                on_click="ignore",
            )


def render_export_page(config: AppConfig) -> None:
    st.header("データ出力")

    station = st.session_state.get("selected_station")
    if not station:
        st.info("「地点選択・ダウンロード」タブで地点を選択してください。")
        return
    station_code = station["station_code"]
    station_name = station["station_name"]

    if not normalized_hourly_path(config, station_code).exists():
        st.warning("正規化済みデータがありません。")
        return

    indices_df = ensure_indices_loaded(config, station_code)
    indicators = annual_indicator_columns(indices_df.columns)
    boundaries = year_boundaries(config)
    if not indicators or not boundaries:
        st.warning("出力できる指標または年区切り設定がありません。")
        return
    basename = sanitize_filename_part(f"{station_code}_{station_name}")

    def _make_progress_reporter() -> tuple:
        status = st.empty()
        progress = st.progress(0.0)

        def _cb(fraction: float, message: str) -> None:
            progress.progress(min(max(fraction, 0.0), 1.0))
            status.info(f"出力中...　{message}（{fraction * 100:.0f}%）")

        def _clear() -> None:
            status.empty()
            progress.empty()

        return _cb, _clear

    st.subheader("個別出力")
    if st.button("時別データをParquet / CSV / Excelへ出力", key="export_hourly_button"):
        progress_cb, clear_progress = _make_progress_reporter()
        try:
            result = export_hourly_data(
                indices_df,
                config.resolved_path("paths.output_dir") / "parquet",
                config.resolved_path("paths.output_dir") / "csv",
                config.resolved_path("paths.output_dir") / "excel",
                f"{basename}_hourly",
                progress_callback=progress_cb,
            )
            st.session_state[f"export_hourly_{station_code}"] = result
        finally:
            clear_progress()
    hourly_paths = st.session_state.get(f"export_hourly_{station_code}")
    if hourly_paths:
        if hourly_paths["excel"] is None:
            st.warning("行数がExcel上限に近いためExcelは省略しました。CSVまたはParquetをご利用ください。")
        _show_downloads(hourly_paths, f"download_hourly_{station_code}")

    if st.button("全指標の年最大値と採否判定を出力", key="export_annual_maxima_button"):
        progress_cb, clear_progress = _make_progress_reporter()
        try:
            tables = _annual_tables(config, indices_df, indicators)
            result = export_annual_maxima(
                tables,
                config.resolved_path("paths.output_dir") / "parquet",
                config.resolved_path("paths.output_dir") / "csv",
                config.resolved_path("paths.output_dir") / "excel",
                f"{basename}_annual_maxima_all_indicators",
                progress_callback=progress_cb,
            )
            st.session_state[f"export_annual_{station_code}"] = result
        finally:
            clear_progress()
    annual_paths = st.session_state.get(f"export_annual_{station_code}")
    if annual_paths:
        _show_downloads(annual_paths, f"download_annual_{station_code}")

    st.subheader("全項目Excelブック")
    c1, c2 = st.columns(2)
    with c1:
        probability_indicator = st.selectbox(
            "確率解析に使う指標",
            indicators,
            format_func=lambda value: indicator_label(value, annual=True),
            key="export_probability_indicator",
        )
    with c2:
        probability_boundary = st.selectbox(
            "確率解析に使う年区切り",
            list(boundaries),
            format_func=lambda value: boundaries[value].label,
            key="export_probability_boundary",
        )

    if st.button("全項目まとめのExcelブックを出力", key="export_full_workbook_button"):
        progress_cb, clear_progress = _make_progress_reporter()
        try:
            station_info_df = pd.DataFrame([station])
            maxima_by_boundary = _annual_tables(config, indices_df, indicators)
            selected_table = maxima_by_boundary[probability_boundary]
            probability_source = selected_table[
                (selected_table["indicator"] == probability_indicator)
                & selected_table["is_eligible_default"]
                & selected_table["max_value"].notna()
            ]
            if len(probability_source) >= 2:
                try:
                    gumbel_result = analyze_gumbel(
                        probability_source["max_value"].to_numpy(),
                        method=config.get("gumbel.default_estimation_method", "mle"),
                        return_periods_years=config.get(
                            "gumbel.return_periods_years", STANDARD_RETURN_PERIODS
                        ),
                    )
                except ValueError as exc:
                    st.warning(f"確率解析を省略しました: {exc}")
                    gumbel_result = None
            else:
                gumbel_result = None

            if gumbel_result is not None:
                probability_table = pd.DataFrame(
                    {
                        "確率年": gumbel_result.return_periods,
                        "確率雨量[mm]": gumbel_result.estimates_mm,
                    }
                )
                params_table = pd.DataFrame(
                    [
                        {
                            "mu": gumbel_result.parameters.loc_mu,
                            "beta": gumbel_result.parameters.scale_beta,
                            "手法": gumbel_result.parameters.method,
                            "AIC": gumbel_result.goodness_of_fit.aic,
                            "指標": probability_indicator,
                            "年区切り": probability_boundary,
                            "採用年数": len(probability_source),
                        }
                    ]
                )
            else:
                probability_table = pd.DataFrame(columns=["確率年", "確率雨量[mm]"])
                params_table = pd.DataFrame(columns=["mu", "beta", "手法", "AIC"])

            exclusion_table = selected_table[
                (selected_table["indicator"] == probability_indicator)
                & ~selected_table["is_eligible_default"]
            ][["year_label", "completeness_percent", "exclusion_reasons"]]
            missing_mask = indices_df["rainfall_raw_mm"].isna()
            if "is_missing" in indices_df:
                missing_mask |= indices_df["is_missing"].fillna(False).astype(bool)
            missing_table = indices_df.loc[missing_mask]
            conditions_table = pd.DataFrame(
                [
                    {"項目": "連続雨量リセット時間(h)", "値": config.get("rainfall.dry_hours_reset")},
                    {"項目": "移動雨量窓(h)", "値": config.get("rainfall.rolling_window_hours")},
                    {"項目": "実効雨量半減期(h)", "値": str(config.get("rainfall.effective_half_lives_hours"))},
                    {"項目": "年完全性閾値(%)", "値": config.get("annual_maxima.completeness_threshold_percent", 95)},
                    {"項目": "ガンベル推定法", "値": config.get("gumbel.default_estimation_method", "mle")},
                    {"項目": "出力日時", "値": dt.datetime.now().astimezone().isoformat()},
                ]
            )
            excel_path = (
                config.resolved_path("paths.output_dir") / "excel" / f"{basename}_全項目.xlsx"
            )
            build_full_excel_workbook(
                excel_path,
                station_info_df,
                indices_df,
                maxima_by_boundary,
                probability_table,
                params_table,
                exclusion_table,
                missing_table,
                conditions_table,
                progress_callback=progress_cb,
            )
            st.session_state[f"export_full_{station_code}"] = excel_path
        finally:
            clear_progress()

    full_path = st.session_state.get(f"export_full_{station_code}")
    if full_path and Path(full_path).exists():
        path = Path(full_path)
        st.download_button(
            "全項目Excelをダウンロード",
            path.read_bytes(),
            file_name=path.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"download_full_{station_code}",
            on_click="ignore",
        )
