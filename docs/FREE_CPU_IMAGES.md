# 未発生新聞：Macなしでも生成する無料CPU画像ルート

記事データのmain更新時と、毎時17分に未生成画像を確認します。Macがメモリ保護・スリープで生成できない間も、公開リポジトリの無料CPUで最大3枚ずつ補います。対象0件ならモデルを取得せず終了します。手動実行の標準はプレビュー保存であり、公開しません。

生成JPEGがmainへ保存されても、その時点では読者向けの掲載採用を意味しません。`scripts/visual_review.py`で未検品を確認し、実画像と本文を照合して`content/visual_reviews.json`へ現在の記事・画像のSHAを記録します。公開ビルドは一致する合格記録を持つJPEGと派生動画だけを採用し、残りはイラスト等を表示します。生成済みJPEGを再生成し続ける必要はありません。検品と再試験の手順は[VISUAL_OPERATIONS.md](VISUAL_OPERATIONS.md)を参照。

公開リポジトリ `RollNuts/mihassei-news` の GitHub Actions 標準 Linux CPU ランナーで、公開された実写向けモデルの**重みをダウンロードして自前推論**する。外部画像生成API、GPT API、ChatGPT追加クレジット、有料GPU、Hugging Face Inference Endpoint、Cloudflareの追加サービスは使わない。

## できること／できないこと

