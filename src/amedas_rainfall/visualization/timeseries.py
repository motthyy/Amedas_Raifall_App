"""時系列グラフの構築（12.3節）。上段: 時雨量棒グラフ、下段: 指標の折れ線グラフ。"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from amedas_rainfall.visualization.styles import PlotStyle

MAX_DISPLAY_POINTS = 5_000
"""この点数を超える期間を表示する際、ピーク(最大値)を保ったまま間引く。

長期間（数十年分）の時別データをそのままPlotlyへ渡すと、図の構築・
ブラウザへのJSON転送・描画のいずれも著しく重くなる（実測: 67万点で
図構築16秒+JSON化41秒）。雨量・防災指標はピーク値が最も重要な情報のため、
単純な間引きではなくバケットごとの最大値を採用し、極端な降雨イベントが
グラフから消えないようにする。詳細確認が必要な場合は表示期間を絞り込む。
"""

BAR_RENDER_MAX_POINTS = 2_000
"""この点数を超える場合、時雨量をgo.Bar（SVG）ではなくgo.Scattergl（WebGL）の
塗りつぶし線で描画する。

go.Barは棒1本ごとにSVG要素を生成するため、万単位の本数になるとブラウザの
描画スレッドが長時間ブロックされる（実測: 20,000点をgo.Barで描画すると、
CPU 6倍スロットリング環境で期間変更のたびに合計約9.6秒のロングタスクが
発生し、操作不能な「フリーズ」状態になる）。WebGL描画に切り替えることで
同じ視覚情報（各時刻の降雨強度）を保ちながら描画コストを桁違いに削減できる。
短期間表示では棒グラフの方が判読しやすいため、閾値以下ではgo.Barを維持する。
"""

INDICATOR_LABELS = {
    "continuous_rainfall_12h_mm": "12時間無降雨リセット連続雨量 [mm]",
    "rolling_rainfall_24h_mm": "24時間移動雨量 [mm]",
    "effective_rainfall_3h_mm": "実効雨量(半減期3時間) [mm]",
    "effective_rainfall_6h_mm": "実効雨量(半減期6時間) [mm]",
    "effective_rainfall_24h_mm": "実効雨量(半減期24時間) [mm]",
    "soil_rainfall_mm": "土壌雨量",
    "soil_tank_1_mm": "第1タンク貯留量 [mm]",
    "soil_tank_2_mm": "第2タンク貯留量 [mm]",
    "soil_tank_3_mm": "第3タンク貯留量 [mm]",
}

RAINFALL_BAR_LABELS = {
    "rainfall_raw_mm": "時雨量 [mm/h]",
}


def _downsample_for_display(
    df: pd.DataFrame,
    bar_column: str,
    indicator_columns: list[str],
    missing_mask: pd.Series | None,
    max_points: int,
) -> tuple[pd.DataFrame, pd.Series | None]:
    """表示点数がmax_pointsを超える場合、バケットごとの最大値を保ったまま間引く。

    各バケットの代表時刻はバケット内先頭の時刻とする（列ごとに最大値を取る値の
    実際の発生時刻とは一致しない場合があるが、長期間の概観表示としては許容する。
    詳細な時刻確認が必要な場合は表示期間を絞り込む）。
    """
    n = len(df)
    if n <= max_points:
        return df, missing_mask

    bucket_size = math.ceil(n / max_points)
    bucket = np.arange(n) // bucket_size

    cols = [c for c in {bar_column, *indicator_columns} if c in df.columns]
    grouped = df[cols].groupby(bucket)
    downsampled = grouped.max()
    representative_time = df.index.to_series().groupby(bucket).first()
    downsampled.index = pd.DatetimeIndex(representative_time.values, tz=df.index.tz)

    down_missing = None
    if missing_mask is not None and bar_column in downsampled.columns:
        down_missing = downsampled[bar_column].isna()

    return downsampled, down_missing


def build_timeseries_figure(
    df: pd.DataFrame,
    bar_column: str,
    indicator_columns: list[str],
    style: PlotStyle,
    missing_mask: pd.Series | None = None,
    max_display_points: int = MAX_DISPLAY_POINTS,
) -> go.Figure:
    """上段に時雨量の棒グラフ、下段に選択指標の折れ線グラフを表示する図を作る。"""
    df, missing_mask = _downsample_for_display(
        df, bar_column, indicator_columns, missing_mask, max_display_points
    )

    if df.index.tz is not None:
        # tz付きのDatetimeIndexはPandasのTimestampオブジェクトのまま(dtype=object)で
        # 保持されるため、Kaleido v1のシリアライザ(orjson)がJSON化に失敗し画像出力が
        # 例外で落ちる。表示は常にJSTのローカル時刻のままでよいため、tz情報だけを
        # 落として(数値は変えず)ネイティブなdatetime64にし、シリアライズ可能にする。
        df = df.tz_localize(None)

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.35, 0.65],
        subplot_titles=(RAINFALL_BAR_LABELS.get(bar_column, bar_column), "選択指標"),
    )

    bar_color = style.style_cycle()[0]["color"]
    if len(df) > BAR_RENDER_MAX_POINTS:
        # 大量点数はSVGのgo.Barだとブラウザが固まるため、WebGL(Scattergl)の
        # 塗りつぶし線（階段状）で見た目を維持しつつ描画コストを抑える。
        fig.add_trace(
            go.Scattergl(
                x=df.index,
                y=df[bar_column],
                name=RAINFALL_BAR_LABELS.get(bar_column, bar_column),
                mode="lines",
                line=dict(width=0.5, color=bar_color, shape="hv"),
                fill="tozeroy",
                fillcolor=bar_color,
            ),
            row=1,
            col=1,
        )
    else:
        fig.add_trace(
            go.Bar(
                x=df.index,
                y=df[bar_column],
                name=RAINFALL_BAR_LABELS.get(bar_column, bar_column),
                marker_color=bar_color,
                width=None,
            ),
            row=1,
            col=1,
        )

    cycle = style.style_cycle()
    for i, col in enumerate(indicator_columns):
        if col not in df.columns:
            continue
        sc = cycle[i % len(cycle)]
        fig.add_trace(
            go.Scattergl(
                x=df.index,
                y=df[col],
                mode="lines",
                name=INDICATOR_LABELS.get(col, col),
                line=dict(color=sc["color"], dash=sc["dash"], width=style.line_width),
            ),
            row=2,
            col=1,
        )

    if style.show_missing_markers and missing_mask is not None and missing_mask.any():
        missing_times = df.index[missing_mask.reindex(df.index, fill_value=False)]
        if len(missing_times) > 0:
            fig.add_trace(
                go.Scattergl(
                    x=missing_times,
                    y=[0] * len(missing_times),
                    mode="markers",
                    name="欠測",
                    marker=dict(color="red", size=4, symbol="x"),
                ),
                row=1,
                col=1,
            )

    for hline in style.horizontal_lines:
        fig.add_hline(
            y=hline["y"],
            line_dash="dash",
            line_color=hline.get("color", "gray"),
            annotation_text=hline.get("label", ""),
            row=2,
            col=1,
        )
    for vline in style.vertical_lines:
        fig.add_vline(
            x=vline["x"],
            line_dash="dash",
            line_color=vline.get("color", "gray"),
            annotation_text=vline.get("label", ""),
        )

    _apply_common_layout(fig, style)

    return fig


def _apply_common_layout(fig: go.Figure, style: PlotStyle) -> None:
    fig.update_layout(
        width=style.width_px(),
        height=style.height_px(),
        title=dict(text=style.title, font=dict(size=style.font_size + 4, color=style.font_color)),
        font=dict(family=style.font_family, size=style.font_size, color=style.font_color),
        legend=dict(
            font=dict(size=style.legend_size, color=style.font_color),
            bgcolor=style.background_color,
            bordercolor="#cccccc",
            borderwidth=1,
        ),
        plot_bgcolor=style.background_color,
        paper_bgcolor=style.background_color,
        margin=dict(
            t=style.margin_top, b=style.margin_bottom, l=style.margin_left, r=style.margin_right
        ),
        barmode="overlay",
        hovermode="x unified",
        hoverlabel=dict(font=dict(color=style.font_color)),
    )
    # サブプロットの見出し（make_subplotsが自動追加する注釈）にも明示的に文字色を適用する
    fig.update_annotations(font=dict(color=style.font_color))

    if style.subtitle:
        fig.add_annotation(
            text=style.subtitle,
            xref="paper",
            yref="paper",
            x=0.5,
            y=1.06,
            showarrow=False,
            font=dict(size=style.font_size, color=style.font_color),
        )
    if style.note:
        fig.add_annotation(
            text=style.note,
            xref="paper",
            yref="paper",
            x=0.0,
            y=-0.18,
            showarrow=False,
            font=dict(size=max(style.font_size - 2, 8), color=style.font_color),
            align="left",
        )
    fig.update_xaxes(
        showgrid=style.show_grid,
        gridcolor="#e0e0e0",
        minor=dict(showgrid=style.show_minor_grid),
        tickfont=dict(size=style.tick_size, color=style.font_color),
        title_font=dict(size=style.axis_label_size, color=style.font_color),
        showline=style.show_frame,
        linecolor=style.font_color,
        mirror=style.show_frame,
        ticks="outside",
        showspikes=style.show_crosshair,
        spikemode="across",
        spikesnap="cursor",
        spikedash="dot",
        spikethickness=1,
        spikecolor="#666666",
        # Plotlyの既定日付フォーマッタは英語月名("Jan 2026"等)を使うため、
        # ズーム段階ごとに日本語の数値月表記へ明示的に置き換える。
        tickformatstops=[
            dict(dtickrange=[None, 1000 * 60 * 60 * 24], value="%H:%M"),
            dict(dtickrange=[1000 * 60 * 60 * 24, 1000 * 60 * 60 * 24 * 30], value="%-m/%-d"),
            dict(dtickrange=[1000 * 60 * 60 * 24 * 30, 1000 * 60 * 60 * 24 * 366], value="%Y年%-m月"),
            dict(dtickrange=[1000 * 60 * 60 * 24 * 366, None], value="%Y年"),
        ],
    )
    fig.update_yaxes(
        showgrid=style.show_grid,
        gridcolor="#e0e0e0",
        minor=dict(showgrid=style.show_minor_grid),
        tickfont=dict(size=style.tick_size, color=style.font_color),
        title_font=dict(size=style.axis_label_size, color=style.font_color),
        showline=style.show_frame,
        linecolor=style.font_color,
        mirror=style.show_frame,
        ticks="outside",
        showspikes=style.show_crosshair,
        spikemode="across",
        spikesnap="cursor",
        spikedash="dot",
        spikethickness=1,
        spikecolor="#666666",
    )
    if style.x_range:
        fig.update_xaxes(range=list(style.x_range))
    if style.y_range:
        fig.update_yaxes(range=list(style.y_range), row=2, col=1)
