# 未発生新聞：Macなしでも生成する無料CPU画像ルート

記事データのmain更新時と、毎時17分に未生成画像を確認します。Macがメモリ保護・スリープで生成できない間も、公開リポジトリの無料CPUで最大3枚ずつ補います。対象0件ならモデルを取得せず終了します。手動実行の標準はプレビュー保存であり、公開しません。

生成JPEGがmainへ保存されても、その時点では読者向けの掲載採用を意味しません。`scripts/visual_review.py`で未検品を確認し、実画像と本文を照合して`content/visual_reviews.json`へ現在の記事・画像のSHAを記録します。公開ビルドは一致する合格記録を持つJPEGと派生動画だけを採用し、残りはイラスト等を表示します。生成済みJPEGを再生成し続ける必要はありません。検品と再試験の手順は[VISUAL_OPERATIONS.md](VISUAL_OPERATIONS.md)を参照。

公開リポジトリ `RollNuts/mihassei-news` の GitHub Actions 標準 Linux CPU ランナーで、公開された実写向けモデルの**重みをダウンロードして自前推論**する。外部画像生成API、GPT API、ChatGPT追加クレジット、有料GPU、Hugging Face Inference Endpoint、Cloudflareの追加サービスは使わない。

## できること／できないこと

- `.github/workflows/free-cpu-scenes.yml` は新着時・定時・手動とも1回1〜3枚まで。無料GitHub Actionsの**公開リポジトリ用標準CPU**に厳格限定。プライベート化・フォーク・PR からは生成しない。対象が0件ならランタイムやモデルをダウンロードせず終了する。画像だけのpushでは起動せず、生成・公開を再帰させない。
- 同ワークフローのSummaryは、`生成対象0件`と`読者向け画像が十分`を同義にしない。`scripts/visual_review.py`の既存判定を使い、承認済みJPEG・専用SVG・承認写真・汎用SVG、未検品JPEG、品質保留JPEGを分けて表示する。Mac停止中でも、CPU生成のidleと画像回収待ちを区別して引き継ぐ。
- `scripts/github_cpu_scenes.py` は最新記事の**画像未生成分だけ**処理。既存のMac製写真を上書きしない。ComfyUI/Macがまだ動かない時だけでも記事に再現画像を付けられる。
- 記事の `visual_strategy` が `illustration` の場合は、通常実行・`retry_held`・ローカルの`--force`でもCPU/Mac生成キューへ入れない。2026-11-06 22:00 JST以降の新作では `illustration_reason` を必須とし、文字/UI、厳密な人数や不在、複雑な人物関係、比較レイアウト、実在キャラクター再現を避ける安全上の制約のいずれかが風刺成立に必要な時だけ使う。**写真向けの scene_prompt を書いたまま illustration に振り分けない。** 既刊を写真化する場合は、下記のプレビューで本当に新しいJPEGを作って目視し、合格した変更と一緒に `cpu-photo` へ戻す。
- CPU用の場面指示は主役・小道具・動作を最初の短い完全文（目安35語以内）に入れ、背景は後続の別文にする。台所や光だけの導入文を先頭に置かず、主役と小道具を最初の文へ含める。長い人物説明を後続文にすると、その文ごと省略されて背景だけの写真になるため、必要なら本文にある小道具の接写へ絞る。77トークンに収まる完全文だけを使い、主役の指示そのものが長すぎる場合は生成を見送る。文字の多い店頭や人物が小道具を隠す構図は、無地の背景・小道具の接写へ簡略化する。**画風の面白さは足さず、documentary / press photo の共通ラッパーに任せる。** cinematic、anime、3D render、glossy product photo、dramatic lighting など広告・CG寄りの指示を個別sceneへ入れない。
- モデルは実写向けの[Realistic Vision 5.1](https://huggingface.co/SG161222/Realistic_Vision_V5.1_noVAE)と推奨MSE VAE。作者推奨のDPM++ 2M Karras・CFG 7を使い、24ステップで場面の再現を試す（速度・画質は実行ごとに確認）。短い高速LCMでは必要な小道具が欠けたため、新規生成にLCMアダプターは使わない。各リビジョンと4つの重みSHA256を `scripts/cpu_photo_model.py` に固定し、読み込み前に照合する。基盤のCreativeML Open RAIL-M、VAEのMITの本文・制限を確認済み。過去の生成記録と承認写真は維持し、新規は `origin: github-cpu-sd15` と正確な推論設定を記録する。CFGのアニメ・CG・文字の除外指定も77トークンを超えれば停止し、従来の安全チェッカーを明示的に保持する。
- JPEGは1枚700KiB以下、1200×675、アーカイブ1.5GiB上限。ハッシュ、記事本文・見出し・画像指示の指紋、モデル名、生成元の一致を確認して初めて記事やOGPに表示する。
- CPUの速度・生成品質はSDXLをMacのGPUで動かす場合に及ばない可能性が高い。**実際の生成成功・本番表示・見栄えはGitHub Actions実行ログと公開URLで確認するまで未証明**。異常な画像を自動公開しないための安全検証はあるが、意味が伝わるかの人間のレビューは自動化していない。

## 初回確認

1. GitHubの [Actions](https://github.com/RollNuts/mihassei-news/actions) → `Free CPU staged-news photos (public repository only)` → `Run workflow` で`limit=1〜3`を選び、`publish`はOFFのまま生成する。必要なら`slug`で記事を限定する。
2. `cpu-scenes-review-<実行ID>` artifactを開き、記事・小道具・明るさ・文字混入・人物に加えて、**実写の報道写真に見えるかを最初に目視する**。アニメ、イラスト、3DCG、プラスチック肌、広告・映画ポスター風の演出が見えるものは、内容が合っていても不採用。保存するのは今回成功したJPEG最大3枚と、対応する記事原本・manifestだけ。モデルやPNGは保存せず、artifactは1日で期限切れになる。
3. 生成済みJPEGを承認後、最新mainの記事との指紋照合を通して画像とmanifestだけを通常pushする。自動公開を使う場合も同じ照合を実行し、改稿後の古い画像を公開しない。送信競合は最大3回まで再試行し、失敗時もartifactから確認・回収できる。
4. Cloudflare反映後、公開JPEGのHTTP 200・SHA、記事・一覧・OGP・RSSを確認する。画像の絵作りとオチまで合うかは数値検査だけでは保証しない。

目視で記事と違う物体になった画像は、manifestの`rejected`へ記事指紋と理由を記録して保留する。Mac・CPUとも通常の生成対象から外れ、記事はSVGで公開を続ける。現在の呼び鈴の記事はこの保留対象。新しい正しいJPEGを実際に生成・確認するまで、古い画像の指紋やseedを変更して解除しない。

品質保留の再試験は`slug`の明示と`retry_held=true`が必要で、確認用artifactだけを作り自動公開しない。すでに有効な画像がある記事を再生成する操作には使えない。保留解除には目視確認した本当に新しいJPEGを使い、旧JPEGの指紋やseedを付け替えない。通常の定期処理は保留記事を除外する。

別モデルの成人ポートレート試作は、同じ手動Actionの`preview_model=sdxl`で写真向けfine-tuneのJuggernautXL v9（SDXL系）を使う限定経路とする。対象は`indoor-beach-friends-exchange-ripples`の固定原稿だけで、`limit=1`・`retry_held=true`・`publish=false`・backfill空を必須とし、mainまたは指定の`codex/sdxl-preview-20261114`ブランチの正確なcommitで実行する。元本文を保ち写真指示だけを頭から太もも中央までの構図へ置き換えた候補記事と、元記事・候補それぞれの指紋、JPEG、実行receiptだけを1日保持する。固定モデル・モデルカード条件とライセンス本文・部品ごとのSHAは`scripts/sdxl_preview_model.py`、メモリ・容量・停止保護は`scripts/sdxl_scene_preview.py`を正とする。初回20ステップはモデルカードの30〜40推奨より少ない限定試験で、実品質は未確認。通常モデルや採用manifestへ接続せず、所要時間・RAM・写真品質は実runと実画像QAで確認してから採用を判断する。

既刊の写真化プレビューは、さらに `backfill_scene` に新しい場面指示を渡し、`limit=1`・`publish=false`で手動実行する。通常キューが残る場合や公開を指定した場合は生成せず、日次上限もモデル取得前に判定する。生成物は元の記事を束縛する `source_article_sha256` 付きのartifactだけに保存し、実行中のcheckoutや本番素材は上書きしない。いつ試し、どう採用するかは[VISUAL_OPERATIONS.md](VISUAL_OPERATIONS.md)を正本とする。

文章だけの試作で必要な物が消える場合は、同じプレビューに `backfill_reference=true`（CLIでは`--backfill-reference`）を加え、現在の記事に合う専用SVGを構図の参照にできる。原記事の承認済みSVGを先に検証し、CairoSVG 2.7.1でメモリ内の800×448 RGBへ変換する。既存RVパイプラインの部品・4つの重み・安全フィルターを再利用し、追加モデルやControlNetは取得しない。初期画像の強さは0.65、指定24ステップのうち実際の変換は15ステップ。GitHub標準CPUで生成・形式検証まで確認済み。ただし初回のノート試作は参照SVGの穴が消えて不採用となり、形状の維持や品質改善は未確認。処理速度の一般化とメモリ使用量の評価もまだ行っていない。

この参照経路は手動の既刊プレビューだけで、既存の日次上限・新着優先・再実行禁止を共有する。通常の新着生成の既定値は変えず、参照bundleのCLI自動公開も拒否する。生成元は `github-cpu-sd15-img2img` として従来と区別し、固定設定、元記事指紋、参照SVGと実RGBのSHA、レンダラーの版を残す。参照SVGの承認を写真の承認に流用せず、実JPEGを本文と見比べてから通常PRで採用する。過去の参照SVGを後から修正しても、それだけで採用写真が失効する判定は加えない。

採用された写真は本文・一覧・OGP・Twitter画像・RSSで同じJPEGを使い、画像URLにはSHA由来の版を付ける。専用SVGの共有PNGは[画像運用](VISUAL_OPERATIONS.md)で原本と結び付けた派生ファイルを使う。X共有・リンクコピー・記事自動紹介では、実際に選ぶ検証済みJPEGまたは専用PNGのSHA先頭12文字を `image` として記事URLへ添える。同じ画像なら共有URLも固定し、未検品・失効画像の版は使わない。canonical・OG URL・RSSの記事URLは従来どおり保つ。既存SNS投稿のキャッシュが即時更新される保証はない。

## 運用上の注意

- 公開標準ランナーの無償利用とプライベートランナーの課金条件はGitHubの規約次第で変更される。公開リポジトリ用ジョブのガードを外さない。
- 1回ごとに必要な重み約5.5GBを取得する場合がある（旧モデル一式や不要なVAEを重複取得しない）。Macへはモデルを追加せず、使い捨てランナー内だけで取得する。有料ストレージや大容量Actionsキャッシュは使用しない。確認用artifactは最大3枚・1日保持に制限する。
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
