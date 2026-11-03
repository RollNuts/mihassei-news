# Macでの実写コント画像生成

GitHub側のコードだけではMacにモデルは入りません。画像がなくても、承認済み写真またはSVGで毎日の発行は続きます。外部の画像API・有料GPU・追加ChatGPTクレジットは使用しません。

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
