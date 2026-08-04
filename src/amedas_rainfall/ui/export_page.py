"""データ出力画面（15節）。時別データ・年最大値・確率雨量をParquet/CSV/Excelへ出力する。"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from amedas_rainfall.config import AppConfig
from amedas_rainfall.pipeline import (
    compute_annual_maxima_all_boundaries,
    normalized_hourly_path,
)
from amedas_rainfall.reporting import (
    build_full_excel_workbook,
    export_annual_maxima,
    export_hourly_data,
)
from amedas_rainfall.statistics.annual_maxima import ALL_YEAR_BOUNDARIES
from amedas_rainfall.statistics.gumbel import STANDARD_RETURN_PERIODS, analyze_gumbel
from amedas_rainfall.ui.common import ensure_indices_loaded


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

    basename = f"{station_code}_{station_name}"

    def _make_progress_reporter() -> tuple:
        status = st.empty()
        progress = st.progress(0.0)

        def _cb(fraction: float, message: str) -> None:
            progress.progress(min(max(fraction, 0.0), 1.0))
            status.info(f"ダウンロード中...　{message}（{fraction * 100:.0f}%）")

        def _clear() -> None:
            status.empty()
            progress.empty()

        return _cb, _clear

    if st.button("時別データをParquet/CSV/Excelへ出力", key="export_hourly_button"):
        progress_cb, clear_progress = _make_progress_reporter()
        result = export_hourly_data(
            indices_df,
            config.resolved_path("paths.normalized_dir") / station_code,
            config.resolved_path("paths.output_dir") / "csv",
            config.resolved_path("paths.output_dir") / "excel",
            f"{basename}_hourly",
            progress_callback=progress_cb,
        )
        clear_progress()
        if result["excel"] is None:
            st.warning("行数がExcel上限に近いため、Excel出力は省略しました。Parquet/CSVを利用してください。")
        st.success(f"出力しました: {result}")

    if st.button("年最大値をParquet/CSV/Excelへ出力", key="export_annual_maxima_button"):
        progress_cb, clear_progress = _make_progress_reporter()
        maxima_all = compute_annual_maxima_all_boundaries(indices_df)
        per_boundary = {k: v.get("effective_rainfall_6h_mm") for k, v in maxima_all.items()}
        per_boundary = {k: v for k, v in per_boundary.items() if v is not None}
        result = export_annual_maxima(
            per_boundary,
            config.resolved_path("paths.probability_dir") / station_code,
            config.resolved_path("paths.output_dir") / "csv",
            config.resolved_path("paths.output_dir") / "excel",
            f"{basename}_annual_maxima",
            progress_callback=progress_cb,
        )
        clear_progress()
        st.success(f"出力しました: {result}")

    if st.button("全項目まとめのExcelブックを出力", key="export_full_workbook_button"):
        station_info_df = pd.DataFrame([station])
        maxima_all = compute_annual_maxima_all_boundaries(indices_df)
        maxima_by_boundary = {k: v.get("effective_rainfall_6h_mm") for k, v in maxima_all.items()}
        maxima_by_boundary = {k: v for k, v in maxima_by_boundary.items() if v is not None}

        calendar_maxima = maxima_by_boundary.get("calendar")
        if calendar_maxima is not None and calendar_maxima["max_value"].notna().sum() >= 2:
            gumbel_result = analyze_gumbel(calendar_maxima["max_value"].dropna().to_numpy())
            probability_table = pd.DataFrame(
                {"確率年": gumbel_result.return_periods, "確率雨量[mm]": gumbel_result.estimates_mm}
            )
            params_table = pd.DataFrame(
                [
                    {
                        "mu": gumbel_result.parameters.loc_mu,
                        "beta": gumbel_result.parameters.scale_beta,
                        "手法": gumbel_result.parameters.method,
                        "AIC": gumbel_result.goodness_of_fit.aic,
                    }
                ]
            )
        else:
            probability_table = pd.DataFrame(columns=["確率年", "確率雨量[mm]"])
            params_table = pd.DataFrame(columns=["mu", "beta", "手法", "AIC"])

        missing_table = indices_df[indices_df.get("is_missing", pd.Series(dtype=bool)).fillna(False)] if "is_missing" in indices_df.columns else pd.DataFrame()
        conditions_table = pd.DataFrame(
            [
                {"項目": "連続雨量リセット時間(h)", "値": config.get("rainfall.dry_hours_reset")},
                {"項目": "移動雨量窓(h)", "値": config.get("rainfall.rolling_window_hours")},
                {"項目": "実効雨量半減期(h)", "値": str(config.get("rainfall.effective_half_lives_hours"))},
                {"項目": "出力日時", "値": dt.datetime.now().isoformat()},
            ]
        )
        excel_path = config.resolved_path("paths.output_dir") / "excel" / f"{basename}_全項目.xlsx"
        progress_cb, clear_progress = _make_progress_reporter()
        build_full_excel_workbook(
            excel_path,
            station_info_df,
            indices_df,
            maxima_by_boundary,
            probability_table,
            params_table,
            pd.DataFrame(columns=["year_label", "除外理由"]),
            missing_table,
            conditions_table,
            progress_callback=progress_cb,
        )
        clear_progress()
        st.success(f"出力しました: {excel_path}")
