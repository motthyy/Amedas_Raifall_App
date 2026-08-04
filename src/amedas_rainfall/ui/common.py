"""複数画面で共通して使うヘルパー関数。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from amedas_rainfall.config import AppConfig
from amedas_rainfall.pipeline import (
    indices_cache_path,
    load_or_compute_all_indices,
    normalized_hourly_path,
)


def ensure_indices_loaded(config: AppConfig, station_code: str, force_recompute: bool = False) -> pd.DataFrame:
    """指標データフレームをセッション内にロードする。

    キャッシュ（data/calculated/{地点コード}/indices.parquet）があれば瞬時に読み込み、
    なければ計算しながら進捗バーを表示する。計算結果はディスクとセッションの両方へ保存する。
    """
    cache_key = f"indices_df_{station_code}"
    if not force_recompute and cache_key in st.session_state:
        return st.session_state[cache_key]

    cache_path = indices_cache_path(config, station_code)
    hourly_path = normalized_hourly_path(config, station_code)
    needs_compute = force_recompute or not (
        cache_path.exists() and hourly_path.exists() and cache_path.stat().st_mtime >= hourly_path.stat().st_mtime
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
    return indices_df


def render_interactive_chart(fig, key: str) -> None:
    """グラフをインタラクティブ表示する（マウスホイールでズーム、点クリックで日時・値を表示）。

    画像出力（export_figure）には渡したfigをそのまま使うため、ここで行う表示上の
    工夫（scrollZoom, on_select）はエクスポート画像の見た目に一切影響しない。
    """
    event = st.plotly_chart(
        fig,
        use_container_width=True,
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
