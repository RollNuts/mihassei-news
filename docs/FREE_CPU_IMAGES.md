# 未発生新聞：Macなしでも生成する無料CPU画像ルート

定時実行は画像の目視確認まで保留しています。手動実行の標準はプレビュー保存であり、公開しません。Macがメモリ保護で生成を見送る間も、公開リポジトリの無料CPUで実画像を試験し、見た目と本番表示を確認してから定期公開を有効化します。

公開リポジトリ `RollNuts/mihassei-news` の GitHub Actions 標準 Linux CPU ランナーで、公開された軽量LCMモデルの**重みをダウンロードして自前推論**する。外部画像生成API、GPT API、ChatGPT追加クレジット、有料GPU、Hugging Face Inference Endpoint、Cloudflareの追加サービスは使わない。

## できること／できないこと

- `.github/workflows/free-cpu-scenes.yml` は手動実行で1回1〜3枚まで。無料GitHub Actionsの**公開リポジトリ用標準CPU**に厳格限定。プライベート化・フォーク・PR からは生成しない。対象が0件ならランタイムやモデルをダウンロードせず終了する。
- `scripts/github_cpu_scenes.py` は最新記事の**画像未生成分だけ**処理。既存のMac製写真を上書きしない。ComfyUI/Macがまだ動かない時だけでも記事に再現画像を付けられる。
- モデル: [SimianLuo/LCM_Dreamshaper_v7](https://huggingface.co/SimianLuo/LCM_Dreamshaper_v7)の既存リビジョンを固定して再利用する。LCMのカード記載はMITだが、基盤[DreamShaper 7](https://huggingface.co/Lykon/dreamshaper-7)は[CreativeML Open RAIL-M](https://huggingface.co/spaces/CompVis/stable-diffusion-license/blob/main/license.txt)であり、その利用制限も確認する。モデルは公開・非ゲートで、外部推論APIを呼ばずGitHubのCPU内部で実行する。公開するのは架空人物による風刺JPEGで、モデル重みは配布しない。
- JPEGは1枚700KiB以下、1200×675、アーカイブ1.5GiB上限。ハッシュ、記事本文・見出し・画像指示の指紋、モデル名、生成元の一致を確認して初めて記事やOGPに表示する。
- CPUの速度・生成品質はSDXLをMacのGPUで動かす場合に及ばない可能性が高い。**実際の生成成功・本番表示・見栄えはGitHub Actions実行ログと公開URLで確認するまで未証明**。異常な画像を自動公開しないための安全検証はあるが、意味が伝わるかの人間のレビューは自動化していない。

## 初回確認

1. GitHubの [Actions](https://github.com/RollNuts/mihassei-news/actions) → `Free CPU staged-news photos (public repository only)` → `Run workflow` で`limit=1〜3`を選び、`publish`はOFFのまま生成する。必要なら`slug`で記事を限定する。
2. `cpu-scenes-review-<実行ID>` artifactを開き、記事・小道具・明るさ・文字混入・人物を目視する。保存するのは今回成功したJPEG最大3枚と、対応する記事原本・manifestだけ。モデルやPNGは保存せず、artifactは1日で期限切れになる。
3. 生成済みJPEGを承認後、最新mainの記事との指紋照合を通して画像とmanifestだけを通常pushする。自動公開を使う場合も同じ照合を実行し、改稿後の古い画像を公開しない。送信競合は最大3回まで再試行し、失敗時もartifactから確認・回収できる。
4. Cloudflare反映後、公開JPEGのHTTP 200・SHA、記事・一覧・OGP・RSSを確認する。画像の絵作りとオチまで合うかは数値検査だけでは保証しない。

品質保留の再試験は`slug`の明示と`retry_held=true`が必要で、確認用artifactだけを作り自動公開しない。すでに有効な画像がある記事を再生成する操作には使えない。保留解除には目視確認した本当に新しいJPEGを使い、旧JPEGの指紋やseedを付け替えない。通常の定期処理は保留記事を除外する。

## 運用上の注意

- 公開標準ランナーの無償利用とプライベートランナーの課金条件はGitHubの規約次第で変更される。公開リポジトリ用ジョブのガードを外さない。
- 1回ごとにモデル約数GBを取得する場合がある。Macへはモデルを追加せず、使い捨てランナー内だけで取得する。有料ストレージや大容量Actionsキャッシュは使用しない。確認用artifactは最大3枚・1日保持に制限する。
- 定時生成の中止は `.github/workflows/free-cpu-scenes.yml` の `schedule:` を削除またはワークフローを無効化。既存写真・記事・Mac用画像生成は維持される。
- **GitHubのCPU方式が失敗しても追加サービスへ自動的に切り替えない。** 推論時は最大55分で停止。画像生成エラーで記事公開を止めない。
- 新規作成は原則3枚×4回/日の上限。記事が多く発行されて追いつかない場合はモデル変更や大量の計算を無断で追加せず、必要な本数と画質を調整する。

### 検証コマンド

```bash
# オフラインで画像構図と未生成記事の候補のみ表示
python3 scripts/github_cpu_scenes.py --dry-run --limit 3

# 外部アクセスなしのゲート試験
python3 -m unittest discover -s tests -p 'test_cpu_scenes.py' -v
```
