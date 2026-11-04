# 未発生新聞 / Mihassei News

[**サイトを見る — veripsa.com**](https://veripsa.com) · [**技術構成**](docs/ARCHITECTURE.md) · [**CI**](https://github.com/RollNuts/mihassei-news/actions/workflows/check.yml)

> A fictional satire newspaper powered by a Python static-site generator, GitHub Actions, and Cloudflare Workers.

**「5年後にこうなったら笑える」**を新聞の形にした個人制作メディア。現実の話題を手がかりに、日常の矛盾を2031年の小さな珍事として描きます。未来を的中させる予測サイトではありません。

## What it does

- **創作ニュース** — 出典と実際の掲載日を分けて記録し、記事ごとの静的ページを生成
- **独立連載** — 推理小説と、公開資料を引用する市場分析を別データ・別ページとして配信
- **読める・見つかる** — カテゴリ、日付別アーカイブ、全文検索、関連記事、RSS、サイトマップ、OGP
- **画像のフォールバック** — 検証済みの写真を優先し、使えなければ記事別の自作SVG・共有用PNGへ切り替え
- **任意の読者機能** — コメント、投票、反応の集計をCloudflare Workers + SQLite Durable Objectsで実装

創作ニュース・推理小説の内容はフィクションです。市場分析は出典と仮説を区別し、将来の結果を保証しません。

## How it's built

| 領域 | 実装 |
| --- | --- |
| サイト生成 | Python（基本ビルドは標準ライブラリ） |
| コンテンツ | `content/articles.json` / `content/serials.json` |
| UI | HTML、CSS、JavaScript |
| 配信・読者API | Cloudflare Workers Static Assets / SQLite Durable Objects |
| CI・公開検証 | GitHub Actions、Python / Nodeテスト |
| 画像 | 自作SVG・PNG、承認済み写真。ローカルComfyUI生成は任意 |

**技術的な特徴:** 静的コンテンツをAPIなしでも配信しつつ、必要な対話機能だけをWorkerに分離。出典・日時・連載の整合性、画像の差し替え、公開後の到達性をテストで確認します。

## Run locally

Python 3があれば静的サイトを生成できます。

```bash
python3 scripts/build.py
python3 -m http.server 8000 --directory dist
```

ブラウザで `http://localhost:8000` を開いてください。**静的ページの閲覧はCloudflareの認証情報なしで可能**です。コメント・投票などのAPIはローカルの静的サーバーだけでは動作しません。

テストは以下で実行します。画像関連の一部テストには無料のPillowが必要です。

```bash
python3 -m pip install 'Pillow>=10.1,<13'
python3 -m unittest discover -s tests -v
```

CIではこれに加え、Nodeのブラウザ周辺テストとWorkerのdry-runを実行します。

## Project map

- [`scripts/build.py`](scripts/build.py) — 記事・一覧・検索導線・RSS・SEOなどの静的生成
- [`scripts/serials.py`](scripts/serials.py) — 連載の生成
- [`scripts/editorial_covers.py`](scripts/editorial_covers.py) — 記事別の自作表紙
- [`src/worker.js`](src/worker.js) — 読者向けAPI
- [`tests/`](tests/) — ビルド、編集、セキュリティ、読者導線などのテスト
- [技術構成・運用の概要](docs/ARCHITECTURE.md) — 仕組みを詳しく知りたい人向け

### Documentation

[運用の入口](docs/OPERATIONS.md) · [編集ルール](docs/EDITORIAL_LOOP.md) · [連載ガイド](docs/SERIAL_EDITING_GUIDE.md) · [Mac画像生成](docs/MAC_SCENE_SETUP.md) · [CPU画像生成の検証状況](docs/FREE_CPU_IMAGES.md) · [検索インデックス確認](docs/SEARCH_INDEX_REFRESH.md)

## Usage and rights

コードを公開していますが、**このリポジトリにはソフトウェアの再利用を許諾するLICENSEを設定していません**。公開されていること自体は、コード・記事・画像の無断再利用を許可する意味ではありません。ライセンス方針が決まるまで、再利用は事前に確認してください。外部の有料AI APIはサイト生成に必要ありません。

## Follow the project

気になったら **[Star](https://github.com/RollNuts/mihassei-news)** して後で見返してください。更新通知は **Watch → Custom** から選べます。バグ報告は再現手順を添えて [Issues](https://github.com/RollNuts/mihassei-news/issues) へ。

Created by [RollNuts](https://github.com/RollNuts) · [Veripsa Core](https://github.com/RollNuts/veripsa) — 並行PRの関係を調べる開発ツール。ホスト版は停止中、ローカル評価は利用可能です。
