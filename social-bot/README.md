# どうでもいい世界 → 《ネジまち》連載漫画 | X無料自動運用

既存のシュールネタ投稿から、オリジナル機械都市の漫画連載 **《ネジまち》** に進化。既存の予約済み2件は消さず、以後の新規投稿は `series: "nejimachi"` の連載だけを順番に予約。**4コマ固定ではなく2〜6コマ**、おかしな機械の街で生きる3体の小さな住民が主人公。**[正史と時系列の設定資料](ip/README.md)** を執筆前に参照する。未発生新聞の既存記事・表示には変更を加えない。

## 仕組み

- `posts.json`：旧ネタ15本は保存。新しい漫画は `series: nejimachi` と `episode` を付けて追記。
- `render.py`：過去ネタの4コマ専用レンダラ。
- `render_ip.py`：新シリーズ2〜6コマ漫画（同じ街とキャラの固定絵柄）。無料Pillowだけで1080×1350 PNGを描画。
- `ip/canon.json`：確定世界観とキャラクター。変更前に必ず確認。
- `ip/ledger.json`：第1話からの出来事・既知の事実・未解決の謎・時系列。
- `ip/audit.py`：登場時期・時系列・事実の初出・予約後の原稿固定を検査。
- `validate.py`：ID・本文の重複、文字数、漫画の2〜6コマ構成、PNG容量、正史との整合を検査。
- `publish.py`：Buffer経由で、`nejimachi` の連番エピソード**だけ**予約。キュー3件まで・1回最大2件、既出本文と履歴のSHA256を照合。旧ネタ在庫は自動投稿対象から除外。
- `state.json`：予約済みIDと連載の内容SHA256を記録し、予約後の改変を検知。画像は `assets/`。
- `.github/workflows/free-surreal-x.yml`：毎日09:23 JSTに起動。設定済みの場合に限り予約投稿、スクリプト・本文変更時には画像生成と検証を行う。

## まずユーザー本人が行う設定（秘密情報はチャットに貼らない）

1. [Buffer](https://buffer.com/pricing) の **Free** を利用し、使いたいXアカウントを1つ接続する。Xアカウント側の連携認可は本人が行う。
2. BufferでXの投稿スケジュールを設定する。例：毎日19:17（日本時間）。本ツールはそのキューへ投稿を追加するだけで、Xへ直接ログインしない。
3. [BufferのAPI設定](https://publish.buffer.com/settings/api) から個人APIキーを取得する（Free枠内）。**APIキーは他人に送らずGitHub Secretsにだけ登録する**。
4. GitHub `RollNuts/mihassei-news` → Settings → Secrets and variables → Actions → Repository secrets へ `BUFFER_API_KEY` を登録する。
5. Xを複数接続している場合だけ、ActionsのRepository variablesに `BUFFER_CHANNEL_ID` を設定する。1チャンネルなら自動選択。
6. 接続後、ActionsのRepository variablesに `ENABLE_X_BOT` = `true` を設定する。**これを設定するまでは自動投稿しない**。
7. GitHub Actions → `Free Surreal X + 4-koma Bot` → Run workflow を実行してログを確認する。予約キューの中身はBufferで確認する。

**本運用**：現在は連携済みのXアカウントへ自動予約が有効。旧ネタ2本はすでにBuffer予約済みのため削除せず、次の空き枠から新連載を始める。投稿枠はBufferの設定（19:30 JST）を使う。投稿に失敗した際はログとキューで確認。

## 無料運営・安全設計

- Buffer Free：X含む最大3チャンネル・各10件の予約キュー、APIキー1本。API呼び出し上限の範囲内で運用。無料アカウントのX指標取得には制約がある。
- GitHub：**公開リポジトリの標準Actions**を利用。追加有料サービス、従量課金のX API、OpenAI APIの呼び出しはしない。
- 無料の画像生成AIを毎日無制限に使えるわけではない。漫画は2〜6コマの小容量PNGで描画する。
- 内容は架空・創作と分かるようにし、模倣・差別・個人攻撃・虚偽の実ニュース風投稿はしない。
- Xの自動化ポリシーに従い、重複・大量投稿・トレンド便乗自動化・自動いいね/自動フォローをしない。
- `ENABLE_X_BOT` を `false` にするか削除すると翌回から予約追加を停止。既にBufferに登録された予約はBuffer内でキャンセルする。
- リポジトリ内の `state.json` はAPIキーや認証情報を含まない。
- GitHubストレージが肥大化しないよう画像は圧縮PNG・5MB未満（通常もっと小さい）とし、生成した画像のみ保持する。
- ネタは有限在庫。**追加GPT API課金なしで新ネタを増やすには、既存ChatGPTの定期タスク等で `posts.json` を補充する必要がある**。このCIだけでは無限に新ネタを生成しない。

## 追加ネタの書き方

```json
{"id":"unique-short-id","title":"面白い題名","kind":"text","text":"本文"}
```

既存の単発ネタはこの形式のまま保存するが、**自動予約の対象にはしない**。

新しい漫画では `ip/README.md` を読み、`series: "nejimachi"`、`episode` 連番、`chapter`、`day`、`order`、`location`、`characters`、`canon_refs`、`panels`（2〜6個の `{scene,focus,secondary?,mood,sign,line,caption}`）を指定。**同時に `ip/ledger.json` の対応話も追記**。新しい設定は先に `ip/canon.json` に追加する。予約済み原稿は編集禁止。

```bash
python social-bot/render.py
python social-bot/render_ip.py
python social-bot/validate.py
```

**無料運用の限界**：毎回の本格的な手描きクオリティや完全な意味上の矛盾検出は保証されない。現在の作画はPillowのオリジナル図形による試作品。設定の整合と画像の見やすさを継続改善する。

公式資料：[Buffer API](https://developers.buffer.com/guides/your-first-post.html) ／ [Buffer無料プラン](https://buffer.com/pricing) ／ [X自動化ルール](https://help.x.com/en/rules-and-policies/x-automation)
