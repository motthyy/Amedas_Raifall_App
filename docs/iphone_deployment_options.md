# iPhone対応 実行手順書（Plan A / Plan B 比較）

本書は、「このアプリをiPhoneで動かすには何を修正すべきか」を、2つの方針それぞれについて
具体的な作業手順として整理したものである。どちらを採るかの意思決定はまだ行っておらず、
比較検討のための手順書として作成した。

背景・技術的経緯は `docs/language_migration_plan.md` を参照。同ドキュメントで既に
「Streamlit Community Cloud等でのホスティングは不採用」と結論づけているため、
本書のPlan Bはその結論と矛盾する代替案として、あえて比較用に手順化している。

---

## Plan A: 既存移行計画（Rust/WASM + SvelteKit）のPhase 2以降を継続

`docs/language_migration_plan.md` で既にPhase 0（ゴールデンマスタ化）とPhase 1
（Rustコア実装・ネイティブビルド検証・145倍高速化の実測）が完了済み。以下はその続き。

### 現在地点

- [x] Phase 0: `tests/fixtures/golden/*.json` に正解データを保存済み
- [x] Phase 1: `core/` にRustクレート実装、`cargo test`でユニット16件・ゴールデン突合9件が成功
- [ ] Phase 2: WASM化 + SvelteKit最小UIでの実機検証 ← **次にやること**
- [ ] Phase 3: 全7画面移植 + Cloudflare Workersプロキシ実装
- [ ] Phase 4: CI/CD（GitHub Pages + Cloudflare Workers自動デプロイ）
- [ ] Phase 5（任意）: Python資産の整理

### Phase 2 でやるべき作業（詳細手順）

1. **wasm32ターゲットの追加**
   ```powershell
   rustup target add wasm32-unknown-unknown
   cargo install wasm-pack
   ```
2. **`core/Cargo.toml` にwasm-bindgen依存を追加**し、`crate-type`に`cdylib`が既にあることを確認（済み）。
3. **`core/src/lib.rs` に `#[wasm_bindgen]` エクスポート関数を追加**
   - まずは`soil_tank`（最重要）と`gumbel`だけをエクスポートし、最小構成で動作検証するのが低リスク。
   - 例: `run_tank_model_10min` を`Vec<f64>`入出力のFFI境界に整える。
4. **ビルド確認**
   ```powershell
   cd core
   wasm-pack build --target web
   ```
   生成される`core/pkg/`のJS/TS型定義・`.wasm`バイナリを`web/`側から読み込む。
5. **`web/` にSvelteKitプロジェクトを新規作成**
   ```powershell
   npm create svelte@latest web
   ```
   - adapter-staticを選択（GitHub Pages向け静的書き出し）。
6. **最小UIを実装**（この段階ではCSV/Parquet読み込み→タンクモデル計算→Plotly.jsグラフ表示のみ）
   - `visualization/styles.py` の配色定義をTS側に手動移植（数値をそのままコピー）。
7. **ローカル開発サーバーでの動作確認**
   ```powershell
   cd web
   npm run dev
   ```
8. **実機検証（最重要ゴール）**
   - `npm run build` の静的出力を一時的にホスティング（Vercel/Netlifyの無料枠、またはngrok等でローカルを一時公開）し、iPhoneのSafariで開く。
   - 確認項目: WASM読み込み速度、OPFS動作（iOS 16.4以降）、Plotlyグラフのタッチ操作、画面回転時のレイアウト崩れ。
9. **Phase 2完了の判定基準**: 実機でタンクモデル計算→グラフ表示まで一通り動き、体感速度がPython版（旧: 数十秒）より明確に速いこと。

### Phase 3以降の要点（既存計画書 7節の再掲）

- 残り6画面（データ品質・時系列・年最大値・確率雨量・出力・マニュアル）をSvelteKitへ移植。
- `proxy/`にCloudflare Workersを新規作成し、`jma/direct_client.py`のロジック（セッションCookie確立→POST→CSVパース、3秒待機・指数バックオフ・自動分割）をTypeScriptへ1:1移植。
- ダウンロードジョブ管理をIndexedDBベースに置き換え。
- 画像出力はPlotly.jsの`toImage()`、Excel出力は`SheetJS`で代替（書式再現性は要検証、既存計画書9節のリスク表を参照）。

### この方針の工数目安

