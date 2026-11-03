# 未発生新聞：軽量動画（任意・追加費用0円）

記事本文を邪魔しない短い動画を、承認済みの生成JPEGからローカルで作る仕組み。**実際の人物が動くAI動画生成ではなく、静止画へゆっくりズームする4秒の無音演出クリップ**です。現実のニュース映像ではありません。

## 読者への配信

- 記事ごとの動画は、**検証済みMP4が存在する場合だけ**ネイティブHTML `<video>` で表示。動画がない記事は従来の写真／SVGのまま。
- **autoplayなし・preload="none"・静止画posterあり・音声なし**。タップして見たい読者だけ再生し、モバイルのデータセーバーや動きを減らす設定を尊重します。
- トップ・記事一覧は従来どおり静止画。動画があるカードには「▶ 4秒映像」の小さな表示だけを付け、動画ファイルは読み込みません。
- OGP／RSSはこれまでの軽いJPEG／PNGを使用。記事URL・canonical・サイトマップも変えません。
- 実際の動画がある記事だけ `VideoObject` に公開日時・MP4・サムネイルを記録。検索結果での動画表示は保証されません。

## 使い方（既存のMac／ローカルPC）

まず、通常のGitHubリポジトリのコピーで実行します。**mac_scene_control の停止状態（OFF）を尊重**するため、止めている場合は再開を勝手に行いません。

~~~sh
# 候補の確認だけ。インストール・ネットワーク・新規ファイル作成なし
python3 scripts/motion_clips.py --limit 1

# 既存のffmpeg・ffprobeがある場合だけ、1本作る（通常4秒）
python3 scripts/motion_clips.py --generate --limit 1
# または特定の記事だけ
python3 scripts/motion_clips.py --generate --slug ARTICLE-SLUG
~~~

**ffmpeg／ffprobeが入っていなければ停止するだけ**。自動インストール、brewの追加導入、API利用、有料GPUへの切り替えはしません。動画は `assets/clips/ARTICLE-SLUG.mp4` と `assets/clips/manifest.json` に保存されます。GitHubへの反映には、生成物の目視確認と通常のgitによる明示的なコミット・pushが必要です。スクリプトは勝手にpushしません。

## GitHub Actionsによる自動生成（無料、上限付き）

既存のMac画像生成スケジュールは変更せず、Macから**検証済みJPEGがmainへ届いたとき**に `.github/workflows/auto-motion-clips.yml` が動きます。初回導入時にも1回起動します。

- GitHubの公開リポジトリで、通常の無料Ubuntuランナーを使用。新しい有料GPU・AI・動画生成API・サブスクリプションは使いません。使い切りランナーに限り、無償ffmpegを用意します。
- **1回の実行で動画を最大1本**。すでに有効なクリップがあれば再生成せず、画像に合わない古い映像は無視します。
- `assets/clips/manifest.json` と検証済みMP4のみを、GitHubのbotが**通常の非強制push**で反映。更新競合時は最新mainを取り直して最大3回だけ再試行。
- 動画追加のbot pushでは通常のGitHub CIは自動発火しないため、**公開確認用ワークフローを明示的に起動**します。変更がなければ何もpushしません。
- Macローカル生成とは別の停止スイッチです。Macの `stopped.json` だけではGitHub上の動画生成は停止しません。

**いつでも停止:** `.github/motion-clips.json` の `"enabled": false` をmainへコミットするか、GitHub Actionsの `Free bounded article motion clips` をDisableしてください。再開は `"enabled": true`。停止中も既存の写真・記事・動画ファイルを削除しません。進行中の処理はmainの再確認とpush競合で停止し、緊急時はGitHub Actionsで実行中のジョブもキャンセルできます。

実行結果は [GitHub Actions](https://github.com/RollNuts/mihassei-news/actions/workflows/auto-motion-clips.yml) と、既存の [公開SEO検証](https://github.com/RollNuts/mihassei-news/actions/workflows/verify-public-photos.yml) で確認。GitHub上の自動生成結果とCloudflare本番への反映は**別々に検証**します。実際の人物を動かすAI動画ではなく、写真を動かす短い演出です。


## 容量と安全

| 項目 | 制限 |
| --- | --- |
| 動画長さ | 4秒 |
| 解像度／fps | 640×360／12fps |
| 形式 | H.264 MP4・音声なし |
| 動画1本 | 最大450KiB |
| 全クリップ合計 | 最大24MiB |
| 同時候補 | 1回最大2本 |
| クリップ登録 | 最大48本 |
| ローカル空き容量 | 12GiBを下回る場合は生成を中止 |

動画が記事の内容・承認済み画像・SHA-256と一致しない場合は、**動画を公開せず静止画へ戻す**。公開処理は許可したファイルのみコピーし、不明なMP4／古いmanifest／別記事の素材を除外。失敗時も記事と従来の写真は削除しません。

ローカル動画の作成を続けても上限に達したら新規生成を止めます。既存データを勝手に消して増産しません。実写で俳優や小道具が動く本格AI動画は別の重い生成処理が必要なので、この軽量機能とは区別しています。
