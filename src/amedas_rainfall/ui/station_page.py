"""地点選択・ダウンロード画面（12.1節）。"""

from __future__ import annotations

import datetime as dt
import logging

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from amedas_rainfall.config import AppConfig
from amedas_rainfall.jma.ame_master_pdf import (
    AmeMasterPdfError,
    attach_precip_start_dates,
    fetch_ame_master_pdf_bytes,
    parse_ame_master_pdf,
    save_ame_master_pdf_cache,
)
from amedas_rainfall.jma.client_factory import create_jma_client
from amedas_rainfall.jma.download_manager import DownloadManager, DownloadManagerConfig
from amedas_rainfall.jma.start_date_finder import (
    StartDateProbeError,
    default_start_year_hint,
    find_earliest_valid_year,
)
from amedas_rainfall.jma.station_catalog import (
    StationMasterBuildError,
    build_station_master,
    load_station_master,
    save_station_master,
    station_master_cache_exists,
)
from amedas_rainfall.models import JobStatus
from amedas_rainfall.storage.repositories import JobRepository

logger = logging.getLogger(__name__)

JST = dt.timezone(dt.timedelta(hours=9))

DOWNLOAD_CHUNK_SIZE = 1
"""1回のStreamlitスクリプト実行で処理するダウンロードジョブ数の上限。

大きくすると1回あたりの処理時間が延び画面の応答性が下がり、小さくすると
st.rerun()の呼び出し回数が増える（オーバーヘッドは小さい）。"""


def _station_master_path(config: AppConfig):
    return config.resolved_path("paths.station_master_dir") / "stations.parquet"


def _ame_master_pdf_cache_path(config: AppConfig):
    return config.resolved_path("paths.ame_master_pdf_dir") / "precip_start_dates.parquet"


