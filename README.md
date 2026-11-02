# 未発生新聞 — Mihassei Shimbun

**https://veripsa.com** · 現実の話題をフックに、「5年後の同じ月日・同じ時刻」の架空ニュースを書くフィクションメディア。

> 【重要】本紙のすべての記事・未来の事件・企業・談話は創作です。実在する人物・企業の不祥事や行動を描くものではありません。ニュースサイトを装って現実の情報を偽る目的ではありません。

## 設計

- 静的HTML/CSS。Python標準ライブラリだけでビルド。記事は `content/articles.json` に保存。
- 1記事 = 1つの独立ページ・オリジナルのSVGイラスト・元の話題へのリンク。
- 発行日時は元ネタの掲載時刻（日本時間）に**5年を加算**。元の情報と架空の記事を別枠に表示。
- 検索・カテゴリ絞り込み・RSS・サイトマップ・スマートフォン表示。
- 定期記事生成はGitHub Actions。Gemini API無料枠を使用できるが、無制限無料ではない。画像はAPI不要の自動生成オリジナルSVG。
- ソーシャル（Redditなど）の公開RSSも候補。Xや5chの本文・画像の無断スクレイピングはしない。許諾済みの公開リンクを使う場合は `content/source_seeds.json` へURL・日時・要旨を追記する。

## Cloudflare Pages公開

1. Cloudflare dashboard → **Workers & Pages → Create → Pages → Connect to Git**。
2. GitHubの **`RollNuts/mihassei-news`** を選ぶ。既存Veripsa Coreのリポジトリとは連携しない。
3. Project name: `mihassei-news` 、Production branch: `main`。
4. Framework preset: **None** / Build command: **`python3 scripts/build.py`** / Output directory: **`dist`**。
5. 初回デプロイが成功したことを確認してから、Pages → **Custom domains → Set up a domain** に `veripsa.com` を入力する。
6. Cloudflareの同じアカウントに対象ドメインzoneが存在する必要がある。古いWorkers・Pages・Redirect Rulesなどが稼働中なら競合がないか確認し、旧サービスへの経路を外してから切り替える。**手動DNSだけでCNAMEを作るより、Pages側のカスタムドメイン設定を使用する。**

DNSを変更せずに新サイトを確認する場合は自動発行される `mihassei-news.pages.dev` を使う。公開後は `veripsa.com` の正しいHTMLが返ることを確認する。

## 自動投稿設定

リポジトリ → **Settings → Secrets and variables → Actions → New repository secret** で `GEMINI_API_KEY` を設定。モデルは `gemini-2.5-flash-lite`。ソース選定・記事化・校閲チェックは `.github/workflows/daily-publish.yml` と `scripts/generate.py` を確認。

- 毎日22:40 JSTに自動実行（GitHub Actionsの遅延はあり得る）。別途ChatGPTの自動発行タスクを有効にした場合は二重掲載にならないよう、GeminiのAPIキーを設定せずに使う。
- 日付が今日のニュースRSSおよび公開コミュニティ投稿を候補にし、1日最大1本生成。
- 元データは見出し等のメタデータだけを入力に利用し、原文の転載禁止を明示。
- AI出力が不適切・単調・重複・形式エラーの場合は発行せずに休刊する。
- APIキー未設定なら**自動生成なし**。サイトそのものは問題なく動く。
- API無料枠は提供元の利用条件・上限によって変わる。課金が発生しない設定を保つため、APIプロジェクト側で課金設定を確認する。
- 自動生成記事は誤りや偶然実在の企業と名称が重複するリスクがあるため、運用開始後に定期監査する。

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
