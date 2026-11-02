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
## 自動投稿と無料運用

**追加の従量課金を発生させない運用方針。** 本リポジトリの外部AI生成API接続コードとGeminiを呼び出すGitHub Actionsの定期発行ワークフローは削除済みです。APIキー、課金アカウント、外部有料モデルを登録する必要はありません。

- 記事生成・更新：ChatGPTの「未発生新聞 自動発行」定期タスク（日本時間9:30・13:30・18:30・22:30の4枠に設定済み）。契約中のChatGPTプランに含まれる機能の範囲で稼働し、外部の従量課金AI APIは呼ばない。
- 元ネタ収集：ChatGPTの標準検索を利用。X・掲示板投稿の無断スクレイピング・本文や画像の転載はしない。確認できるURLと投稿日時がある話題だけ採用。
- 更新先：接続済みGitHubの公開リポジトリ `RollNuts/mihassei-news`、`content/articles.json`。1枠最大1本（1日最大4本）。生成した内容は公開前にJSON妥当性、重複、安全性、フィクション表示、元記事の著作権への配慮を確認。
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
