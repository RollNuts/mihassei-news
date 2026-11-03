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
- GitHub公開ランナーでのCPU画像生成は実験的な経路です。**定時実行の有効・停止や画質検証は[`FREE_CPU_IMAGES.md`](FREE_CPU_IMAGES.md)を正本として確認**してください。設定済みというだけで画像の品質・本番反映を成功と見なしません。

## 読者向けAPIと測定

- `src/worker.js`にはコメント、投票、反応などのAPIがあります。Cloudflare Workers + SQLite Durable Objectsを使用。
- 静的な記事と、API依存の操作を分離。APIが利用できない時も閲覧は継続します。
- 閲覧等の数値は匿名ブラウザ識別子に基づく**参考値**であり、人間のユニークユーザー数やページビューを意味しません。
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