既存計画書の見積り: Phase 2（3〜5日）+ Phase 3（2〜4週間）+ Phase 4（2〜3日）。
Phase 0/1の資産（ゴールデンマスタ・Rustコア）は無駄にならない。

---

## Plan B: 現行Streamlitアプリをサーバーホスティングし、iPhone SafariからPWA的に使う

こちらは新規に検討した代替案。既存計画書が指摘した「重さの根本原因が残る」「重量級依存が残る」
という弱点は解消されない前提で、**とにかく最短でiPhoneから開けるようにする**ことを目的とする。

### 前提として把握しておくべき制約

- 現行`src/amedas_rainfall/`は**Rustコアを一切呼んでいない**（`core/`はまだ独立した未統合の実験）。
  そのままホスティングしても、タンクモデル計算は旧来の純Pythonループ（約52秒/地点）のまま。
- `config/default.yaml`のパスは全て相対パス・`PROJECT_ROOT`基準（`config.py`）なので、
  コンテナ化自体に大きな支障はない。
- `data/`, `state/`, `output/`, `logs/`配下は`.gitignore`済みのローカル生成物。サーバー上でも
  永続化するにはボリュームマウントが必須（再起動で消えると使い物にならない）。
- 現状、認証機構が一切ない。インターネットに公開すると誰でも操作でき、気象庁サイトへの
  ダウンロードリクエストを第三者に発生させられてしまうため、**アクセス制御の追加が必須**。
- `requirements.txt`に`playwright`（Chromiumバイナリ同梱、数百MB）・`kaleido`（内部にヘッドレスChrome）
  があり、Linuxサーバーでは`playwright install --with-deps chromium`等の追加セットアップが要る。
- `fonts.preferred_japanese_fonts`に`Yu Gothic`, `Meiryo`（Windows専用フォント）が指定されている。
  Linuxコンテナにはこれらが存在しないため、フォールバックの`Noto Sans CJK JP`をOS側に
  インストールしないと、グラフの日本語ラベルが文字化け（豆腐）表示になる。

### 手順

#### 1. コード側の修正

1. **アクセス制御を追加**
   - 最短: リバースプロキシ（Caddy/nginx）でBasic認証をかける。アプリ本体のコード変更は不要。
   - もう少し丁寧にやるなら`streamlit-authenticator`等を導入し、`app.py`にログイン画面を追加。
   - どちらを選ぶかは運用スタイル次第（個人利用ならBasic認証で十分なことが多い）。
2. **フォント設定の見直し**
   - `config/default.yaml`の`fonts.preferred_japanese_fonts`はそのままでよい（先頭の
     Windows専用フォントが無ければ自動的にリストの後方にフォールバックする実装かを
     `visualization/styles.py`側で確認 — フォールバックしない実装なら明示的にLinux側の
     フォント名を先頭に追加する修正が必要）。
3. **ログ/データディレクトリのパス確認**
   - `_setup_logging`や`config.resolved_path`はそのままでよいが、Dockerで動かす場合は
     `PROJECT_ROOT`配下の`data/`, `state/`, `output/`, `logs/`を**外部ボリュームにマウント**
     できるよう`docker-compose.yml`側で対応する（アプリコード変更は不要）。
4. **Playwrightフォールバックの扱いを決める**
   - `config/default.yaml`の`download.mode: direct` / `fallback: playwright`はそのまま使うか、
     イメージサイズ削減のため一旦`playwright`を無効化するか判断する（直接ダウンロードで
     ほぼ間に合っているなら、フォールバックを切ってイメージを軽量化する選択肢もある）。
5. **依存関係の再検証**
   - `requirements.txt`はOS非依存なのでそのまま使えるが、`playwright install`はDockerfile内で
     別途実行する必要がある（pipインストールだけではChromium本体は入らない）。

#### 2. ホスティング先の選定

| 選択肢 | 概要 | 評価 |
|---|---|---|
| Streamlit Community Cloud | GitHub連携で無料デプロイ | 永続ストレージがないため`state/jobs.sqlite`等が再起動で消える。Playwrightの動作可否も不確実。個人の継続利用には不向き。 |
| 自宅サーバー/VPS + Docker（推奨） | さくらのVPS・Oracle Cloud Free Tier等 + Docker | 永続ボリューム・Playwright・アクセス制御を全て自分でコントロールできる。月額数百〜千円程度（無料枠のみで運用可能な場合もあり）。 |
| 自宅PC + ポート開放/Tailscale等 | 今のPCをサーバー化 | コスト最小だが、PCを常時起動しておく必要があり、ルーター設定や動的IP対応が別途要る。 |

