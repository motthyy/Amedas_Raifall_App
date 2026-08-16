# 開発言語移行 調査・行程書

本書は、現行のPython/Streamlit実装が抱える性能上の限界と、「GitHub公開によりiPhoneからも操作できるようにしたい」という要件を踏まえ、開発言語・アーキテクチャを再検討した結果をまとめたものである。既存機能（README.md・docs/specification.md・docs/calculation_method.md・docs/jma_download.md に記載の全機能）を一切削らずに移行することを前提とする。

調査日: 2026-07-21。

---

## 進捗ログ

- **2026-08-16: 現行Pythonアプリの実用性能を先行改善。** 通常パイプラインを、巨大な
  10分値DataFrameを作らない時別計算へ変更し、同じ数式ループをNumba JITで実行するようにした。
  欠測を含む180日入力で従来参照実装との最大絶対差0.0、コンパイル後の50年分計算0.086秒を確認。
  Rust/WASM計画はiPhone・静的Web配信のための別フェーズとして残るが、Windows版の日常操作に
  おける土壌雨量計算待ちはこの改修で解消した。

- **2026-07-21: Phase 0完了。** `tests/fixtures/golden/generate.py` により、既存Pythonの
  主要関数（閾値処理・連続雨量・移動雨量・実効雨量・タンクモデル・年最大値・ガンベル分布・
  ブートストラップ）の入出力を `tests/fixtures/golden/*.json` へ「正解データ」として書き出し済み。
- **2026-07-21: Phase 1のコード実装が完了（ビルド未検証）。** `core/` にRustクレート
  （`amedas-core`）を作成し、`processing/`（一部）・`indices/`・`statistics/` を移植した。
  詳細は `core/README.md` を参照。
  - **既知の課題**: このマシンにネイティブ向けリンカ（Visual Studio Build Toolsの
    `link.exe`）が入っておらず、管理者権限が必要なため非対話セッションから自動導入
    できなかった。そのため `cargo test` によるゴールデンマスタとの数値突合は**まだ
    実行できていない**。ユーザーの判断で、ビルド確認は後回しにして実装を先に進めている。
    次回作業時は、まず `core/README.md` の手順でビルドツールを導入し、`cargo test` を
    実行してコードの正しさを確認することを最優先で行うこと。
  - Rust/cargo自体（1.97.1）とNode.js（LTS 24.18.0）はこのマシンに導入済み。
- **2026-07-21: Visual Studio Build Tools導入完了、ビルド検証完了。** `cargo test`で
  ユニットテスト16件・ゴールデンフィクスチャ突合テスト9件が全て成功。タンクモデル
  （`soil_tank_matches_python`、欠測区間を含む4320ステップの中規模系列）を含め、
  Rust版の出力がPython版と数値一致することを確認した。
  - **ベンチマーク結果**（`core/examples/benchmark.rs` / `benchmark_python.py`、
    同一条件: 50年分・10分刻み・262.8万ステップ）:
    Rustネイティブ = **0.070秒**、Python = **10.138秒**（約145倍高速化）。
    「重い」の主因だったタンクモデル計算のボトルネックは、Rust移植により実質的に解消した。
  - 次はPhase 2（`wasm-bindgen`によるWASM化、SvelteKit最小UIでの実機検証）に進む。

---

## 0. 結論（要約）

- 「重い」の主因は2つ。
  1. 推定土壌雨量指数の3段タンクモデル（`src/amedas_rainfall/indices/soil_tank.py` の `run_tank_model_10min`）が、数百万ステップ（数十年分×10分刻み）を**純Pythonのforループで逐次計算**している。実機で約52秒（キャッシュ導入後も初回・データ更新のたびに発生）。データが増えるほど線形に悪化する。
  2. Streamlitの「操作のたびにスクリプト全体を再実行する」アーキテクチャそのものが、大規模データ・多数のUIウィジェットと相性が悪い（既にトラブルシューティングに記載の多重プロセス問題もこれに起因）。
