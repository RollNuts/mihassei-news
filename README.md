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

## Cloudflare Workers 公開（推奨・現行ダッシュボード）

Cloudflareの現在のGit取り込み画面はWorkersを標準表示します。**Pagesへ戻る必要はありません**。

1. Workers & Pages → Create → Import Git repository → `RollNuts/mihassei-news`。
2. Project name: `mihassei-news`。
3. Build command: `python3 scripts/build.py`。
4. Deploy command: `npx wrangler deploy`。
5. Preview command: デフォルトのまま。
6. 「デプロイ」を押す。`wrangler.jsonc`が`dist/`を静的アセットとして公開します。

この構成は**Cloudflare Workers Static Assets**を利用し、Workersコードを動かさない静的配信のため、リクエストは無料・無制限の対象です。独自ドメインの切り替えは初回デプロイ成功後に実施してください。Squarespace登録ドメインもCloudflareへDNSを接続できますが、既存メール設定やレコードを保全してから切り替えます。

## Cloudflare Pages公開

1. Cloudflare dashboard → **Workers & Pages → Create → Pages → Connect to Git**。
2. GitHubの **`RollNuts/mihassei-news`** を選ぶ。既存Veripsa Coreのリポジトリとは連携しない。
3. Project name: `mihassei-news` 、Production branch: `main`。
4. Framework preset: **None** / Build command: **`python3 scripts/build.py`** / Output directory: **`dist`**。
5. 初回デプロイが成功したことを確認してから、Pages → **Custom domains → Set up a domain** に `veripsa.com` を入力する。
6. Cloudflareの同じアカウントに対象ドメインzoneが存在する必要がある。古いWorkers・Pages・Redirect Rulesなどが稼働中なら競合がないか確認し、旧サービスへの経路を外してから切り替える。**手動DNSだけでCNAMEを作るより、Pages側のカスタムドメイン設定を使用する。**

DNSを変更せずに新サイトを確認する場合は自動発行される `mihassei-news.pages.dev` を使う。公開後は `veripsa.com` の正しいHTMLが返ることを確認する。

## 自動投稿と無料運用

**追加の従量課金を発生させない運用方針。** 本リポジトリの外部AI生成API接続コードとGeminiを呼び出すGitHub Actionsの定期発行ワークフローは削除済みです。APIキー、課金アカウント、外部有料モデルを登録する必要はありません。

- 記事生成・更新：ChatGPTの「未発生新聞 自動発行」定期タスク（日本時間22:30に設定済み）。契約中のChatGPTプランに含まれる機能の範囲で稼働し、外部の従量課金AI APIは呼ばない。
- 元ネタ収集：ChatGPTの標準検索を利用。X・掲示板投稿の無断スクレイピング・本文や画像の転載はしない。確認できるURLと投稿日時がある話題だけ採用。
- 更新先：接続済みGitHubの公開リポジトリ `RollNuts/mihassei-news`、`content/articles.json`。1日最大1本。生成した内容は公開前にJSON妥当性、重複、安全性、フィクション表示、元記事の著作権への配慮を確認。
- GitHub Actions：`.github/workflows/check.yml` のテストのみ。**生成AIへのリクエストは行わない。** GitHub Freeの公開リポジトリの標準ホストランナーを使用。
- 自動サイト更新：Cloudflare Pages FreeにGitHubを接続すると、`main`への反映時にサイトをビルドして配信する。
- 画像：`scripts/build.py` が記事ごとに独立したSVGイラストを生成する。画像AIのAPI費用なし。
- 課金防止：Cloudflare Pages Freeを選び、Workers有料機能・R2・KV・D1の有料機能を使わない。外部APIの有料プランに接続しない。制限に達したら投稿を見送る（勝手に有料プランへ移行しない）。
- **既存コストは別**：`veripsa.com`のドメイン更新費と利用中のChatGPTプラン料金はゼロにはならない。新たな月額・従量課金を増やさない方針。
- CloudflareのGit連携はユーザーのCloudflareアカウント画面で初回設定が必要。設定前は記事をGitHubに追加しても独自ドメインには反映されない。
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
