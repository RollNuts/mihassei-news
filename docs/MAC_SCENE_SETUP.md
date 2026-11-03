# Macでの実写コント画像生成

GitHub側のコードだけではMacにモデルは入りません。画像がなくても、承認済み写真またはSVGで毎日の発行は続きます。外部の画像API・有料GPU・追加ChatGPTクレジットは使用しません。

## 停止・再開・状態確認（既存Mac向け）

導入済みモデルとvenvを再利用します。デスクトップの **未発生新聞画像生成** フォルダにある次のファイルをダブルクリックできます。

| ファイル | 動作 |
| --- | --- |
| `画像生成を停止.command` | Macの画像生成だけを停止。実行中の専用処理とlaunchdを解除し、再起動後もOFFを維持 |
| `画像生成を再開.command` | 初回3枚の確認記録を検証して未生成分の自動運転を再開。全停止で無効化したCPU画像ワークフローも元の状態へ復旧 |
| `画像生成の状態確認.command` | ON/OFF、実行中、成功・エラー、画像数と未処理数、容量、GitHub最終反映結果を表示 |
| `全画像生成を停止.command` | Mac側と、GitHubの `free-cpu-scenes.yml` だけを停止。該当する実行中ジョブもキャンセル |

ターミナルではリポジトリ内で以下を実行します。ほかの記事発行・漫画・GitHub Actionsは停止対象に含めません。

~~~bash
python3.11 scripts/mac_scene_control.py status
python3.11 scripts/mac_scene_control.py stop
python3.11 scripts/mac_scene_control.py resume
python3.11 scripts/mac_scene_control.py stop-all
# GitHub CPU画像ワークフローだけを明示的に復旧
python3.11 scripts/mac_scene_control.py resume-cpu
# ショートカットを作り直す
python3.11 scripts/mac_scene_control.py install-shortcuts
~~~

停止は専用アプリ領域の `stopped.json` に永続化します。ワーカー起動時、生成前、push直前に確認し、明示的な再開まで新しい処理を始めません。停止時はPID・起動日時・UID・実行パス・専用プロセスグループを照合し、他のPythonや一般のComfyUIを一括終了しません。モデル・保留画像・既存記事は保持します。

停止要求の直前に既に送信中だったpushはGitHub側で完了する場合があります。その場合も完了SHAを記録し、次回の再開で最新mainと照合します。停止後に新たなpushを始めることは防止し、成功分のoutboxを消しません。認証不足や通信障害でGitHub CPU側を止められなければ、エラーと対象を表示し、成功扱いにしません。

自動実行は毎時45分とログイン時、1回最大4枚。Macがスリープ・電源OFF中は処理できず、復帰・ログイン後に未処理分を確認します。常駐のComfyUIは残さず、処理終了・停止後に専用サーバーと不要PNGを片付けます。生成中は12GiBの空き、1枚700KiB・生成JPEG総量1.5GiBの上限を維持します。

画像は実写風を基本に、利用者の希望に従って多少のアニメ調も許容します。機械検証は破損・寸法・容量・SHA・記事対応を確認し、画風・小道具・オチの完全一致までは保証しません。初回3枚の実生成・目視・本番配信確認を記録した `quality-pilot.json` がない状態では再開できません。

GitHub CPUワークフローは現在のmainでcronが保留されているため、ONへ戻しても新しい定期スケジュールは追加しません。既存のpush／手動起動条件を維持し、Macとは独立して状態を制御します。

## 最小のセットアップ

Apple Silicon、メモリ16GiB以上、空き30GiB以上、Python 3.11〜3.13が必要です。以下は既存のPython 3.11を使う例です。システムPythonは変更しません。

まずGitHubへのローカル認証を確認してください。接続済みChatGPTのGitHub認証はMacのgitとは別です。

~~~bash
export PATH="/Library/Developer/CommandLineTools/usr/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
gh auth status
# 認証が無効な場合のみ、本人がブラウザでログイン
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
~~~

Xcodeの同意画面が出るMacでは、上のPATHでインストール済みCommand Line Toolsのgitを使えます。この処理でXcodeの規約へ自動同意はしません。

最新mainを取得した本リポジトリ内で実行します。まだコピーがない場合は、通常のMacのターミナルで以下を実行してください。同名のフォルダが既にあればcloneは停止し、既存ファイルを上書きしません。Workで検証用に取得したスナップショットではなく、GitHubをoriginとする通常のcloneが必要です。

~~~bash
git clone https://github.com/RollNuts/mihassei-news.git "$HOME/VeripsaScenesRepo" && cd "$HOME/VeripsaScenesRepo"
~~~

~~~bash
# 状態確認のみ。ダウンロード・削除・設定変更はしない
python3.11 scripts/mac_scene_setup.py

# 公式SDXL 1.0のライセンスと用途を確認してから実行
python3.11 scripts/mac_scene_setup.py --install --python python3.11 --download-model --acknowledge-model-license

# まず3本を実際に生成し、検証済みのJPEGだけGitHubへ反映
python3.11 scripts/mac_scene_worker.py --limit 3