- 「GitHub公開でiPhoneからも操作できる」という要件は、事実上「ブラウザで完結するWebアプリにする」ことを意味する。GitHub Pagesは静的ホスティングのみで、Pythonインタプリタもサーバーサイド処理も動かせない。Pyodide（ブラウザ上でCPythonを動かす技術）も検討したが、これは**タンクモデルの逐次ループを「インタプリタのままブラウザで動かす」だけ**なので、重さの根本原因（1）を全く解決しない上、WASM版CPython自体の初回ダウンロードが10〜30MBあり、モバイル回線ではむしろ悪化する。よって不採用。
- 結論として、「配信層をWeb化する」作業はどのみち避けられない。であれば同じタイミングで**計算コアをコンパイル言語に移し、WebAssembly化する**のが最も投資対効果が高い。1回の書き換えで「重い」と「iPhoneで開けない」の両方を同時に解決できる。
- 推奨スタック:
  - **計算コア**: Rust（`wasm-bindgen`でWASM化。ネイティブバイナリ／PyO3経由のPython拡張としても再利用可能）
  - **UI**: TypeScript + SvelteKit（静的書き出し、GitHub Pagesにホスティング）＋ Plotly.js（現行のPlotly配色・レイアウト設計をほぼそのまま移植可能）
  - **気象庁データ取得プロキシ**: TypeScript（Cloudflare Workers、無料枠で常時稼働）
  - **Python**: 新アーキテクチャの実行時には不要になる。移行期の数値突合・検証用としてのみ一時的に残す。

---

## 1. 現状分析（なぜ重いのか）

| 要因 | 該当箇所 | 内容 |
|---|---|---|
| 逐次ループの計算コスト | `indices/soil_tank.py` `run_tank_model_10min` | 50年分で約262万ステップ。各ステップがPythonバイトコード解釈を伴うため、C実装の数十〜100倍以上遅い。ベクトル化不可（各ステップが前ステップの貯留量に依存する状態遷移のため）。実測で約52秒／地点。 |
| Streamlitの全体再実行モデル | `app.py`, `ui/*.py` | ウィジェット操作のたびにスクリプト全体が再実行される設計。大きなDataFrameを持つほど体感が重くなる。 |
| 重量級の依存関係 | `requirements.txt` | Playwright（Chromiumバイナリ同梱、数百MB）、Kaleido（画像出力用に内部でヘッドレスChromeを保持）、pandas/numpy/scipy/pyarrowの大型バイナリwheel。ローカルWindows PCへのインストール自体は`install.bat`で吸収しているが、この構成のままではiPhoneで「リンクを開くだけ」という体験は原理的に不可能。 |
| ジョブ管理・状態のファイルI/O | `state/jobs.sqlite`, `storage/*.py` | ローカルファイルシステム前提の設計で、ブラウザ環境（サンドボックス）とは根本的に相性が悪い。 |

---

## 2. なぜ「言語を変える」ことがiPhone対応に直結するのか

- GitHub Pagesは静的ファイル（HTML/CSS/JS/WASM/画像）のみ配信可能。Pythonサーバーは動かない。
- Streamlit Community Cloud等の「PythonごとWeb化する」サービスも存在するが、（a）常時起動サーバーが必要でGitHub単体では完結しない、（b）Streamlitの重さそのもの（上記1.）は一切解決しない、（c）モバイルSafariでの操作性もStreamlit側の制約を引き継ぐ。よって要件（軽量化・iPhone対応）のどちらも満たさない。
- Pyodide（ブラウザ内CPython）も、タンクモデルのループが「インタプリタのままブラウザに移る」だけであり、むしろ初回ロードが重くなる。不採用。
- 結論: ブラウザネイティブで高速に動く形（JavaScript、またはWebAssemblyにコンパイルされた言語）でなければ、根本解決にならない。

