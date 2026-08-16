"""マニュアル画面。README.mdの内容をアプリ内に表示する。"""

from __future__ import annotations

import streamlit as st

from amedas_rainfall.config import AppConfig, PROJECT_ROOT


def render_manual_page(config: AppConfig) -> None:
    st.header("マニュアル")

    readme_path = PROJECT_ROOT / "README.md"
    if not readme_path.exists():
        st.warning("README.mdが見つかりません。")
        return

    text = readme_path.read_text(encoding="utf-8")
    st.markdown(text)
