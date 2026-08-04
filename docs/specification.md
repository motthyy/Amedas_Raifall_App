# 仕様書（実装サマリ）

本書は、本アプリが実装している機能仕様の要約である。数式の詳細は
`docs/calculation_method.md`、気象庁データ取得方式の詳細は
`docs/jma_download.md` を参照。

## 対象範囲

- OS: Windows 10 / 11、Python 3.11
- UI: Streamlit（日本語のみ）、グラフ: Plotly（画像出力はPlotly+Kaleido）
- データ処理: pandas / NumPy、統計: SciPy
- 保存形式: Parquet（内部処理標準）、CSV、SQLite（ジョブ管理）、Excel（openpyxl/xlsxwriter）
- 通信: requests（直接HTTP）、Playwright（予備のブラウザ操作手段）
- 取得元は気象庁「過去の気象データ・ダウンロード」のみ。非公式サイトは使用しない。

## 主要機能と対応モジュール

| 機能 | モジュール |
|---|---|
| 地点マスタ取得・キャッシュ | `jma/station_catalog.py` |
| 直接HTTPダウンロード | `jma/direct_client.py` |
| ブラウザ操作予備手段 | `jma/playwright_client.py` |
| 開始日時探索 | `jma/start_date_finder.py` |
| CSVパース | `jma/csv_parser.py` |
| ダウンロードジョブ管理（分割・再試行・中断再開） | `jma/download_manager.py`, `storage/database.py`, `storage/repositories.py` |
| 正規化・重複統合 | `processing/normalization.py`, `processing/quality.py`, `processing/merging.py` |
| 雨量指標計算 | `indices/continuous_rainfall.py`, `indices/rolling_rainfall.py`, `indices/effective_rainfall.py` |
| 土壌雨量 | `indices/soil_tank.py` |
| 年最大値・完全性 | `statistics/annual_maxima.py` |
| ガンベル分布・確率雨量 | `statistics/gumbel.py`, `statistics/bootstrap.py` |
| 可視化・画像出力 | `visualization/timeseries.py`, `visualization/probability.py`, `visualization/styles.py`, `visualization/export.py` |
| データ出力（Excel/CSV/Parquet） | `reporting.py` |
| Streamlit画面 | `ui/station_page.py`, `ui/quality_page.py`, `ui/timeseries_page.py`, `ui/probability_page.py`, `ui/export_page.py`, `app.py` |
| 処理の橋渡し | `pipeline.py` |

## 地点マスタが保持する項目

都道府県、地点名、地点コード（`stid`）、ダウンロード要求用コード（`prid`/`stid`）、
地点種別（アメダス／気象官署／その他）、緯度・経度・標高、現在観測中か、
観測終了地点か、降水量観測の有無、地点情報取得日時。

キャッシュ先: `data/station_master/stations.parquet`。

## ダウンロード方式

1年単位を基本とし、失敗時は6か月→3か月→1か月→7日の順に自動分割する。
既に成功した期間は再取得しない。ジョブ状態（PENDING/DOWNLOADING/SUCCESS/
VALIDATED/RETRY_WAIT/SPLIT/FAILED）はSQLite（`state/jobs.sqlite`）で管理し、
アプリ再起動後も継続する。

既定の直接方式（`direct`）が使用できない場合の予備手段として、Playwright
によるブラウザコンテキスト経由のリクエスト（`playwright`）を用意している。

## CSV解析

CP932（Shift_JIS拡張）優先でデコードし、UTF-8へのフォールバックも備える。
複数行ヘッダー（地点名行・要素名行・副見出し行）を検出し、24時表記は
翌日0時へ正規化、品質情報・均質番号を保持し、欠測は0ではなくNaNとして扱う。

## 指標

時雨量、12時間無降雨リセット連続雨量、24時間移動雨量、実効雨量（半減期3h/6h/24h、
0.3mm未満で0に丸め）、土壌雨量（気象庁標準3段タンクモデル、10分雨量は時別値の
均等6分配による推定、0.3mm未満で0に丸め）。

## 年最大値・確率雨量

暦年／年度／6月始まり年の3種類で年最大値と年間データ完全率を計算し、
既定95%未満の年・不完全年・未終了年を除外対象とする（変更可）。
ガンベル分布（最尤法／積率法）により、1〜30年・50・100・200・500年の
確率雨量を算出し、Gringorten/Weibull/Cunnaneのプロッティングポジションと
ブートストラップ信頼区間、AIC/KS統計量/RMSE/相関係数による適合度評価を提供する。

## 可視化・出力

Streamlit上でPlotlyによるインタラクティブな時系列・確率雨量グラフを表示し、
図幅・DPI・フォント・線種・配色（白黒モード含む）等を調整の上、
PNG（300/600/1200dpiまたは任意）・SVG・PDFとして`output/figures/`へ出力できる。
グラフ設定は`output/plot_settings/`にJSONとして保存・再読込できる。
時別データ・年最大値・確率雨量はCSV/Parquet/Excel（複数シート構成）で出力できる。