---

## 3. 計算コア言語の比較

| 言語 | 実行速度 | WASM成熟度 | 学習コスト（Python経験者視点） | 科学計算/統計ライブラリ | 備考 |
|---|---|---|---|---|---|
| **Rust**（推奨） | 非常に高速（ネイティブの1.1〜1.5倍程度でWASM実行） | 非常に高い（`wasm-bindgen`/`wasm-pack`が事実上の標準） | 中〜高（所有権モデルの学習が必要） | `statrs`（Gumbel分布等）、`polars`（pandas相当）、`ndarray`、`csv` | メモリ安全性が高く、長期保守に強い。PyO3で「移行期だけPythonから呼ぶ」ことも可能で段階移行と相性が良い。 |
| Go（TinyGo経由） | 高速だが標準GoのWASM出力は大きい（TinyGoで軽量化要） | 中（TinyGoは標準ライブラリの一部が非対応） | 低（文法がシンプル） | `gonum`はあるがRustのエコシステムほど厚くない | 学習は楽だが、TinyGoの制約に当たりやすく統計処理では手詰まりの可能性。 |
| C++（Emscripten） | 最速級 | 高い（実績豊富） | 高（メモリ管理・ビルド設定が複雑） | Eigen等豊富 | 性能は最良だが、単独開発・長期保守の負荷が大きく今回の規模には過剰。 |
| AssemblyScript | 中程度（TSライクだが最適化に制約） | 中（WASM専用言語で汎用性は低い） | 低（TS経験があれば読みやすい） | 薄い（自作が多くなる） | 統計・タンクモデルのような数値計算では力不足になりやすい。 |
| Kotlin/Wasm | 発展途上 | 低〜中（2026年時点でも実験的機能が残る） | 中 | 薄い | 「将来ネイティブiOSアプリ化」の夢はあるが、今回の主目的（軽量化・Web公開）には成熟度が不足。 |

**結論**: Rustを計算コアに採用する。理由は (1) WASM化の実績・ツールチェーンが最も枯れている、(2) 統計・データフレーム系ライブラリが揃っている、(3) 移行期にPyO3でPythonから直接呼び出せるため「一気に全部書き換え」ではなく「モジュール単位で検証しながら置き換え」が可能、(4) メモリ安全性によりC++より事故が少ない。

---

## 4. UI／配信層の言語・フレームワーク比較

| 選択肢 | GitHub Pages適性 | モバイル体感 | 現行Plotly資産の再利用 | 備考 |
|---|---|---|---|---|
| **TypeScript + SvelteKit（推奨）** | ◎（静的書き出し`adapter-static`が公式サポート） | ◎（バンドルが小さくロードが速い） | ◎（Plotly.jsは公式JS版。`visualization/styles.py`の配色・レイアウト定義はほぼそのまま移植可能） | Streamlitの「宣言的に書ける」感覚に近く、かつ再描画範囲を細かく制御できるため「重い」の再発を防げる。 |
| TypeScript + React (Vite) | ◎ | ○（Svelteよりバンドルはやや大きい） | ◎ | エコシステムは最大。チーム開発ならこちらも十分あり。 |
| 素のTypeScript（フレームワークなし） | ◎ | ◎（最小） | ◎ | 依存が最小で軽量だが、画面数が多い（地点選択/ダウンロード/品質/時系列/確率雨量/出力/マニュアルの7画面）ため状態管理を自作するコストが増える。 |

**結論**: SvelteKitを採用。軽量さとGitHub Pages静的書き出しの親和性、Plotly.jsとの組み合わせ実績から最適。

---

## 5. 気象庁データ取得層の扱い（最重要の分岐点）

`docs/jma_download.md` の調査により、気象庁サイトは以下の理由でブラウザから直接fetchできないことが判明している。

