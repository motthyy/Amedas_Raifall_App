"""データ品質画面（12.2節）。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from amedas_rainfall.config import AppConfig
from amedas_rainfall.pipeline import (
    compute_completeness_all_boundaries,
    load_normalized_hourly,
    normalized_hourly_path,
    year_boundaries,
)
from amedas_rainfall.storage.repositories import JobRepository

def render_quality_page(config: AppConfig) -> None:
    st.header("データ品質")

    station = st.session_state.get("selected_station")
    if not station:
        st.info("「地点選択・ダウンロード」タブで地点を選択してください。")
        return

    station_code = station["station_code"]

    db_path = config.resolved_path("paths.jobs_db")
    job_repo = JobRepository(db_path)
    jobs = job_repo.get_jobs_for_station(station_code)

    st.subheader("取得期間一覧（成功／失敗）")
    if jobs:
        jobs_df = pd.DataFrame(
            [
                {
                    "開始日": j.start_date, "終了日": j.end_date, "状態": j.status.value,
                    "試行回数": j.attempt_count, "エラー": j.error_message,
                }
                for j in jobs
            ]
        )
        st.dataframe(jobs_df, width="stretch", height=200)
    else:
        st.info("ダウンロードジョブがまだありません。")

    path = normalized_hourly_path(config, station_code)
    if not path.exists():
        st.warning("正規化済みデータがまだありません。ダウンロード後に「正規化データを再構築」を実行してください。")
        return

    hourly = load_normalized_hourly(config, station_code)

    st.subheader("欠測・重複・競合の状況")
    missing_mask = hourly["rainfall_raw_mm"].isna()
    if "is_missing" in hourly.columns:
        missing_mask = missing_mask | hourly["is_missing"].fillna(False).astype(bool)
    n_missing = int(missing_mask.sum())
    n_conflict = int(hourly["is_conflicting"].sum()) if "is_conflicting" in hourly.columns else None
    c1, c2, c3 = st.columns(3)
    c1.metric("総時間数", len(hourly))
    c2.metric("欠測時間数", n_missing)
    c3.metric("値競合の時間数", n_conflict)

    if n_conflict:
        st.dataframe(
            hourly[hourly["is_conflicting"]][
                [
                    column
                    for column in (
                        "rainfall_raw_mm", "quality_code", "source_file",
                        "conflict_values", "conflict_sources",
                    )
                    if column in hourly.columns
                ]
            ],
            width="stretch",
        )

    st.subheader("年別データ完全率")
    threshold = st.slider(
        "採用可否の完全率閾値(%)", min_value=50.0, max_value=100.0,
        value=float(config.get("annual_maxima.completeness_threshold_percent", 95.0)), step=0.5,
        key="quality_completeness_threshold",
    )
    completeness = compute_completeness_all_boundaries(
        hourly, completeness_threshold_percent=threshold, config=config
    )

    for key, boundary in year_boundaries(config).items():
        label = boundary.label
        st.markdown(f"**{label}**")
        rows = completeness.get(key, [])
        if not rows:
            st.caption("データがありません。")
            continue
        table = pd.DataFrame(
            [
                {
                    "年区分": r.year_label,
                    "開始": r.start_datetime,
                    "終了": r.end_datetime,
                    "想定時間数": r.expected_hours,
                    "有効時間数": r.valid_hours,
                    "欠測時間数": r.missing_hours,
                    "完全率(%)": round(r.completeness_percent, 2),
                    "状態再初期化あり": r.has_state_reset,
                    "採用可否(既定)": r.is_eligible_default,
                    "除外理由": "、".join(r.exclusion_reasons) if r.exclusion_reasons else "",
                }
                for r in rows
            ]
        )
        st.dataframe(table, width="stretch", height=200)

    st.subheader("推定開始日時・最終有効日時")
    valid_idx = hourly.index[hourly["rainfall_raw_mm"].notna()] if "rainfall_raw_mm" in hourly.columns else []
    if len(valid_idx) > 0:
        st.write(f"推定開始日時: {valid_idx.min()}")
        st.write(f"最終有効日時: {valid_idx.max()}")
    else:
        st.write("有効データがありません。")