# 上が成功してから有効化。毎時45分、ログイン直後に最大4本
python3.11 scripts/mac_scene_setup.py --enable-schedule

# 状態と最終結果を確認
python3.11 scripts/mac_scene_setup.py
~~~

同じ公式モデルが既にある場合はダウンロード指定の代わりに `--model-path "/absolute/path/sd_xl_base_1.0.safetensors"` を使ってください。SHA-256を確認し、シンボリックリンクで再利用します。他のモデルを上書きしません。

## ライセンス・容量・依存関係

- [公式SDXL 1.0モデル](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0)：CreativeML Open RAIL++-M。無償ですが用途の制限があります。[ライセンス本文](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/main/LICENSE.md)を確認してください。モデルは配布せず、匿名の架空人物による創作として表示します。
- ファイル名 `sd_xl_base_1.0.safetensors`、約6.94GB、SHA-256 `31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b`。毎回の起動前にも正当性を検証します。
- 新規のComfyUIは[公式v0.43.0](https://github.com/Comfy-Org/ComfyUI/releases/tag/v0.43.0)に固定。GPL-3.0のローカルソフトです。既存の専用環境は勝手に更新しません。PyTorch・Pillow等は専用venvへ、pipキャッシュなしでインストールします。
- 追加インストールの代替は既存の公式モデル・専用環境の再利用。外部画像APIには切り替えません。
- 専用アプリ：`~/Library/Application Support/VeripsaScenes`。一時PNG：`~/Library/Caches/VeripsaScenes`。
- モデル再利用時もセットアップ前に30GiB、実行中に12GiB＋小さな作業余裕を確保します。依存インストール・モデル取得・生成待機中も空き容量を監視し、不足時は停止します。
- 1回1〜4本、JPEGは1200×675（16:9）、1枚700KiB以下。元の作業場所・専用outbox・一時checkoutを合わせた生成JPEG総量が1.5GiBを超えないよう、コピー前と生成前に容量を予約します。既存の画像を削除して空きを作ることはしません。
- 一時checkoutには既存画像の作業コピーができます。1.5GiBのJPEG上限と12GiBのディスク空き下限は、この作業コピーも含みます。正常終了時には専用の作業コピーだけ片付けます。
- キャッシュの削除は下記コマンドで専用領域だけ行います。アンインストールはまずスケジュールを解除し、必要な再送画像を確認してから専用アプリ領域を手動で取り除きます。既存モデルへのリンク先や写真は削除しません。

## 記事ごとに異なる場面

各記事の `scene_prompt` に、本文のオチを視覚化する英語30〜70語（800文字以内）の具体的な場面を保存します。人物の動作・小道具・場所を指定し、文字・ロゴ・実在人物を入れません。記事自動発行時にこの項目も執筆し、Macは画像生成だけ行います。指定がない旧記事は既存の場面テンプレートを使います。

本文・見出し・要約・場面指示が変われば旧画像は無効となり、次回に生成し直します。画像の解像度、形式、サイズ、SHA-256、記事との対応を検証します。真っ黒などほぼ単色の生成物も拒否します。ただし、人物の手・文字の混入・笑いの分かりやすさは機械検証だけでは保証できません。初回の実生成は目視確認してください。

## 競合・障害からの復旧

1. 最新mainをfetchし、専用の一時worktreeを用意。
2. 以前の成功画像を再送してから、画像のない記事を最大4本生成。
3. 成功画像を専用outbox `VeripsaScenes/pending` に保管。一部失敗しても成功分を反映。
4. push直前に再び最新mainを取得し、内容が一致する画像とmanifestだけを合成。記事追加・他の画像を維持。
5. 通常pushが競合したら最大3回再試行。失敗した画像は次回へ持ち越し。force pushはしない。

ユーザーの作業ブランチ・未保存変更・記事本文には触れません。強制終了で残った専用worktreeも次回に復旧します。記事本文が生成中に編集された場合は古い画像を公開しません。通信障害時も既存写真／SVGで公開を継続します。

Macが停止中は生成できません。ログイン直後と毎時45分に不足分を再確認します。各処理後に専用ComfyUIを終了し、PNGを片付けます。launchdが引き継がないHomebrew／gitのPATHも設定済みです。

タイムアウト・容量不足時はその回の残りの画像をキューへ追加しません。launchdからの停止要求でも子プロセスを終了し、専用PNGを片付けます。セットアップ・生成・キャッシュ削除は同時実行を防止しています。push競合で最新mainを再取得するときも、増えたJPEG分の容量を確認します。壊れた再送用manifestは上書きせず保存し、本番確認では画像のSHA-256も照合します。

## 停止と診断

~~~bash
# スケジュール停止。モデル・記事・JPEGを保持
python3.11 scripts/mac_scene_setup.py --disable-schedule

# 専用の一時キャッシュだけ削除。実行中は拒否
python3.11 scripts/mac_scene_setup.py --cleanup-cache

# 生成予定を確認（モデル、通信、GitHub書き込みは不要）
python3.11 scripts/local_scene_photos.py --dry-run --limit 3
~~~

`last-run.json` が `skipped` のときは `detail` を確認します。認証無効なら `gh auth login`、容量不足なら利用者が保存先を整理してください。生成済みJPEG・記事・他アプリのファイルを自動で削除することはありません。

## 2026-11-03の実機確認

M3 MacBook Air（8CPU／10GPU、16GB）、Python 3.11.15で実施。セットアップ前の空きは215.1GiBでした。既存のComfyUI・画像モデルを検索してから、専用venvと公式ComfyUI v0.43.0を導入しました。SDXL利用規約への本人の明示的な同意を得て、公式モデル1個だけをダウンロードし、6,938,078,334バイトと公式SHA-256の一致を確認しました。システムPython、Xcodeのライセンス同意、他アプリのファイルは変更していません。

ComfyUIのHTTP応答、`Device: mps`、実際のSDXL生成を確認。初回は3記事とも生成に成功し、1枚約63〜92KiB、生成時間は約120〜138秒でした。16GB機ではスワップを使用し、実行中の空きは約199〜200GiBでした。容量の差分にはOSのスワップや同時作業も含みます。

初回は小道具や記事のオチが省略される傾向があり、共通のスタイル指示を短縮し、日本人の架空俳優を強調。3記事の `scene_prompt` も具体的な小道具と動作に調整しました。人物・文字・オチの一致を完全自動で保証する仕組みではなく、形式・容量・記事対応の機械検証と目視品質確認を区別します。

通常のgitはXcode規約未同意で停止しましたが、インストール済みCommand Line Toolsのgitを使えました。Macのgh認証とGitHubへの通常pushも使用できています。モデル・venvの追加取得は不要です。

採用した3枚は1200×675のJPEGです。スパイ記事は複数人物の動作再現が安定しなかったため、脱出後の無人の部屋と縄に絞りました。箱に生じた不要なラベルはローカルの切り抜きで画面外に除き、JPEGとSHAを再検証しています。小道具やオチの再現度には改善の余地があります。

### 最終運用状態（2026-11-03）

- インストール、MPS起動、公式モデル照合：成功。モデル1個、専用領域約8.5GiB。
- 3記事（夕飯ガチャ／700人特番／スパイ）のJPEGをmainと本番へ反映。各1200×675、約61〜101KiB、合計約229KiB。トップ・記事・一覧・OGP・RSSの参照と、本番JPEGのSHA一致を確認。
- PC幅1280pxとスマホ幅390pxで表示確認。スマホの横はみ出しなし。ロード失敗時のSVG復旧テストも成功。
- Macワーカーの最新main取得→追加1枚生成→検証→通常push→後片付けは成功。ただし生成されたお笑い記事の画像はアニメ調だったため、承認manifestから外してSVGへ戻しました。JPEG自体は削除していません。
- 実写指定を冒頭へ移す追加実験も、お笑い記事の画風・アイス記事の小道具が安定せず不採用。実験PR #132は閉じ、未検証の変更はmainへ入れていません。試作は専用アプリの `quality-review-20261103` に少量保管。
- **launchdは未有効化。全記事の狙った品質による完全自動運用は未完了です。** `last-run.json` は `quality-review-required`。正常終了だけで画質合格とはせず、意図的にスケジュールの有効化条件を満たさない状態にしています。
- 現在は18記事中3記事が承認済み生成画像、残りは従来の承認済み写真／SVG。毎日の記事発行処理は変更していません。試験用ComfyUIは停止し、専用出力の不要PNGは残っていません。
- 追加の有料API・画像API・GPU契約は0件。モデル再ダウンロード、他アプリや既存写真の削除、force pushは行っていません。

変更箇所は `scripts/mac_scene_worker.py`（容量を増やさないエラー診断）、`scripts/local_scene_sync.py`（画像の出所維持）、`scripts/local_scene_photos.py`（短いスタイル指示）、対象3記事の `scene_prompt`、生成JPEGとmanifest、関連テスト、本書です。実装はPR #120・#121・#129で反映しました。

実行した主な検証コマンド：

~~~bash
python -m unittest discover -s tests       # 最終242件成功
node --test tests/*.test.cjs               # 54件成功
python scripts/build.py                   # 18記事ビルド成功
npx --yes wrangler@4 deploy --dry-run      # パッケージ検証成功
python scripts/mac_scene_worker.py --limit 1  # 実生成・通常push成功、目視で不採用
python scripts/check_live.py              # 公開記事・RSS等の検証成功
python scripts/check_share_cards.py       # Twitterbot用カード6件成功
~~~

PR #129の画像配信を確認した本番コミットは `f51290d13b22949959ecf96f8089b38a31ff65fc`。追加の実機ワーカー試験のコミットは `28da49c`、その不採用画像は後続コミットで承認解除しています。公開されたバイト列の確認と、任意のCloudflareステータス表示は区別しています。

残る作業は、指定した実写の画風と視覚的なオチが複数記事で安定するモデル／生成条件を確立することです。単に `last-run.json` をokへ書き換えて定期運用を開始しないでください。Macの再インストールや公式モデルの追加ダウンロードは不要です。
