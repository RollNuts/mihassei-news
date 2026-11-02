# 未発生新聞 — Mihassei Shimbun

**https://veripsa.com** · 現実の話題をフックに、「5年後の同じ月日・同じ時刻」の架空ニュースを書くフィクションメディア。

> 【重要】本紙のすべての記事・未来の事件・企業・談話は創作です。実在する人物・企業の不祥事や行動を描くものではありません。ニュースサイトを装って現実の情報を偽る目的ではありません。

## 設計

- 静的HTML/CSS。Python標準ライブラリだけでビルド。記事は `content/articles.json` に保存。
- 1記事 = 1つの独立ページ・オリジナルのSVGイラスト・元の話題へのリンク。
- 発行日時は元ネタの掲載時刻（日本時間）に**5年を加算**。元の情報と架空の記事を別枠に表示。
- 検索・カテゴリ絞り込み・RSS・サイトマップ・スマートフォン表示。
- 記事の自動作成はChatGPTの定期タスクと接続済みGitHubで行う。外部のAI生成APIやAPIキーは使用しない。画像はAPI不要のオリジナルSVG。
- ソーシャル（Redditなど）の公開RSSも候補。Xや5chの本文・画像の無断スクレイピングはしない。許諾済みの公開リンクを使う場合は `content/source_seeds.json` へURL・日時・要旨を追記する。


## 読者導線と編集方針

- トップページはジャンル別の入口を設け、記事数に応じて厚みのあるカテゴリ固定ページと記事一覧フィルターを使い分ける。
- 記事一覧は最初に18件を表示し、「さらに記事を見る」で18件ずつ追加。検索とジャンル・年代フィルタは未表示の記事も含む全件を対象とし、JavaScriptが利用できない場合は全記事を表示する。ホームの号外抽選データは新着40本までに制限し、記事量増加に伴う転送量の肥大化を防ぐ。
- 記事末尾には掲載済み記事から「続けて読む」を選ぶ（同ジャンルを優先し、実際の本紙掲載日が新しいものを先にする）。同じ記事にはリンクしない。
- RSSへの購読導線と、記事・サイトをXで共有するリンクを設ける。未確認のSNSアカウントを「公式」として紹介しない。購読数やPVを実際より多く見せない。
- `/editorial/` で生成AI・自動化の利用、創作と出典の区別、広告と権利対応、連絡方法を公開する。編集方針は将来の運営変更に合わせて更新する。
- AdSense審査・広告収益は保証されない。承認前の広告スクリプトの自動挿入は行わず、広告導入時は読みやすさ・プライバシー・PR表示を優先する。

## 本番構成

