"""アメダス長期雨量・鉄道防災指標解析アプリ エントリーポイント。

起動方法:
    streamlit run app.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import streamlit as st  # noqa: E402

from amedas_rainfall.config import get_default_config  # noqa: E402
from amedas_rainfall.ui.annual_maxima_page import render_annual_maxima_page  # noqa: E402
from amedas_rainfall.ui.export_page import render_export_page  # noqa: E402
from amedas_rainfall.ui.manual_page import render_manual_page  # noqa: E402
from amedas_rainfall.ui.probability_page import render_probability_page  # noqa: E402
from amedas_rainfall.ui.quality_page import render_quality_page  # noqa: E402
from amedas_rainfall.ui.station_page import render_station_page  # noqa: E402
from amedas_rainfall.ui.timeseries_page import render_timeseries_page  # noqa: E402


def _setup_logging(config) -> None:
    logs_dir = config.resolved_path("paths.logs_dir")
    logs_dir.mkdir(parents=True, exist_ok=True)
    import datetime as dt

    log_path = logs_dir / f"app_{dt.date.today():%Y%m%d}.log"
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        root_logger.setLevel(logging.INFO)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        root_logger.addHandler(file_handler)


def main() -> None:
    st.set_page_config(
        page_title="アメダス長期雨量・鉄道防災指標解析アプリ",
        layout="wide",
    )
    config = get_default_config()
    _setup_logging(config)

    st.title("アメダス長期雨量・鉄道防災指標解析アプリ")
    st.caption(
        "気象庁「過去の気象データ・ダウンロード」を用いた時別降水量の取得、"
        "鉄道防災用雨量指標・土壌雨量・ガンベル分布による確率雨量の解析ツールです。"
    )

    # ページ選択(st.radio)を、本文をスクロールしても常時見える状態で固定表示する。
    # data-testid="stElementContainer"は各要素を包むdiv（Streamlit 1.3x以降）。
    # 直前に置いた目印(id="page-nav-anchor")を含むコンテナの次の兄弟コンテナ
    # （＝ラジオボタン本体）だけをposition: stickyにする。
    # 背景色はvar(--background-color)がメイン画面では未定義のため常にフォール
    # バック値（白）が使われてしまい、ダークテーマだと白背景に白文字でタブが
    # 見えなくなっていた。st.context.themeで実テーマを判定して色を決め打ちする。
    nav_bg_color = "#0e1117" if st.context.theme.type == "dark" else "#ffffff"
    st.markdown(
        f"""
        <style>
        div[data-testid="stElementContainer"]:has(#page-nav-anchor) + div[data-testid="stElementContainer"] {{
            position: sticky;
            top: 2.875rem;
            z-index: 999;
            background-color: {nav_bg_color};
            padding-top: 0.5rem;
            padding-bottom: 0.5rem;
        }}
        </style>
        <div id="page-nav-anchor"></div>
        """,
        unsafe_allow_html=True,
    )

    # st.tabs()は非表示のタブも含め、全タブの中身を毎回のスクリプト実行で計算してしまう
    # （表示/非表示はCSSでの切り替えに過ぎない）。そのため、例えば「地点選択・ダウンロード」
    # タブで既にダウンロード済みの地点を選択しただけで、裏側で「時系列グラフ」「確率雨量
    # グラフ」タブの重い指標計算・グラフ構築まで毎回実行されてしまい、フリーズしたように
    # 見えていた。st.radioで選択中のページのみをレンダリングすることでこれを避ける。
    pages = {
        "地点選択・ダウンロード": render_station_page,
        "データ品質": render_quality_page,
        "時系列グラフ": render_timeseries_page,
        "年最大値グラフ": render_annual_maxima_page,
        "確率雨量グラフ": render_probability_page,
        "データ出力": render_export_page,
        "マニュアル": render_manual_page,
    }
    selected_page = st.radio(
        "ページ選択", list(pages.keys()), horizontal=True, key="active_page", label_visibility="collapsed"
    )
    st.divider()
    pages[selected_page](config)


if __name__ == "__main__":
    main()