- セッションCookie（`ci_session`, `AWSALB`）の維持が必要
- レスポンスにCORSヘッダー（`Access-Control-Allow-Origin`）が付与されない前提で設計されており、任意オリジンのブラウザJSからは読み取り不可
- CSVはCP932（Shift_JIS）バイト列で返る（※これ自体はブラウザの`TextDecoder("shift_jis")`で復号可能なので問題にならない）

このため、**ダウンロード処理だけはブラウザ単体では完結できず、何らかのサーバー（プロキシ）が必要**である。3つの選択肢を検討した。

| 案 | 概要 | 評価 |
|---|---|---|
| (a) ローカルPython継続 | ダウンロードだけは現行のPython（`direct_client.py`等）をWindows PC上で動かし続け、生成された`hourly.parquet`をアプリ（新UI）に読み込ませる | 実装コストは最小。ただし「iPhoneから完結して新規地点をダウンロードする」ことはできない（PCでの事前実行が必要）。 |
| (b) TypeScript化してエッジにデプロイ（推奨） | `direct_client.py`のロジック（セッション確立→POST→CSVパース）をTypeScriptに移植し、Cloudflare Workers等の無料枠にデプロイ。ブラウザ（iPhone含む）から直接このプロキシを叩く | 気象庁への配慮（3秒待機・直列・指数バックオフ）をプロキシ側で一元管理できる。GitHub Actionsで自動デプロイでき「GitHubで公開」の要件にも合致。 |
| (c) GitHub Actionsで定期取得しリポジトリにコミット | 定期実行でデータを取得し静的JSON/Parquetとしてリポジトリに蓄積、アプリは読むだけ | 特定地点を継続監視する用途には良いが、「任意の地点をユーザーが選んでその場でダウンロードする」という現行のインタラクティブな使い方に合わない。 |

**結論**: (b) を採用し、`direct_client.py`のロジックをTypeScript（Node/Cloudflare Workers）へ1:1移植する。Playwright予備手段（`playwright_client.py`）はヘッドレスブラウザが必要なため、Cloudflare Workersでは動かせない。当面は「direct方式のみ」で運用し、予備手段が必要になった場合のみ別途Node+Playwrightの小さなサービス（Render/Fly.io無料枠等）を追加する方針とする（現行READMEでも「実行時検証はしていません」と明記されている手段であり、優先度は低い）。

---

## 6. 推奨アーキテクチャ全体像

```
repo/
├── core/                      … Rust クレート（計算コア）
│   ├── src/
│   │   ├── csv_parser.rs      ← jma/csv_parser.py 相当
│   │   ├── quality.rs         ← processing/quality.py
│   │   ├── merging.rs         ← processing/merging.py
│   │   ├── continuous_rainfall.rs
│   │   ├── rolling_rainfall.rs
│   │   ├── effective_rainfall.rs
│   │   ├── soil_tank.rs       ← 最重要（速度向上の主目的）
│   │   ├── annual_maxima.rs
│   │   ├── gumbel.rs
│   │   ├── bootstrap.rs
│   │   └── lib.rs             ← wasm-bindgen エクスポート
│   └── Cargo.toml
├── web/                        … SvelteKit（static adapter）
│   ├── src/routes/             ← station / download / quality / timeseries / probability / export / manual の7画面
│   ├── src/lib/plotly-styles.ts ← visualization/styles.py 相当
│   └── static/
├── proxy/                      … Cloudflare Workers（TypeScript）
│   └── src/jma-client.ts      ← jma/direct_client.py 相当
├── config/                      … 既存の tank_model.yaml / default.yaml を流用（形式はそのまま、読み込み側だけ移植）
├── docs/                        … 既存ドキュメントを一次情報源として維持
└── .github/workflows/
    ├── deploy-pages.yml        ← web/ をビルドしGitHub Pagesへ
    └── deploy-proxy.yml        ← proxy/ をCloudflare Workersへ
```

補足設計判断:

