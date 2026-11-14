# Technical overview / 技術構成

開発者と保守担当者向けの実装上の要点です。担当・重複防止・公開完了の共通手順は[OPERATIONS.md](OPERATIONS.md)、変更履歴はPR・コミットを参照してください。

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
- Cloudflareのビルド監視は含むパス`*`・除外`docs/*`、非本番ビルドはOFF。公開SHAがmainより古くても、祖先関係と差分が`docs/`だけだとGitで証明できる場合に限り同じ公開物として扱い、実配信SHAを報告する。記事・画像・コード等の差分は配信を待つ。
- 生成結果は`dist/`。手元で公開するだけならCloudflareアカウントは不要です。

## データと日時

- 架空ニュースは`content/articles.json`。`published_at`が実際の初回掲載日時で、演出上の`fiction_at`はその年だけを5年進め、月日・時刻・タイムゾーンを一致させます。`source.published_at`は着想元資料の日時であり、紙面日時の基準には使いません。
- 連載は`content/serials.json`。実在の市場データに基づく考察は`kind: analysis`。`scripts/serials.py`の`WITHDRAWN_SERIES`に指定した連載は公開loader・生成から除外し、原JSONと履歴保護は保持する。
- 独立した週刊写真コラムは`content/photo_issues.json`から`/photo/weekly/`へ生成。`scripts/photo_issues.py`が号の固定指紋と画像・採否を検証し、draftは公開出力しない。担当枠・人物設定・編集の正本は[EDITORIAL_LOOPの成人企画](EDITORIAL_LOOP.md#成人の軽い未来企画既存枠)を参照する。 号改訂は`editorial_history`に固定項目の完全snapshot・旧採用/拒否・登録studyを残し、`images[cut].adoption`で移す元/先の号FPとcutを指定する。元native来歴・旧reviewは保持し、同じ固定モデル、原receiptの検証、現号への新しい実画像reviewを要求する。 `accepted_article_photo`はsource記事かつ固定masterの既存JPEGだけを、`article_photo_sources`の本文指紋・生成record・元acceptedのsnapshotと原receiptで照合し、原生成記録を保つ。`master_archive`は同SHAの固定JPEGを指し、通常記事の将来の写真更新から号を独立させる。公開写真は号固有の版付きURLへコピーする。 試作の固定計画はPR外の計画branchへ先に保存し、採用素材を揃えてから公開PRへまとめる。必要な最終CIを保ち、画像途中ごとの全CI・本番buildを増やさない。
- 市場分析は資料`sources`、仮説`scenarios`、検証可能な`forecast`を持ち、判定時には結果の根拠を残します。
- RSS、構造化データ、サイトマップでは架空の未来日時を実際の掲載日時として扱いません。
- 続報記事は既刊を上書きせず独立した記事として保存し、任意の `followup_of` に直前の関連記事slugを記録します。親記事が存在し、子記事より先に実公開されていることをビルドで検証し、公開ページでは前後を相互リンクします。
- 詳しい検証基準は[`docs/SERIAL_EDITING_GUIDE.md`](SERIAL_EDITING_GUIDE.md)と[`scripts/forecast_ledger.py`](../scripts/forecast_ledger.py)を参照。

## Xへの読者共有

- 記事の共有ボタンと手動用下書きは `scripts/social_text.py` の `reader_share_text` を共用。検証済みの `x_share_lead` があれば優先し、なければ見出しを使う。要約の断片や定型の問いかけは足さない。紹介文の編集基準は `social-bot/article-picks/README.md` を参照する。
- `#未発生新聞` を必ず1回残し、発見語は共通の `article_hashtags` で最大2個。本文・タグ・リンクを合わせたXの重み付き文字数を検証し、長すぎる紹介文は途中で切らず見出しへ戻す。
- 記事ボタンは `reader_share`、手動デスクは `share_desk` の既存UTMを維持。ボタンはXの編集画面を開くだけで、送信は読者が行う。デスクは公開ビルドに含めない。

## 画像

- 通常の表紙は[`scripts/editorial_covers.py`](../scripts/editorial_covers.py)で生成する記事別SVG。SNS共有用にはPNGを生成します。
- 記事固有の挿絵は`assets/editorial-scenes/`のSVGを本文指紋と照合して採用します。生成JPEGは機械検証に加え、`content/visual_reviews.json`の目視確認が現在の記事・画像に一致する場合だけ公開。未検品JPEGとその派生動画は公開ビルドへコピーしません。日次・週次担当の手順は[`VISUAL_OPERATIONS.md`](VISUAL_OPERATIONS.md)を参照。
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
- 急増の原因を日次スナップショットまで待たずに確認できるよう、PVは日本時間の1時間単位でも保持します。`hourly.today` / `hourly.yesterday` / 日付指定時の`hourly.day`は、各時間を`unmeasured` / `partial` / `complete` / `in-progress` / `future`で区別します。`topArticles.currentHour` / `today` / `last7Days`（日付指定時は`day`）は全記事のPVカウンターからサーバー側で順位を作り、直近20記事だけを見て当たり記事を取り逃がさないようにします。急増した時間が過ぎた後も原因を遡れるよう、`?hour=YYYY-MM-DDTHH`で直近14日以内の1時間を指定すると、`selectedHour`にその時間のPVと流入元、`topArticles.selectedHour`にその時間の上位記事を返します。`scripts/growth_diagnosis.py`はこのGETだけの情報と既存X送達台帳を突き合わせ、X配信・単一記事・混合流入のどれが有力かを因果断定せず整理します。さらに同時間帯の `pageViewKinds` を使い、記事閲覧が中心か、トップ・一覧・連載などのナビゲーション表示が中心かを別軸で記録します。ページ種別は表示先の内訳であり、同じ人の回遊回数や実人数の推定には使いません。
- セッションは一般的なWeb解析に合わせ、30分間ページ表示がなければ新しい訪問として扱います。ブラウザにはランダムUUID・最終表示時刻・粗い流入区分だけを保存し、サーバーにはハッシュ化したセッションIDを重複排除用に直近14日だけ保持します。`sessions`は訪問回数、`visitors`は匿名ブラウザ識別子、PVは表示回数であり、相互に読み替えません。導入前のセッション数は復元せず`sessionTracking.startedAtUtc`で開始点を明示します。
- 流入元別PVは、セッション最初の「X / 検索 / その他外部 / 判別不能」をその30分セッション内の後続ページにも引き継ぐ**セッション獲得元**として集計します。書き込み量を抑えるため時間別カウンターだけを保存し、当日・日付指定の流入元PVはその日の時間別値を読み取り時に合算します。累積の流入元PVや記事別の流入元PVは保持しません。`pageViewSources*`の開始前は復元せず、完全な参照URL・検索語・UTM文字列は保存しません。従来の`channels` / `arrivals`はブラウザ単位の累積参考値として互換維持し、PV流入元とは別指標です。
- サイト全体PVの増加が記事由来か非記事ページ由来かを判別するため、各表示を `home` / `article` / `archive` / `serial` / `other` の5分類だけで集計します。ページ種別も時間別カウンターだけを保存し、当日・日付指定の内訳は時間別値から合算します。累積のページ種別カウンターは保持しません。`pageViewKinds*`は完全なpathnameを保存せず、独立した開始時刻を持つため導入前のPVを0で埋めません。
- 記事ごとの`last7DaysPageViews`も同じ日本時間7暦日の表示回数。トップと記事末尾の「よく読まれている記事」は全公開記事を候補に、この値→当日PV→実際の掲載日時の順で選びます。現在読んでいる記事は記事末尾の候補から除外します。旧ブラウザ数、いいね、編集者による固定選択をPV順位に混ぜません。必要な値が欠けた場合はランキングを表示せず、新着記事への通常リンクを使います。
- 運営者の確認は`?analytics=off`でそのブラウザの自動閲覧送信を停止できます。`/privacy/`の「このブラウザの閲覧を集計しない」でも切り替えられ、`?analytics=on`で再開できます。設定は`localStorage`の`mihassei-analytics-disabled-v1`に保存（保存できなければ当該ページのみ）。`navigator.webdriver === true`の自動操作も送信を抑止しますが、全botや運営者を自動判別する仕組みではありません。GET集計表示と明示的ないいね等の操作は独立しています。
- [`scripts/record_metrics.py`](../scripts/record_metrics.py)はGETだけで前日の集計を`analytics/daily/`へ保存。schema v3は日別PV・対象日の計測範囲・計測開始日時・記事別累計PVを旧ブラウザ指標と分けます。新APIが利用できる場合は、その日のセッション数、24時間のPV列、上位記事、流入元別PVも同じ日次ファイルへ固定して、オンライン側の14日保持を過ぎても比較できるようにします。累計値は取得時点の値であり、対象日の終了時点の値ではありません。旧APIに新項目がなければ欠測のまま残し、既存のschema v2/v3記録は上書きしません。終了済みの日付でも計測開始日は一日分の実績とは限りません。
- 日次記録の`recent_articles[].page_views_report_day`は、その記事の`date_jst`1日分のPVです。同じ完了日で記事同士を比べる編集分析ではこれを優先し、公開時刻が遅い記事はその日に公開されていた時間も併記して補助的に正規化します。`page_views_last_7_days`は取得時点の推薦用PVとして残し、`page_views_last_7_days_window`で対象期間を確認します。前日の終了値、7日窓、累計PVを混同せず、日次の伸びは`daily_page_views`と計測範囲を主に確認します。旧ブラウザ指標は互換性と補助分析のために保持します。
- コメント等の書き込みは制限・検証・削除経路が必要です。管理用トークンはCloudflareのSecretとして扱い、GitHubや公開JSに記録しないでください。
- 登録・保存・削除・プライバシーの細部は実装とサイト内`/privacy/`を参照。新しい計測項目を単純な「実人数」と表記しないでください。

## チェックと公開

```bash
python3 scripts/build.py
python3 -m unittest discover -s tests -v
```

- CIの正本は[`.github/workflows/check.yml`](../.github/workflows/check.yml)。Pythonテスト、Nodeテスト、Workerのdry-runを含みます。
- Cloudflare Buildsは本番`main`の自動デプロイを有効にし、非本番のプレビューブランチビルドはOFFにします。ブランチごとの`wrangler preview`がDurable Objectの無料namespace枠を使い切るためです。既存namespaceは削除せず、PRは上記CIで検証し、統合後の本番revisionと実配信を確認します。プレビュー停止を画像・記事発行や本番ビルドの停止と混同しません。再有効化する場合は無料枠と実行コマンドを確認し、本番のbindings・migrationsを変更したり、検証失敗時に本番deployへ切り替えたりしません。
- 本番の公開確認は[`.github/workflows/verify-public-photos.yml`](../.github/workflows/verify-public-photos.yml)を参照。公開revisionの完全SHA一致を最大12分待つ処理はここへ集約し、同一リポジトリのmainの公開確認が成功してからPV/API診断を実行します（定時・手動診断は維持）。PR・CIの成功と、公開URLの到達・画像表示の成功を混同しません。
- 記事の編集方針と引き継ぎは[`EDITORIAL_LOOP.md`](EDITORIAL_LOOP.md)、検索インデックスは[`SEARCH_INDEX_REFRESH.md`](SEARCH_INDEX_REFRESH.md)を参照。
- 不要な従量課金サービス、外部AI API、広告費を勝手に導入しません。ホスティング・ドメイン等の契約状況は契約画面で確認が必要です。

## Docsの原則

READMEに日々の実装記録、過去の施策や実験日記、デプロイ操作の全手順を追記しないでください。小さな改善はPRに残し、長期間有用な手順だけを該当ドキュメントへ反映してください。