- 公開URL: https://veripsa.com
- ホスト: Cloudflare Workers Static Assets + SQLite Durable Object
- 公開GitHub: https://github.com/RollNuts/mihassei-news
- Git連携: main にコミット後、Cloudflare が自動デプロイ
- Build: python3 scripts/build.py
- Deploy: npx wrangler deploy
- Wrangler: wrangler.jsonc（/api/* だけ Worker を起動し、静的ファイル配信は無料）
- AWS / 外部従量課金AI API は利用しない。

## 読者の反応

- src/worker.js の NewsroomStats は Cloudflare Workers Free の SQLite Durable Object を使用。new_sqlite_classes のマイグレーションで自動作成。
- ブラウザ内で生成したランダムIDのSHA-256を保存して、サイト初回来訪・記事初回閲覧・いいね・X共有ボタンクリックを集計する。IPや端末指紋はDBに保存しない。
- GET /api/engagement?articles=slug1,slug2 で人数・記事別リアクションのブラウザ単位の参考値を取得できる。
- 実人数・PV・実際のX投稿数ではない。API障害時は数字を表示せず静的本文は継続する。
- Freeプランの上限に達すると追加API処理が失敗する。勝手な有料プラン移行はしない。Workers契約がFreeであることはCloudflare管理画面で要確認。
### 読者コメント欄（追加課金なし）

- 各記事末尾にニックネーム制のコメント欄を表示。ログイン・メールアドレス不要。本文2〜180文字・ニックネーム16文字以内。
- Cloudflare Workers Free の **SQLite Durable Object `ReaderComments`** にコメントを保存。既存のいいね・閲覧統計用 `NewsroomStats` には一切手を加えない。D1/R2/有料API/外部広告は使わない。
- 1ブラウザにつき5分間隔・最大3件/日、サイト全体で最大20件/時・100件/日。記事ごとに最大100件保持し、新しい順に20件表示。個人情報の入力やURL投稿は禁止。
- 表示は `textContent` を使用してHTMLを解釈しない。自分の投稿は同じブラウザから削除可能。3件の異なるブラウザIDによる通報で自動的に非表示。ブラウザIDはSHA-256ハッシュで保存し、生のIPアドレスはDBに保存しない。
- **重要:** ブラウザID制限は本格的なBOT認証ではなく、IDを再生成すれば回避されうる。管理者は不適切な投稿を確認して削除すること。今後必要になれば無料のCloudflare Turnstile（サイトキーとシークレットの設定が必要）を追加する。
- 管理画面: `https://veripsa.com/assets/comments-admin.html`。コメントの確認・非表示・再表示・完全削除が可能。
- **管理用キーは任意だが設定推奨:** Cloudflare Dashboard → Workers & Pages → `mihassei-news` → Settings → Variables and Secrets に `COMMENTS_ADMIN_TOKEN` を **Secret** として追加する（ランダム24文字以上）。GitHubや公開JavaScriptに絶対に書かない。設定前は管理用APIだけ無効、一般読者のコメント投稿・通報は利用できる。
- コメントは動的APIによるため静的記事本文のSEO・RSS・サイトマップを変更しない。無料枠を超えた場合はAPIがエラーを返して停止し、追加請求のために有料プランへ変更しない。

## 自動投稿と無料運用

**追加の従量課金を発生させない運用方針。** 本リポジトリの外部AI生成API接続コードとGeminiを呼び出すGitHub Actionsの定期発行ワークフローは削除済みです。APIキー、課金アカウント、外部有料モデルを登録する必要はありません。

- 記事生成・更新：ChatGPTの「未発生新聞 ジャンル別多頻度発行」定期タスク（日本時間7:30〜23:30の2時間ごと・9枠に設定済み）。契約中のChatGPTプランに含まれる機能の範囲で稼働し、外部の従量課金AI APIは呼ばない。
- 元ネタ収集：ChatGPTの標準検索を利用。X・掲示板投稿の無断スクレイピング・本文や画像の転載はしない。確認できるURLと投稿日時がある話題だけ採用。
- 更新先：接続済みGitHubの公開リポジトリ `RollNuts/mihassei-news`、`content/articles.json`。ジャンル別の1日9枠で各枠1〜2本の発行を目標とする（記事の公開は出典の確認、接続サービスの稼働、無料利用枠に依存し保証されない）。公開前にJSON妥当性、重複、安全性、フィクション表示、元記事の著作権への配慮を確認。
- GitHub Actions：`.github/workflows/check.yml` のテストのみ。**生成AIへのリクエストは行わない。** GitHub Freeの公開リポジトリの標準ホストランナーを使用。
- 自動サイト更新：Cloudflare WorkersのGit連携でmainの更新を検知し自動デプロイする。
- 画像：`scripts/build.py` が記事ごとに独立したSVGイラストを生成する。画像AIのAPI費用なし。
- 課金防止：Cloudflare Workers Freeを維持し、無料枠超過時は処理を停止する。R2・D1・有料の外部APIを使わない。外部APIの有料プランに接続しない。制限に達したら投稿を見送る（勝手に有料プランへ移行しない）。
- **既存コストは別**：`veripsa.com`のドメイン更新費と利用中のChatGPTプラン料金はゼロにはならない。新たな月額・従量課金を増やさない方針。
- CloudflareのGit連携と独自ドメインは設定済み。デプロイ失敗時はCloudflareのログを確認する。
- 自動発行は、ChatGPTの定期タスク実行時に必要なWeb検索・GitHub連携が利用できる場合のみ成功する。失敗しても成功を偽装しない。定期タスクの利用枠・サービス条件が変わったときは投稿を止める。

### 他のAIを使いたくなったら

文章の候補づくりや査読を別のAIサービスで試すことは可能。ただし複数AIへのAPI接続はしない。手動で候補を持ち込んでGitHubへ追記するか、追加課金のない公式連携が実際に利用可能なときだけ運用を拡張する。

## ローカル実行

```bash
python3 scripts/build.py
python3 -m unittest discover -s tests -v
```

生成した `dist/` を静的サーバーで公開する。

## 著作権と創作

元記事・X・5ch・Redditの文章や画像は転載しない。著作物の一部が偶然一致する可能性もあるので、独立した記事だけを公開する。元ソースへ明示リンクをつける。実際の人物を創作された犯罪・不正等に結び付けない。公式サービスと紛らわしいロゴを使用しない。本文・画像・RSSはフィクションと表示する。

## データ形式

`content/articles.json` に記事を追加する。各記事で `source.published_at` と `fiction_at` は日本時間のISO 8601（例 `2026-11-02T11:00:00+09:00`, `2031-11-02T11:00:00+09:00`）。2月29日は5年後に同日が存在しないので、例外的に自動生成をスキップする。

## ライセンス

記事本文・オリジナル画像の無断転載を許諾するものではありません。外部の素材・写真は同梱しません。

## 検索エンジン登録・広告審査

- Google Search Console: https://search.google.com/search-console/ で Domain property `veripsa.com` を追加し、Cloudflare DNS に Search Console が提示した TXT 値を登録。所有権確認後、`https://veripsa.com/sitemap.xml` を登録。
- 記事の `fiction_at` は読者向けに演出する創作日時。構造化データに記録する `datePublished` は、現実に本紙へ公開した `published_at`（JST ISO 8601）または古い記事の `published_on` であり、架空の未来日付を使わない。
- AdSense 審査用に `ads.txt` と `google-adsense-account` メタタグを登録済み。サイト所有権の確認後に、Google AdSense 側から審査申請する。承認される前に広告表示コードを実行しない。
- AdSense の承認と広告収益は保証されない。著作権・プライバシー・オリジナル性・広告ポリシーを確認し、承認されても読書を妨げる広告大量掲載はしない。
- `/privacy/` を公開し、匿名のブラウザ単位のアクセス測定と、写真表示時の外部通信などを明示している。

## 記事と写真

- `photo` フィールドは任意。含む場合は商用利用が可能な実際の Unsplash 作品ページを開いて、撮影者・ダウンロード対象の画像 URL・ライセンスを事前に確認する。
- `photo` の必須キー：`url`, `source`, `credit`, `alt`, `license`。URL は `https://images.unsplash.com/photo-...`、作品ページ `https://unsplash.com/photos/...`、ライセンス名は `Unsplash`。
- 写真は記事内の出来事そのものではなくイメージ写真。写真家のクレジットを掲載。ダウンロードできなくてもオリジナルSVGへフォールバック。
- `.github/workflows/cache-photos.yml` が作品の画像を無料のGitHub標準ランナーで `assets/photos/` に保存。画像は取得時にJPEGの形式・サイズを検証。GitHubから本番サイトへ自動デプロイ。写真に有料APIや画像生成サーバーは使わない。
- 新規記事は `published_at` に本紙の実際の公開時刻を保存し、`source.published_at` に元ソースの発信時刻を保存する。この2つは必ず別々に扱う。