- **データ永続化**: `state/jobs.sqlite` や `data/normalized/*.parquet` に相当する状態は、ブラウザの **OPFS (Origin Private File System)** または **IndexedDB** に保存する。iOS Safari 16.4以降でOPFS対応済み。これにより「サーバーにDBを置かない」完全クライアントサイド構成が可能。
- **画像出力**: Kaleido（サーバーサイドヘッドレスChrome）の代替として、`Plotly.js`標準の`toImage()`/`downloadImage()`（Canvasベース、PNG/SVG）を使用。300/600/1200dpi相当は`scale`倍率で近似する（要検証、9節参照）。PDF出力は`jsPDF`等で代替。
- **Excel出力**: `openpyxl`/`xlsxwriter`の代替として`SheetJS (xlsx)`を使用。複数シート構成は再現可能だが、詳細な書式（罫線・条件付き書式等）は現行実装との差分を検証する必要がある。

---

## 7. 段階的移行行程（フェーズ）

既存機能を落とさないことを最優先し、**各フェーズ終了時に現行Python版と数値・表示を突き合わせて検証**しながら進める。

### Phase 0: ゴールデンマスタ化（1〜2日）
- `tests/*.py` の入出力ケースをJSON/CSVフィクスチャとして書き出す（`tests/fixtures/`を拡張）。
- 主要地点（例: README推奨の「豊田」「小河内 a0365」）で数年分の`hourly.parquet`→計算済み`indices.parquet`のペアを「正解データ」として保存。
- 成果物: 移行後の言語で同じ入力を計算した際に、この正解データと突合できる状態。

### Phase 1: Rust計算コア実装（ネイティブ、1〜2週間）
- `core/`クレートに、`processing/`・`indices/`・`statistics/`を移植。
- `docs/calculation_method.md`の数式をそのまま実装のドキュメンテーションコメントに転記。
- まずはネイティブCLIとして動かし、Phase 0のゴールデンマスタと許容誤差（例: 相対誤差1e-9）で一致することをRust側のテストで確認。
- タンクモデルの実測ベンチマークを取り、Python版（約52秒）比でどれだけ短縮できたかを記録する。

### Phase 2: WASM化と最小UI検証（3〜5日）
- `wasm-bindgen`でPhase 1のクレートをWASM化。
- SvelteKitで最小限のUI（Parquet/CSVを読み込み→指標計算→Plotlyグラフ表示のみ）を作り、実際にiPhone Safariで開いて速度・OPFS動作を実機検証する。ここで問題が出れば早期に方針修正できる。

### Phase 3: 全画面移植（2〜4週間）
- 地点選択・ダウンロード・データ品質・時系列グラフ・確率雨量グラフ・出力・マニュアルの7画面をSvelteKitへ移植。
- `proxy/`（Cloudflare Workers）にJMA直接クライアントを実装し、3秒待機・指数バックオフ・分割再試行ロジックを移植。
- ダウンロードジョブ管理をIndexedDBベースに置き換え（中断・再開の要件を維持）。

### Phase 4: CI/CD・公開（2〜3日）
- GitHub Actionsで`web/`のビルド＆GitHub Pagesデプロイ、`proxy/`のCloudflare Workersデプロイを自動化。
- README/マニュアルタブを新構成に合わせて更新。

### Phase 5（任意）: Python資産の整理
- 全機能の並行稼働・突合が完了した時点で、Pythonを完全引退させるか、内部検証・バッチ処理専用CLIとして残すかを判断する。

---

## 8. 既存資産の再利用方針

- `config/tank_model.yaml` / `config/default.yaml`: 中身は変更せず、読み込み側（YAMLパーサ）だけをRust/TSに用意すればそのまま使える。
- `tests/*.py` の期待値: Phase 0でゴールデンマスタ化し、Rust/TS双方のテストから参照する「唯一の正解」として扱う。
- `docs/calculation_method.md` / `docs/jma_download.md`: 数式・気象庁サイト仕様の一次情報源として、そのまま移行後も参照し続ける（変更不要）。
- README.mdの利用上の注意（19節）・推定値である旨の表記（11節）・確率年1年が算出不可である理由（13節）などの**文言レベルの制約**は、新UIでも一言一句の意味を変えずに引き継ぐこと。

