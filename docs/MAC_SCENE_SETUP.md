# 未発生新聞：Macの無料ローカル画像生成セットアップ

## Mac用の容量制限付きローカル生成（手元のMacで有効化）

上のComfyUI画像生成機能に、Mac向けの安全なインストーラと定期実行用 `launchd` 設定を追加した。**GitHubにマージしただけではMac本体へソフトはインストールされない。** 実際にMacで明示的にコマンドを実行して有効化する必要がある。ChatGPTの追加課金、画像APIの従量課金、外部GPUは使わない。

**前提:** Apple Silicon Mac、メモリ16GiB以上、Python 3.11–3.13（`python3` で見つからなければ `--python python3.12` などを指定）、gitがインストール済み、モデルを新規取得するなら空き30GiB以上。SDXL 1.0のOpenRAIL++の条件と用途を確認すること。画像は架空のコント再現であり、実際の事件や人物の報道写真ではない。

~~~bash
# 1. Macにコピーした本リポジトリ内で最新コードを取得
git pull --ff-only origin main

# 2. ストレージと設定を確認するだけ（ダウンロードも変更もなし）
python3 scripts/mac_scene_setup.py

# 3-A. 公式モデルを1本だけ取得してセットアップ（約6.94GB）
# モデルのライセンスを確認した場合のみ実行
python3 scripts/mac_scene_setup.py --install --download-model --acknowledge-model-license

# 3-B. すでに同じ公式モデルがある場合は重複ダウンロードせず再利用
# python3 scripts/mac_scene_setup.py --install --model-path "/path/to/sd_xl_base_1.0.safetensors" --acknowledge-model-license

# 4. Macのlaunchdに定期実行を登録（新着記事を4本ずつ、1日4枠）
python3 scripts/mac_scene_setup.py --enable-schedule

# 任意: その場で1枚だけ試す
# python3 scripts/mac_scene_worker.py --limit 1

# 現在の状態・最終実行結果・画像容量
python3 scripts/mac_scene_setup.py

# 停止（生成済み画像・モデルは保持）
python3 scripts/mac_scene_setup.py --disable-schedule

# 専用キャッシュだけ掃除（既存画像・モデルは保持）
python3 scripts/mac_scene_setup.py --cleanup-cache
~~~

**上限・保護:** アプリ本体は `~/Library/Application Support/VeripsaScenes`、一時ファイルは `~/Library/Caches/VeripsaScenes` に分離。モデルは公式SHA-256で検証し、ダウンロードは最大7.5GB。新規セットアップ前の空き容量は30GiB、既存モデルを再利用するときは20GiBが下限。生成中は12GiBを下回る前に停止する。生成は1回最大4枚（毎日4枠）、JPEGは**1枚700KiB以下**、生成済みJPEGの合計が**1.5GiB**に達したら停止。完了後は専用作業フォルダの一時PNGだけを削除し、モデル・既存写真・他ファイルには触れない。GitHubへのpushは`--publish`を明示したワーカーからのみ行い、強制pushしない。失敗しても従来のSVGで記事公開を継続する。

**実機依存:** Python/PyTorchのApple MPS対応、GitHubへの認証、ComfyUIの起動とモデルライセンスによっては失敗する。Macの電源が切れている間は実行できない。**全記事に写真が必ず付くことを保証する機能ではない。** 写真未生成なら既存写真／SVGを表示する。