- `.github/workflows/free-cpu-scenes.yml` は新着時・定時・手動とも1回1〜3枚まで。無料GitHub Actionsの**公開リポジトリ用標準CPU**に厳格限定。プライベート化・フォーク・PR からは生成しない。対象が0件ならランタイムやモデルをダウンロードせず終了する。画像だけのpushでは起動せず、生成・公開を再帰させない。
- 同ワークフローのSummaryは、`生成対象0件`と`読者向け画像が十分`を同義にしない。`scripts/visual_review.py`の既存判定を使い、承認済みJPEG・専用SVG・承認写真・汎用SVG、未検品JPEG、品質保留JPEGを分けて表示する。Mac停止中でも、CPU生成のidleと画像回収待ちを区別して引き継ぐ。
- `scripts/github_cpu_scenes.py` は最新記事の**画像未生成分だけ**処理。既存のMac製写真を上書きしない。ComfyUI/Macがまだ動かない時だけでも記事に再現画像を付けられる。
- 記事の `visual_strategy` が `illustration` の場合は、通常実行・`retry_held`・ローカルの`--force`でもCPU/Mac生成キューへ入れない。2026-11-06 22:00 JST以降の新作では `illustration_reason` を必須とし、文字/UI、厳密な人数や不在、複雑な人物関係、比較レイアウト、実在キャラクター再現を避ける安全上の制約のいずれかが風刺成立に必要な時だけ使う。**写真向けの scene_prompt を書いたまま illustration に振り分けない。** 再び写真を試すなら、記事側を `cpu-photo` に戻し、場面指示を変えて新しい記事指紋で扱う。
- CPU用の場面指示は主役・小道具・動作を最初の短い完全文（目安35語以内）に入れ、背景は後続の別文にする。77トークンに収まる完全文だけを使い、主役の指示そのものが長すぎる場合は生成を見送る。文字の多い店頭や人物が小道具を隠す構図は、無地の背景・小道具の接写へ簡略化する。**画風の面白さは足さず、documentary / press photo の共通ラッパーに任せる。** cinematic、anime、3D render、glossy product photo、dramatic lighting など広告・CG寄りの指示を個別sceneへ入れない。
- モデルは実写向けの[Realistic Vision 5.1](https://huggingface.co/SG161222/Realistic_Vision_V5.1_noVAE)に、公式[SD1.5 LCM LoRA](https://huggingface.co/latent-consistency/lcm-lora-sdv1-5)と推奨MSE VAEを組み合わせ、CPU・6ステップ・CFG 2で生成する。各リビジョンと5つの重みSHA256を `scripts/cpu_photo_model.py` に固定し、読み込み前に照合する。基盤のCreativeML Open RAIL-M、LoRAのOpen RAIL++、VAEのMITの本文・制限を確認済み。旧DreamShaperの承認済み写真は維持する。旧native LCMはnegative_promptを無視するが、新経路のStableDiffusionPipelineではCFGによるアニメ・CG・文字の除外指定が有効。除外指定も77トークンを超えれば生成を止める。旧モデルと同じ安全チェッカーを明示的に保持し、拒否された画像は採用しない。
- JPEGは1枚700KiB以下、1200×675、アーカイブ1.5GiB上限。ハッシュ、記事本文・見出し・画像指示の指紋、モデル名、生成元の一致を確認して初めて記事やOGPに表示する。
- CPUの速度・生成品質はSDXLをMacのGPUで動かす場合に及ばない可能性が高い。**実際の生成成功・本番表示・見栄えはGitHub Actions実行ログと公開URLで確認するまで未証明**。異常な画像を自動公開しないための安全検証はあるが、意味が伝わるかの人間のレビューは自動化していない。

## 初回確認

1. GitHubの [Actions](https://github.com/RollNuts/mihassei-news/actions) → `Free CPU staged-news photos (public repository only)` → `Run workflow` で`limit=1〜3`を選び、`publish`はOFFのまま生成する。必要なら`slug`で記事を限定する。
2. `cpu-scenes-review-<実行ID>` artifactを開き、記事・小道具・明るさ・文字混入・人物に加えて、**実写の報道写真に見えるかを最初に目視する**。アニメ、イラスト、3DCG、プラスチック肌、広告・映画ポスター風の演出が見えるものは、内容が合っていても不採用。保存するのは今回成功したJPEG最大3枚と、対応する記事原本・manifestだけ。モデルやPNGは保存せず、artifactは1日で期限切れになる。
3. 生成済みJPEGを承認後、最新mainの記事との指紋照合を通して画像とmanifestだけを通常pushする。自動公開を使う場合も同じ照合を実行し、改稿後の古い画像を公開しない。送信競合は最大3回まで再試行し、失敗時もartifactから確認・回収できる。
4. Cloudflare反映後、公開JPEGのHTTP 200・SHA、記事・一覧・OGP・RSSを確認する。画像の絵作りとオチまで合うかは数値検査だけでは保証しない。

目視で記事と違う物体になった画像は、manifestの`rejected`へ記事指紋と理由を記録して保留する。Mac・CPUとも通常の生成対象から外れ、記事はSVGで公開を続ける。現在の呼び鈴の記事はこの保留対象。新しい正しいJPEGを実際に生成・確認するまで、古い画像の指紋やseedを変更して解除しない。

品質保留の再試験は`slug`の明示と`retry_held=true`が必要で、確認用artifactだけを作り自動公開しない。すでに有効な画像がある記事を再生成する操作には使えない。保留解除には目視確認した本当に新しいJPEGを使い、旧JPEGの指紋やseedを付け替えない。通常の定期処理は保留記事を除外する。

## 運用上の注意

- 公開標準ランナーの無償利用とプライベートランナーの課金条件はGitHubの規約次第で変更される。公開リポジトリ用ジョブのガードを外さない。
- 1回ごとに必要な重み約5.6GBを取得する場合がある（旧モデル一式や不要なVAEを重複取得しない）。Macへはモデルを追加せず、使い捨てランナー内だけで取得する。有料ストレージや大容量Actionsキャッシュは使用しない。確認用artifactは最大3枚・1日保持に制限する。
- 定時生成の中止は `.github/workflows/free-cpu-scenes.yml` の `schedule:` を削除またはワークフローを無効化。既存写真・記事・Mac用画像生成は維持される。
- **GitHubのCPU方式が失敗しても追加サービスへ自動的に切り替えない。** 推論時は最大55分で停止。画像生成エラーで記事公開を止めない。
- 毎時の定時処理は1回最大3枚で、新着記事のmain更新時にも不足分だけ処理する。GitHubトークン由来などでpush起動しない更新も、次の定時処理で回収する。Macとの同時生成や記事改稿があっても最新mainと再照合し、有効な既存画像は上書きしない。

### 検証コマンド

```bash
# オフラインで画像構図と未生成記事の候補のみ表示
python3 scripts/github_cpu_scenes.py --dry-run --limit 3

# 外部アクセスなしのゲート試験
python3 -m unittest discover -s tests -p 'test_cpu_scenes.py' -v
```