---

## 9. リスク・要検証事項

| リスク | 内容 | 対応方針 |
|---|---|---|
| iOS Safariの対応状況 | OPFS/WASMはSafari 16.4以降で概ね対応。ユーザーのiPhoneのiOSバージョンを事前確認する | Phase 2で実機検証を必須とする |
| 画像出力のDPI再現性 | Plotly.js `toImage()`はCanvasベースで、Kaleidoのような明示的DPI指定とは概念が異なる | `scale`倍率での近似仕様を決め、300/600/1200dpi相当の見た目をPhase 3で目視比較 |
| Excel出力の書式再現性 | SheetJSがopenpyxl/xlsxwriter同等の書式（罫線・複数シート等）をどこまで再現できるか未検証 | Phase 3で現行出力ファイルと突合 |
| 気象庁サイトへの配慮 | 3秒待機・直列リクエスト・指数バックオフの方針は言語を変えても維持が必須（利用規約上の要請） | `proxy/`側で必ず同等ロジックを実装し、README19節の記載も維持 |
| CSVフォーマット変更 | 気象庁サイトの仕様変更リスクはPythonでも明記済み（README18節） | `docs/jma_download.md`をそのまま参照し、変更検知時は`proxy/src/jma-client.ts`を見直す |

---

## 10. 一括実装用プロンプト

以下は、コーディングエージェント（Claude Code等）にこの移行を実行させる際にそのまま渡せるプロンプトである。実際には規模が大きいため、エージェント側がフェーズごとにコミットを分けながら段階的に進めることを想定している（「一括」とは「一つの指示として全体像を渡す」という意味であり、実行自体は複数セッションに分かれる想定）。

