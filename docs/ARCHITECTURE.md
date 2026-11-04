# Technical overview / 技術構成

GitHubのREADMEは「何を作ったか」「試し方」「コードの入口」だけに絞っています。このページは、開発者と保守担当者向けの**実装上の要点**です。変更履歴はPR/コミット履歴を参照してください。

## 全体像

```text
content/articles.json ──┐
                         ├─ Python scripts/build.py ── dist/ (HTML / CSS / JS / RSS / OGP)
content/serials.json ────┘                │
                                         └─ Cloudflare Workers Static Assets
                                              └─ /api/* → src/worker.js
                                                         └─ SQLite Durable Objects
```

- 記事の本文、アーカイブ、連載、検索導線は静的生成。読者APIが停止しても本文は閲覧可能。
- 通常のビルドは外部AI生成APIを使いません。画像生成は独立した任意の処理です。
- `main`への更新をCloudflareのGit連携でデプロイ。GitHub Actionsではテストと公開後の読み取り検証を実施。
- 生成結果は`dist/`。手元で公開するだけならCloudflareアカウントは不要です。

## データと日時

- 架空ニュースは`content/articles.json`。実際の出典・公開日時と、5年後という演出上の`fiction_at`を混ぜません。
- 連載は`content/serials.json`。推理小説は`kind: fiction`、実在の市場データに基づく考察は`kind: analysis`。
- 市場分析は資料`sources`、仮説`scenarios`、検証可能な`forecast`を持ち、判定時には結果の根拠を残します。
- RSS、構造化データ、サイトマップでは架空の未来日時を実際の掲載日時として扱いません。
- 詳しい検証基準は[`docs/SERIAL_EDITING_GUIDE.md`](SERIAL_EDITING_GUIDE.md)と[`scripts/forecast_ledger.py`](../scripts/forecast_ledger.py)を参照。

## 画像

- 通常の表紙は[`scripts/editorial_covers.py`](../scripts/editorial_covers.py)で生成する記事別SVG。SNS共有用にはPNGを生成します。
- 写真は`content/approved_photos.json`の承認情報、ライセンス、形式などを検証してから採用。不採用の場合は自作表紙に戻します。
- ローカルのComfyUIによる生成は任意であり、Macが停止していても記事発行には影響しません。詳細は[`MAC_SCENE_SETUP.md`](MAC_SCENE_SETUP.md)。
- 承認済みJPEGから無料CPUとFFmpegで作る**任意の12秒・24fps・3カットMP4**は、検証済みのものだけ表示。旧4秒・12fpsクリップも互換性を維持し、1本450KiB・全体24MiBの上限を適用します。人物自体を動かすAI動画生成ではありません。
- 動画は編集者が選んだ記事だけが対象。トップは最大1本の画面内無音プレビュー、記事ページは手動再生とし、動きを減らす設定・データ節約設定・読者の手動停止を尊重します。記事一覧は静止画のままです。
- 字幕は`content/broadcasts.json`に記事slugと3つの短い文を定義し、ブラウザで`video.currentTime`へ同期。字幕だけの変更ではMP4を再生成せず、静的ビルドで反映します。動画の検証・停止・容量管理の詳細は[`MOTION_CLIPS.md`](MOTION_CLIPS.md)。
- GitHub公開ランナーでのCPU画像生成は実験的な経路です。**定時実行の有効・停止や画質検証は[`FREE_CPU_IMAGES.md`](FREE_CPU_IMAGES.md)を正本として確認**してください。設定済みというだけで画像の品質・本番反映を成功と見なしません。

## 読者向けAPIと測定