def render_station_page(config: AppConfig) -> None:
    st.header("地点選択・ダウンロード")

    master_path = _station_master_path(config)

    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button(
            "地点マスタを更新", help="気象庁サイトから全都道府県の地点一覧を再取得します（数分かかります）",
            key="station_refresh_master_button",
        ):
            try:
                with st.spinner("地点マスタを取得しています（都道府県ごとに待機時間を挟みます）..."):
                    progress = st.progress(0.0)
                    status_text = st.empty()

                    def _cb(i: int, total: int, label: str) -> None:
                        progress.progress(i / total)
                        status_text.text(f"{i}/{total}: {label}")

                    with create_jma_client(config) as client:
                        df = build_station_master(
                            client,
                            wait_seconds=config.get("download.normal_wait_seconds", 3.0),
                            progress_callback=_cb,
                        )

                    status_text.text("気象庁「地域気象観測所一覧」PDFから降水量観測開始日を取得しています...")
                    try:
                        pdf_bytes = fetch_ame_master_pdf_bytes(
                            url=config.get(
                                "jma_master_pdf.url",
                                "https://www.jma.go.jp/jma/kishou/know/amedas/ame_master.pdf",
                            ),
                            user_agent=config.get("download.user_agent"),
                        )
                        ame_master_df = parse_ame_master_pdf(pdf_bytes)
                        save_ame_master_pdf_cache(ame_master_df, _ame_master_pdf_cache_path(config))
                        df = attach_precip_start_dates(df, ame_master_df)
                    except AmeMasterPdfError:
                        logger.exception("観測所一覧PDFの取得・解析に失敗しました。")
                        st.warning(
                            "観測所一覧PDFの取得・解析に失敗しました。開始年の手動探索は利用できます。"
                        )

                    save_station_master(df, master_path)
                st.success(f"{len(df)}件の地点情報を取得しました。")
                st.session_state.pop("station_master_df", None)
            except StationMasterBuildError as exc:
                logger.exception("地点マスタの更新に失敗しました。")
                st.error(str(exc))
            except Exception as exc:  # noqa: BLE001
                logger.exception("地点マスタの更新中に予期しないエラーが発生しました。")
                st.error(f"地点マスタを更新できませんでした。通信状態を確認して再試行してください: {exc}")

    with col2:
        if station_master_cache_exists(master_path):
            mtime = dt.datetime.fromtimestamp(master_path.stat().st_mtime)
            st.caption(f"キャッシュ済み地点マスタ（最終更新: {mtime:%Y-%m-%d %H:%M}）を使用します。")
        else:
            st.warning("地点マスタのキャッシュがありません。「地点マスタを更新」を押してください。")
            return

    if "station_master_df" not in st.session_state:
        st.session_state.station_master_df = load_station_master(master_path)
    df = st.session_state.station_master_df

    st.subheader("地点検索")
    c1, c2, c3 = st.columns(3)
    with c1:
        prefectures = ["すべて"] + sorted(df["prefecture"].unique().tolist())
        selected_pref = st.selectbox("都道府県", prefectures, key="station_pref_filter")
    with c2:
        name_filter = st.text_input("地点名（部分一致）", "", key="station_name_filter")
    with c3:
        code_filter = st.text_input("地点コード", "", key="station_code_filter")

    c4, c5, c6 = st.columns(3)
    with c4:
        only_observing = st.checkbox("現在観測中のみ", value=True, key="station_only_observing")
    with c5:
        only_precip = st.checkbox("降水量観測地点のみ", value=True, key="station_only_precip")
    with c6:
        station_types = ["すべて"] + sorted(df["station_type"].unique().tolist())
        selected_type = st.selectbox("地点種別", station_types, key="station_type_filter")

    filtered = df.copy()
    if selected_pref != "すべて":
        filtered = filtered[filtered["prefecture"] == selected_pref]
    if name_filter:
        filtered = filtered[filtered["station_name"].str.contains(name_filter, na=False, regex=False)]
    if code_filter:
        filtered = filtered[filtered["station_code"].str.contains(code_filter, na=False, regex=False)]
    if only_observing:
        filtered = filtered[filtered["is_currently_observing"]]
    if only_precip:
        filtered = filtered[filtered["has_precipitation_observation"]]
    if selected_type != "すべて":
        filtered = filtered[filtered["station_type"] == selected_type]

    st.caption(f"該当地点数: {len(filtered)}")
    display_cols = [
        "prefecture", "station_name", "station_code", "station_type",
        "latitude", "longitude", "elevation_m", "is_currently_observing",
        "has_precipitation_observation",
    ]
    st.dataframe(filtered[display_cols], width="stretch", height=300)

    station_options = filtered["station_code"] + " / " + filtered["station_name"] + "（" + filtered["prefecture"] + "）"
    if station_options.empty:
        st.info("条件に合致する地点がありません。")
        return
    selected_label = st.selectbox("地点を選択", station_options.tolist(), key="station_selected_label")
    selected_code = selected_label.split(" / ")[0]
    station_row = filtered[filtered["station_code"] == selected_code].iloc[0]
    st.session_state.selected_station = station_row.to_dict()

    if station_row["latitude"] is not None and pd.notna(station_row["latitude"]):
        fig = go.Figure(
            go.Scattermapbox(
                lat=[station_row["latitude"]],
                lon=[station_row["longitude"]],
                mode="markers",
                marker=dict(size=14, color="red"),
                text=[station_row["station_name"]],
            )
        )
        fig.update_layout(
            mapbox=dict(
                style="open-street-map",
                center=dict(lat=station_row["latitude"], lon=station_row["longitude"]),
                zoom=8,
            ),
            height=350,
            margin=dict(t=0, b=0, l=0, r=0),
        )
        st.plotly_chart(fig, width="stretch", theme=None)

    pdf_precip_start_date = station_row.get("precip_start_date")
    if pdf_precip_start_date is not None and pd.notna(pdf_precip_start_date):
        pdf_precip_start_date = pd.Timestamp(pdf_precip_start_date).date()
    else:
        pdf_precip_start_date = None

    is_amedas = station_row["station_type"] == "アメダス"
    hint_year = pdf_precip_start_date.year if pdf_precip_start_date else default_start_year_hint(is_amedas)

    if st.session_state.get("_station_page_active_code") != selected_code:
        # st.number_input/st.date_inputはkeyが既にsession_stateにある場合value引数を
        # 無視するため、地点切替時はウィジェット生成前にここで明示的に書き換える。
        # そうしないと前の地点で選んだ値が残り続け、新しい地点のPDF由来の開始日が
        # 自動反映されない。ユーザーは以降自由に変更できる。
        st.session_state["_station_page_active_code"] = selected_code
        st.session_state.pop("start_search_result", None)
        st.session_state["station_manual_year"] = hint_year
        st.session_state["station_plan_start"] = pdf_precip_start_date or dt.date(hint_year, 1, 1)

    st.divider()
    st.subheader("時別降水量の取得可能開始日時の調査")

    if pdf_precip_start_date:
        st.caption(
            f"気象庁「地域気象観測所一覧」より: 降水量観測開始日 {pdf_precip_start_date:%Y-%m-%d}"
            "（ダウンロード計画の開始日に自動反映されます。下記の「開始年を探索する」で"
            "実測に基づく調査結果に置き換えることもできます）"
        )
    else:
        st.caption(
            "気象庁「地域気象観測所一覧」に降水量観測開始日の情報が見つかりませんでした"
            "（地点マスタが未更新か、地点名の突き合わせができなかった可能性があります）。"
            "下記の「開始年を探索する」で実測に基づき調査してください。"
        )

    manual_year = st.number_input(
        "探索開始年（手動修正可）", min_value=1875, max_value=dt.date.today().year,
        key="station_manual_year",
    )

    if st.button("開始年を探索する", key="station_search_start_year_button"):
        try:
            with st.spinner("観測開始年を探索しています..."):
                with create_jma_client(config) as client:
                    result = find_earliest_valid_year(
                        client,
                        selected_code,
                        candidate_year_hint=int(manual_year),
                        current_year=dt.date.today().year,
                        wait_seconds=config.get("download.normal_wait_seconds", 3.0),
                    )
            st.session_state["start_search_result"] = result
            if result.earliest_valid_datetime:
                st.session_state["station_plan_start"] = dt.date(
                    result.earliest_valid_datetime.year, 1, 1
                )
        except StartDateProbeError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("観測開始年の探索に失敗しました。")
            st.error(f"観測開始年を探索できませんでした: {exc}")

    if "start_search_result" in st.session_state:
        result = st.session_state["start_search_result"]
        if result.earliest_valid_datetime:
            st.success(f"推定開始日時: {result.earliest_valid_datetime:%Y-%m-%d %H:%M}")
        else:
            st.warning("開始日時を自動確定できませんでした。手動で開始年を指定してください。")
        st.caption(f"確認した年: {result.candidate_years_checked}")
        for note in result.notes:
            st.caption(f"備考: {note}")

    st.divider()
    st.subheader("ダウンロード計画")

    if (
        st.session_state.get("start_search_result")
        and st.session_state["start_search_result"].earliest_valid_datetime
    ):
        # 1. 実測探索（「開始年を探索する」）の結果を最優先する（ユーザーが明示的に実行した結果のため）。
        default_start = dt.date(st.session_state["start_search_result"].earliest_valid_datetime.year, 1, 1)
    elif pdf_precip_start_date:
        # 2. 気象庁「地域気象観測所一覧」PDF由来の観測開始日を自動反映する。
        default_start = pdf_precip_start_date
    else:
        # 3. どちらもなければ従来どおりの粗いヒントにフォールバックする。
        default_start = dt.date(int(manual_year), 1, 1)
    st.session_state.setdefault("station_plan_start", default_start)
    plan_start = st.date_input("開始日", key="station_plan_start")
    yesterday = dt.date.today() - dt.timedelta(days=1)
    plan_end = st.date_input(
        "終了日（最新取得可能日）", value=yesterday, max_value=yesterday, key="station_plan_end"
    )

    st.caption(
        "ダウンロード開始を押すと、1件ずつ順番に取得・保存を確認しながら最新データまで自動的に"
        "続けて取得します（気象庁サイトへの負荷対策として待機時間を挟みます）。"
        "失敗した期間は自動的に次の期間へ進み、後で「失敗・中断ジョブを再試行対象に戻す」からやり直せます。"
        "ジョブ状態はSQLiteに保存されるため、アプリを閉じても次回起動時に続きから再開できます。"
        "強制終了などでダウンロードが中断された場合も、同じボタンから再試行できます。"
    )

    db_path = config.resolved_path("paths.jobs_db")
    job_repo = JobRepository(db_path)
    manager_config = DownloadManagerConfig(
        normal_wait_seconds=config.get("download.normal_wait_seconds", 3.0),
        min_wait_seconds=config.get("download.min_wait_seconds", 2.0),
        retry_wait_seconds=tuple(config.get("download.retry_wait_seconds", [10, 30, 120])),
        backoff_multiplier=config.get("download.backoff_multiplier", 2.0),
        max_retries_per_span=config.get("download.max_retries_per_span", 5),
        split_sequence=tuple(
            config.get(
                "download.split_sequence", ["1year", "6month", "3month", "1month", "7day"]
            )
        ),
    )
    # DownloadManager側が停止可能な共有スロット待機を行うため、ここだけ二重適用を避ける。
    client = create_jma_client(config, global_throttle=False)
    manager = DownloadManager(client, job_repo, config.resolved_path("paths.raw_dir"), manager_config)

    if st.button("ダウンロード計画を作成/更新", key="station_create_plan_button"):
        try:
            job_ids = manager.plan_jobs(selected_code, plan_start, plan_end)
            st.success(f"{len(job_ids)}件の年単位ジョブを計画しました（既存分は再利用します）。")
        except ValueError as exc:
            st.error(str(exc))

    jobs = job_repo.get_jobs_for_station(selected_code)
    if jobs:
        jobs_df = pd.DataFrame(
            [
                {
                    "開始日": j.start_date, "終了日": j.end_date, "状態": j.status.value,
                    "試行回数": j.attempt_count, "行数": j.row_count, "保存ファイル": j.saved_file,
                    "次回試行": j.next_attempt_at, "エラー": j.error_message,
                }
                for j in jobs
            ]
        )
        st.dataframe(jobs_df, width="stretch", height=250)

        non_split_jobs = [j for j in jobs if j.status != JobStatus.SPLIT]
        n_pending = sum(
            1 for j in non_split_jobs
            if j.status in (JobStatus.PENDING, JobStatus.RETRY_WAIT, JobStatus.DOWNLOADING)
        )
        n_success = sum(1 for j in non_split_jobs if j.status in (JobStatus.SUCCESS, JobStatus.VALIDATED))
        n_failed = sum(1 for j in non_split_jobs if j.status == JobStatus.FAILED)
        n_cancelled = sum(1 for j in non_split_jobs if j.status == JobStatus.CANCELLED)
        n_total = len(non_split_jobs)
        st.caption(
            f"完了: {n_success} / 未完了: {n_pending} / 失敗: {n_failed} / "
            f"計画外: {n_cancelled} / 合計: {n_total}"
        )
        n_stuck = sum(1 for j in non_split_jobs if j.status == JobStatus.DOWNLOADING)
        if n_stuck and not st.session_state.get(f"auto_download_running_{selected_code}", False):
            st.warning(
                f"{n_stuck}件のジョブがダウンロード中の状態のまま止まっています。"
                "前回アプリが強制終了された可能性があります。"
                "「失敗・中断ジョブを再試行対象に戻す」を押して再試行してください。"
            )

        auto_run_key = f"auto_download_running_{selected_code}"
        is_running = st.session_state.get(auto_run_key, False)

        b1, b2, b3, b4 = st.columns(4)
        with b1:
            start_download = st.button(
                "ダウンロード開始", key="station_run_batch_button", type="primary",
                width="stretch", disabled=is_running,
            )
        with b2:
            if st.button("失敗・中断ジョブを再試行対象に戻す", key="station_retry_failed_button"):
                n = manager.retry_failed(selected_code)
                st.success(f"{n}件のジョブを再試行対象(PENDING)に戻しました。")
                st.rerun()
        with b3:
            start_analysis = st.button(
                "データ解析", key="station_rebuild_normalized_button", width="stretch",
                disabled=is_running,
            )
        with b4:
            stop_download = st.button(
                "ダウンロード停止", key="station_stop_download_button", width="stretch",
                disabled=not is_running,
            )

        if start_analysis:
            from amedas_rainfall.pipeline import load_or_compute_all_indices, rebuild_normalized_from_raw

            analysis_status = st.empty()
            analysis_progress = st.progress(0.0)
            analysis_percent = st.empty()
            analysis_status.info("データ解析中...")

            try:
                merged = rebuild_normalized_from_raw(config, selected_code, station_row["station_name"])
                analysis_percent.text(f"正規化データを統合しました（{len(merged)}時間分）。指標を計算しています...")

                def _analysis_progress(fraction: float, message: str) -> None:
                    ratio = min(max(fraction, 0.0), 1.0)
                    analysis_progress.progress(ratio)
                    analysis_percent.text(f"{message}（{ratio * 100:.0f}%）")

                indices_df = load_or_compute_all_indices(
                    config,
                    selected_code,
                    hourly_df=merged,
                    force_recompute=True,
                    progress_callback=_analysis_progress,
                )
                st.session_state[f"indices_df_{selected_code}"] = indices_df

                analysis_progress.progress(1.0)
                analysis_status.success(
                    f"データ解析が完了しました（{len(merged)}時間分）。"
                    "計算結果は保存されるため、次回以降は再計算不要です。"
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("データ解析に失敗しました。")
                analysis_status.error(f"データ解析に失敗しました: {exc}")

        if start_download:
            reset_count = manager.reconcile_validated_files(selected_code)
            if reset_count:
                st.warning(f"破損または欠落している保存済みCSV {reset_count}件を再取得対象に戻しました。")
            st.session_state[auto_run_key] = True
            is_running = True

        if stop_download:
            st.session_state[auto_run_key] = False
            is_running = False
            st.info("ダウンロードを停止しました。「ダウンロード開始」で未完了ジョブから再開できます。")

        if is_running:
            # 1回のスクリプト実行では少数のジョブのみ処理し、st.rerun()で次の
            # チャンクへ進む。全ジョブを1回のブロッキング呼び出しで処理すると、
            # 数十年分のダウンロードで数分間ブラウザが無応答になる(フリーズする)ため、
            # ジョブ数の多いダウンロードでも画面の応答性を保つための対策である。
            status_banner = st.empty()
            progress_bar = st.progress(0.0)
            percent_text = st.empty()
            log_area = st.empty()
            status_banner.info("ダウンロード中...（少しずつ進み、途中で「ダウンロード停止」を押せます）")
            logs: list[str] = []

            def _progress(msg: str) -> None:
                logs.append(msg)
                log_area.text("\n".join(logs[-20:]))
                current_jobs = [j for j in job_repo.get_jobs_for_station(selected_code) if j.status != JobStatus.SPLIT]
                total = len(current_jobs) or 1
                done = sum(
                    1 for j in current_jobs if j.status in (JobStatus.SUCCESS, JobStatus.VALIDATED, JobStatus.FAILED)
                )
                ratio = min(done / total, 1.0)
                progress_bar.progress(ratio)
                percent_text.text(f"{done}/{total}件 完了（{ratio * 100:.0f}%）")

            try:
                manager.run(
                    selected_code, station_row["station_name"], progress_callback=_progress,
                    max_jobs=DOWNLOAD_CHUNK_SIZE,
                )
            finally:
                client.close()

            remaining = manager.job_repo.get_actionable_jobs(selected_code)
            if remaining:
                st.rerun()
            else:
                st.session_state[auto_run_key] = False
                waiting = manager.job_repo.get_waiting_retry_jobs(selected_code)
                failed = manager.job_repo.get_failed_jobs(selected_code)
                if waiting:
                    next_time = min(
                        (job.next_attempt_at for job in waiting if job.next_attempt_at), default=None
                    )
                    status_banner.warning(
                        f"再試行待ちが{len(waiting)}件あります。"
                        + (f" 次回試行可能: {next_time:%Y-%m-%d %H:%M:%S}" if next_time else "")
                        + " 時刻を過ぎてから「ダウンロード開始」を押すと再開します。"
                    )
                elif failed:
                    status_banner.warning(
                        f"取得処理は終了しましたが、失敗が{len(failed)}件あります。"
                        "詳細を確認し、必要なら再試行対象に戻してください。"
                    )
                else:
                    status_banner.success("ダウンロードと保存ファイル検証が完了しました。")
                    progress_bar.progress(1.0)
    else:
        st.info("まだダウンロード計画がありません。「ダウンロード計画を作成/更新」を押してください。")