```
あなたは、Windows上で動くPython/Streamlit製のアプリ「アメダス長期雨量・鉄道防災指標解析アプリ」
（リポジトリ直下の README.md, docs/specification.md, docs/calculation_method.md,
docs/jma_download.md を一次仕様書とする）を、以下の新アーキテクチャへ移行するタスクを担当する。

## 絶対条件
1. 既存の全機能を1つも失わないこと。README.md の1〜21節に記載された機能・文言・警告・
   注意書き（特に11節「気象庁公表値ではない」、13節「確率年1年は算出不可」、19節「気象庁サイトへの
   配慮」）は、意味を変えずに新UIへ引き継ぐこと。
2. 数式・係数は docs/calculation_method.md を唯一の正とし、1文字も仕様を変えずに実装すること。
3. tests/ 配下の既存pytestケースを「正解データ」として使い、移行後の実装が同じ入力に対して
   同じ出力（許容誤差1e-9程度）を返すことを、移行先言語のテストとして必ず用意すること。
4. 既存のPythonコード（src/amedas_rainfall/）は削除せず、移行完了まで並行して残し、
   数値突合の基準として使い続けること。

## 目的
- 現行実装は、推定土壌雨量指数の計算（src/amedas_rainfall/indices/soil_tank.py の
  run_tank_model_10min）が数百万ステップの純Pythonループで約52秒かかる、Streamlitの
  全体再実行モデルにより操作が重い、という2つの性能課題を抱えている。
- 加えて、GitHubで公開しiPhoneからも操作できるようにしたいという要件があるが、
  GitHub Pagesは静的ホスティングのみでPythonは動かせない。
- そこで、計算コアをRustで書き直しWebAssembly化し、UIをTypeScript(SvelteKit)で
  ブラウザ完結型に作り直すことで、上記全ての課題を同時に解決する。

## 新アーキテクチャ
- core/ : Rustクレート。processing/(quality, merging, normalization),
  indices/(continuous_rainfall, rolling_rainfall, effective_rainfall, soil_tank),
  statistics/(annual_maxima, gumbel, bootstrap) を移植。wasm-bindgenでWASM化し、
  ネイティブcdylibとしても利用可能にする。
- web/ : SvelteKit（static adapter、GitHub Pagesへ静的デプロイ）。地点選択・ダウンロード・
  データ品質・時系列グラフ・確率雨量グラフ・出力・マニュアルの7画面を移植。
  グラフはPlotly.jsを使い、src/amedas_rainfall/visualization/styles.py の配色・レイアウト
  定義をそのまま移植する。状態永続化はOPFS/IndexedDBを用い、サーバー側DBを持たない。
  画像出力はPlotly.jsのtoImage()、Excel出力はSheetJSを使う。
- proxy/ : Cloudflare Workers（TypeScript）。src/amedas_rainfall/jma/direct_client.py の
  ロジック（セッションCookie確立→POST→CSVパース）を1:1移植する。docs/jma_download.md に
  記載の全パラメータ・ヘッダー・レスポンス仕様を厳密に踏襲すること。気象庁サイトへの配慮
  （既定3秒待機・直列リクエスト・失敗時6か月→3か月→1か月→7日の自動分割・指数バックオフ）
  も必ず実装すること。CSVはCP932でエンコードされているため、ブラウザ標準の
  TextDecoder("shift_jis") 等で復号すること。
- config/ : 既存の tank_model.yaml, default.yaml をそのまま流用し、読み込み処理のみ
  Rust/TS側に用意する。

## 進め方（フェーズごとにコミットを分けること）
1. Phase 0: tests/ の入出力をゴールデンマスタとしてJSON/CSVフィクスチャに書き出す。
2. Phase 1: core/ にRustで計算コアをネイティブ実装し、ゴールデンマスタと一致することを
   確認するテストを書く。タンクモデルの実行時間をベンチマークし、Python版との比較を記録する。
3. Phase 2: core/ をwasm-bindgenでWASM化し、web/ に最小限のUI（データ読み込み→
   指標計算→Plotlyグラフ表示のみ）を作る。この時点でモバイルSafariでの動作を確認する
   ための手順をREADMEに書いておく。
4. Phase 3: web/ の残り6画面を移植し、proxy/ を実装してダウンロード〜ジョブ管理まで
   一通り動くようにする。
5. Phase 4: .github/workflows/ にGitHub PagesへのデプロイとCloudflare Workersへの
   デプロイのCIを追加する。

## 受け入れ基準
- 既存READMEの「21. 最初に試す推奨手順」に相当する一連の操作（地点選択→ダウンロード→
  正規化→指標計算→年最大値→確率雨量）が、新UI上で最初から最後まで実行できること。
- Phase 0で作成したゴールデンマスタと、新実装の出力が許容誤差内で一致すること。
- README.mdに記載の全ての注意書き・免責事項が新UIにも引き継がれていること。
- 気象庁サイトへの配慮（待機時間・直列リクエスト・自動分割・バックオフ）が
  proxy/ 側で実装され、docs/jma_download.md の6節に記載の方針を満たしていること。

各フェーズの完了時に、何を実装し、Python版とどう突き合わせて検証したかを簡潔に報告すること。
```

---

## 参考: なぜ「Pythonのまま最適化」ではなく言語移行を選ぶのか

Numba/Cython/PyO3による部分的な高速化（タンクモデルのループだけをCコンパイルする等）で「重い」の一部は緩和できる。しかし、iPhone×GitHub公開の要件はPythonのままでは達成できないため、いずれにせよ配信層の書き換えが不可避である。同じ労力を払うなら、配信層の書き換えと同時に計算コアもコンパイル言語へ移す方が、1回の投資で両方の課題を解決でき合理的である、というのが本書の結論である。