推奨: **VPS + Docker**（永続化・セキュリティの制御がしやすいため）。

#### 3. Dockerイメージの作成

1. `Dockerfile`を新規作成（リポジトリに未存在のため要新規作成）:
   - ベースイメージ: `python:3.12-slim`
   - `apt-get install`で`fonts-noto-cjk`（日本語フォント）を追加
   - `pip install -r requirements.txt` → `playwright install --with-deps chromium`
   - `EXPOSE 8501`、`CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0"]`
2. `docker-compose.yml`を新規作成し、`data/`, `state/`, `output/`, `logs/`をホスト側ディレクトリに
   バインドマウント（コンテナ再作成でもデータが消えないようにする）。

#### 4. リバースプロキシ・HTTPS・認証の設定

1. VPS上にCaddy（自動HTTPS対応で設定が簡単）を導入し、ドメイン（自分の管理下のもの）を
   Caddyfileに設定。Let's Encrypt証明書を自動取得。
2. Caddyfileに`basicauth`ディレクティブでアクセス制御を追加（ID/パスワードは環境変数か
   別ファイルで管理し、リポジトリにコミットしない）。
3. iOS Safariで「ホーム画面に追加」を安定動作させるにはHTTPS必須（自己署名証明書は
   信頼されずアプリ的に使えないため、Let's Encrypt等の正規証明書が必要）。

#### 5. デプロイ・起動

```powershell
# VPS上で（SSH接続後）
docker compose up -d --build
```

- 動作確認: VPSのグローバルIP/ドメインにブラウザでアクセスし、Basic認証→アプリ画面表示を確認。
- ログ監視: `docker compose logs -f`で起動エラー（フォント欠如・Playwrightセットアップ漏れ等）を確認。

#### 6. iPhone側の設定

1. Safariで対象URLを開く（Basic認証のID/パスワードを入力）。
2. 共有ボタン→「ホーム画面に追加」でアイコン化（ネイティブアプリではないが起動体験は近くなる）。
3. Streamlitのモバイル表示崩れがないか、実機で一通り操作して確認（特に`st.tabs`/`st.radio`の
   横並びUIは画面幅次第で崩れやすいため、必要なら`app.py`側のCSS調整が追加で発生する可能性あり）。

#### 7. 運用上の注意（既存README/docsの制約を継続適用）

- 気象庁サイトへの配慮（3秒待機・直列リクエスト・指数バックオフ）は`config/default.yaml`の
  設定のまま維持すること。サーバー化しても複数人が同時にダウンロードを叩けば実質的に
  リクエスト頻度が上がってしまうため、アクセス制御（手順4）は性能面だけでなくこの観点でも必須。
- 証明書の自動更新（Caddyが自動でやるが、VPS自体の死活監視は別途必要）。
- `state/jobs.sqlite`・`data/normalized/*.parquet`のバックアップ運用を決めておく（VPSが壊れると
  蓄積データが消える）。

### この方針の工数目安

- Dockerfile/compose作成・VPSセットアップ・Caddy設定: 半日〜1日
- 動作検証・フォント/Playwrightの微調整: 半日〜1日
- 合計: 早ければ1〜2日で「iPhone Safariから開ける」状態に到達可能。
- ただし「重い」問題（タンクモデル約52秒）はそのまま残る点に注意。

---

## 比較まとめ

| 観点 | Plan A（Rust/WASM+SvelteKit） | Plan B（Streamlitホスティング） |
|---|---|---|
| 着手までの工数 | 大（残りPhase 2〜4で数週間） | 小（1〜2日） |
| 「重い」問題の解決 | 解決する（145倍高速化済み） | 解決しない（Python純ループのまま） |
| 保守対象 | Rust + TypeScript（新規学習コスト） | 既存Python資産をほぼそのまま流用 |
| ランニングコスト | ほぼ無料（GitHub Pages + Cloudflare Workers無料枠） | VPS費用が継続発生（無料枠次第で0円も可） |
| セキュリティ | クライアント完結、サーバー側の認証設計が不要 | 常時稼働サーバーの認証・証明書・監視が必要 |
| 既存投資の活用 | Phase 0/1の資産をそのまま活用 | 既存Rust移行の投資（Phase 0/1）は活用しない |
