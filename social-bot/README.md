# どうでもいい世界 | X & 4コマ 自動運用（追加API料金ゼロ）

「どうでもいいのに妙に気になる疑問」を一行ネタと4コマにする、小規模なXコンテンツ実験。未発生新聞の既存記事・公開導線には変更を加えません。

## 仕組み

- `posts.json`：ネタ本体。現在15本（テキスト9本、4コマ6本）。新規ネタはここへ追記。
- `render.py`：Python/Pillowで4コマPNGを描画。AI画像生成API、OpenAI API、外部画像サービス不使用。オリジナル図形＋日本語吹き出し。
- `validate.py`：ID・本文の重複、長さ、4コマ枚数、PNG容量をチェック。
- `publish.py`：Buffer API経由で、Xの予約キューへ最大2件ずつ追加。キュー3件まで。既出本文と送信台帳を照合し二重投稿を防ぐ。
- `state.json`：追加した投稿IDだけを記録。画像・メディア本体は `assets/` に置く。
- `.github/workflows/free-surreal-x.yml`：毎日09:23 JSTに起動。**初期設定は投稿OFF**。4コマは本文更新時に生成。

## まずユーザー本人が行う設定（秘密情報はチャットに貼らない）

1. [Buffer](https://buffer.com/pricing) の **Free** を利用し、使いたいXアカウントを1つ接続する。Xアカウント側の連携認可は本人が行う。
2. BufferでXの投稿スケジュールを設定する。例：毎日19:17（日本時間）。本ツールはそのキューへ投稿を追加するだけで、Xへ直接ログインしない。
3. [BufferのAPI設定](https://publish.buffer.com/settings/api) から個人APIキーを取得する（Free枠内）。**APIキーは他人に送らずGitHub Secretsにだけ登録する**。
4. GitHub `RollNuts/mihassei-news` → Settings → Secrets and variables → Actions → Repository secrets へ `BUFFER_API_KEY` を登録する。
5. Xを複数接続している場合だけ、ActionsのRepository variablesに `BUFFER_CHANNEL_ID` を設定する。1チャンネルなら自動選択。
6. 接続後、ActionsのRepository variablesに `ENABLE_X_BOT` = `true` を設定する。**これを設定するまでは自動投稿しない**。
7. GitHub Actions → `Free Surreal X + 4-koma Bot` → Run workflow を実行してログを確認する。予約キューの中身はBufferで確認する。

設定前は安全モードでスキップ。設定しても初回は最大2投稿が予約キューに入るだけで、Bufferに設定した時刻に配信される。

## 無料運営・安全設計

- Buffer Free：X含む最大3チャンネル・各10件の予約キュー、APIキー1本。API呼び出し上限の範囲内で運用。無料アカウントのX指標取得には制約がある。
- GitHub：**公開リポジトリの標準Actions**を利用。追加有料サービス、従量課金のX API、OpenAI APIの呼び出しはしない。
- 無料の画像生成AIを毎日無制限に使えるわけではない。4コマは小容量PNGの定型レンダリングで代替する。
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

4コマの場合は `kind:"comic"` と `motif`（star/alien/vampire/angel/nessie/dinosaur）、`panels`（4つの短文）を追加する。本文・4コマ両方の面白さを人間が確認してから公開すると品質を維持できる。

公式資料：[Buffer API](https://developers.buffer.com/guides/your-first-post.html) ／ [Buffer無料プラン](https://buffer.com/pricing) ／ [X自動化ルール](https://help.x.com/en/rules-and-policies/x-automation)