- `src/worker.js`にはコメント、投票、反応などのAPIがあります。Cloudflare Workers + SQLite Durable Objectsを使用。
- 静的な記事と、API依存の操作を分離。APIが利用できない時も閲覧は継続します。
- PVはJavaScriptが動作するページの実表示回数。同じページの再読み込み・別ページからの再訪は加算し、同一イベントの通信再送は`viewId`で重複排除します。非表示・事前読み込み中は最初に表示されるまで送信せず、タブを戻しただけでは加算しません。BFCacheからページ自体を再表示した場合は新しい表示として数えます。
- `/api/engagement`の`pageViews`がサイト全体、`articles[slug].pageViews`が記事別の累計PV。既存の`visitors`は匿名ブラウザ識別子の数、`articleReads`は1日あたりのブラウザ×記事の重複除外件数であり、PV・人間の人数・読了数へ読み替えません。APIの既存`unit: anonymous-browser`は旧項目の単位として維持し、PVの単位は`pageViewTracking.unit: page-view`で区別します。
- 集計日は日本時間。`today`・`yesterday`・`day`の`pageViewsCoverage`は未計測`unmeasured`、開始日の一部`partial`、終了済み日`complete`、当日途中`in-progress`を区別します。開始前のPVは`null`で返し、最初の有効なPVの日時を`pageViewTracking.startedAtUtc`に記録。古い閲覧数からPVを推定・移行しません。
- `last7Days`は当日を含む直近7暦日のPV合計。`measuredDays`と`complete`で期間の欠測を示し、`complete`も当日が終了したという意味ではありません。日別カウンターと表示イベントの重複排除記録は直近14日分に制限し、累計PVは保持します。既存のDurable Objectを使い、追加の計測サービスや有料APIは導入しません。
- 記事ごとの`last7DaysPageViews`も同じ日本時間7暦日の表示回数。トップと記事末尾の「よく読まれている記事」は全公開記事を候補に、この値→当日PV→実際の掲載日時の順で選びます。現在読んでいる記事は記事末尾の候補から除外します。旧ブラウザ数、いいね、編集者による固定選択をPV順位に混ぜません。必要な値が欠けた場合はランキングを表示せず、新着記事への通常リンクを使います。
- 運営者の確認は`?analytics=off`でそのブラウザの自動閲覧送信を停止できます。`/privacy/`の「このブラウザの閲覧を集計しない」でも切り替えられ、`?analytics=on`で再開できます。設定は`localStorage`の`mihassei-analytics-disabled-v1`に保存（保存できなければ当該ページのみ）。`navigator.webdriver === true`の自動操作も送信を抑止しますが、全botや運営者を自動判別する仕組みではありません。GET集計表示と明示的ないいね等の操作は独立しています。
- [`scripts/record_metrics.py`](../scripts/record_metrics.py)はGETだけで前日の集計を`analytics/daily/`へ保存。schema v3は日別PV・対象日の計測範囲・計測開始日時・記事別累計PVを旧ブラウザ指標と分けます。累計値は取得時点の値であり、対象日の終了時点の値ではありません。旧APIにPVがなければ`page_views_available: false`と`null`で残し、既存のschema v2記録は上書きしません。終了済みの日付でも計測開始日は一日分の実績とは限りません。
- 日次記録の`recent_articles[].page_views_last_7_days`は取得時点の推薦用PVです。`page_views_last_7_days_window`で対象期間を確認し、前日の終了値や日別PVと混同しません。日次の伸びは`daily_page_views`と計測範囲を主に確認し、旧ブラウザ指標は互換性と補助分析のために保持します。
- コメント等の書き込みは制限・検証・削除経路が必要です。管理用トークンはCloudflareのSecretとして扱い、GitHubや公開JSに記録しないでください。
- 登録・保存・削除・プライバシーの細部は実装とサイト内`/privacy/`を参照。新しい計測項目を単純な「実人数」と表記しないでください。

## チェックと公開

```bash
python3 scripts/build.py
python3 -m unittest discover -s tests -v
```

- CIの正本は[`.github/workflows/check.yml`](../.github/workflows/check.yml)。Pythonテスト、Nodeテスト、Workerのdry-runを含みます。
- 本番の公開確認は[`.github/workflows/verify-public-photos.yml`](../.github/workflows/verify-public-photos.yml)を参照。PR・CIの成功と、公開URLの到達・画像表示の成功を混同しません。
- 記事の編集方針と引き継ぎは[`EDITORIAL_LOOP.md`](EDITORIAL_LOOP.md)、検索インデックスは[`SEARCH_INDEX_REFRESH.md`](SEARCH_INDEX_REFRESH.md)を参照。
- 不要な従量課金サービス、外部AI API、広告費を勝手に導入しません。ホスティング・ドメイン等の契約状況は契約画面で確認が必要です。

## Docsの原則

READMEに日々の実装記録、過去の施策や実験日記、デプロイ操作の全手順を追記しないでください。小さな改善はPRに残し、長期間有用な手順だけを該当ドキュメントへ反映してください。
