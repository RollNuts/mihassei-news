# Repository guidance

このリポジトリは、公開中の未発生新聞（https://veripsa.com）のコードとコンテンツです。

## README / docs

- READMEは訪問した開発者が短時間で理解できる概要、主な機能、技術構成、ローカルでの試し方、詳細ドキュメントへのリンクに限定する。目安は100行以内。
- 「本日対応したこと」「改善日付ごとの施策」「詳細な内部運営ルール」「同じ注意書きの繰り返し」をREADMEへ追記しない。変更履歴はコミット・PRに残す。
- 永続的に役立つ技術上のルールは`docs/ARCHITECTURE.md`、画像関連は`docs/MAC_SCENE_SETUP.md`や`docs/FREE_CPU_IMAGES.md`、編集ルールは`docs/EDITORIAL_LOOP.md`や`docs/SERIAL_EDITING_GUIDE.md`に記録。
- 事実に基づかない性能・Star・訪問数・検証成功・生成画像の品質を主張しない。相矛盾する状況説明がないか確認する。

## Safety / scope

- 架空ニュースと実在の市場データは明確に区別し、実際の掲載日と演出の未来日を混ぜない。
- 外部の従量課金AI API、有料GPU、追加クレジット、有料プランへの自動変更をしない。
- README・docsだけの整理でサイト生成、記事の内容、画像、公開設定、Cloudflare設定、定期発行ワークフローを変更しない。
- 公開前に必要なテストを実行。CI成功のみで本番公開成功と記載しない。
