# 未発生新聞：明示した記事だけを試す無料CPU画像ルート

通常の写真制作と担当は[OPERATIONS.md](OPERATIONS.md)を正本とします。このCPU経路は、記事slugを明示した `workflow_dispatch` だけで使う手動補助です。main更新や時刻による自動生成は行いません。標準は `limit=1`・`publish=false` のレビュー用保存で、記事未指定はランタイム・モデル取得前に拒否します。

生成JPEGがmainへ保存されても、その時点では読者向けの掲載採用を意味しません。`scripts/visual_review.py`で未検品を確認し、実画像と本文を照合して`content/visual_reviews.json`へ現在の記事・画像のSHAを記録します。公開ビルドは一致する合格記録を持つJPEGと派生動画だけを採用し、残りはイラスト等を表示します。生成済みJPEGを再生成し続ける必要はありません。検品と再試験の手順は[VISUAL_OPERATIONS.md](VISUAL_OPERATIONS.md)を参照。

公開リポジトリ `RollNuts/mihassei-news` の GitHub Actions 標準 Linux CPU ランナーで、公開された実写向けモデルの**重みをダウンロードして自前推論**する。外部画像生成API、GPT API、ChatGPT追加クレジット、有料GPU、Hugging Face Inference Endpoint、Cloudflareの追加サービスは使わない。

## できること／できないこと

- `.github/workflows/free-cpu-scenes.yml` はmain上の手動実行と正確な記事slugが必須。既存の1〜3枚のバッチ上限を維持しますが、指定した1記事だけを選びます。無料GitHub Actionsの**公開リポジトリ用標準CPU**に厳格限定し、プライベート化・フォーク・PR・push・定時実行からは生成しません。対象が0件ならランタイムやモデルをダウンロードせず終了します。
- 同ワークフローのSummaryは、`生成対象0件`と`読者向け画像が十分`を同義にしない。`scripts/visual_review.py`の既存判定を使い、承認済みJPEG・専用SVG・承認写真・汎用SVG、未検品JPEG、品質保留JPEGを分けて表示する。Mac停止中でも、CPU生成のidleと画像回収待ちを区別して引き継ぐ。
- `scripts/github_cpu_scenes.py` は指定記事の**画像未生成分だけ**処理し、有効な既存写真を上書きしません。CLIの生成にも明示 `--slug` と手動GitHub実行のガードが必要です。オフラインの `--dry-run` 一覧は記事未指定でも使えます。
- 記事の `visual_strategy` が `illustration` の場合は、通常実行・`retry_held`・ローカルの`--force`でもCPU/Mac生成キューへ入れない。2026-11-06 22:00 JST以降の新作では `illustration_reason` を必須とし、文字/UI、厳密な人数や不在、複雑な人物関係、比較レイアウト、実在キャラクター再現を避ける安全上の制約のいずれかが風刺成立に必要な時だけ使う。**写真向けの scene_prompt を書いたまま illustration に振り分けない。** 既刊を写真化する場合は、下記のプレビューで本当に新しいJPEGを作って目視し、合格した変更と一緒に `cpu-photo` へ戻す。
- CPU用の場面指示は主役・小道具・動作を最初の短い完全文（目安35語以内）に入れ、背景は後続の別文にする。台所や光だけの導入文を先頭に置かず、主役と小道具を最初の文へ含める。長い人物説明を後続文にすると、その文ごと省略されて背景だけの写真になるため、必要なら本文にある小道具の接写へ絞る。77トークンに収まる完全文だけを使い、主役の指示そのものが長すぎる場合は生成を見送る。文字の多い店頭や人物が小道具を隠す構図は、無地の背景・小道具の接写へ簡略化する。**画風の面白さは足さず、documentary / press photo の共通ラッパーに任せる。** cinematic、anime、3D render、glossy product photo、dramatic lighting など広告・CG寄りの指示を個別sceneへ入れない。
- モデルは実写向けの[Realistic Vision 5.1](https://huggingface.co/SG161222/Realistic_Vision_V5.1_noVAE)と推奨MSE VAE。作者推奨のDPM++ 2M Karras・CFG 7を使い、24ステップで場面の再現を試す（速度・画質は実行ごとに確認）。短い高速LCMでは必要な小道具が欠けたため、新規生成にLCMアダプターは使わない。各リビジョンと4つの重みSHA256を `scripts/cpu_photo_model.py` に固定し、読み込み前に照合する。基盤のCreativeML Open RAIL-M、VAEのMITの本文・制限を確認済み。過去の生成記録と承認写真は維持し、新規は `origin: github-cpu-sd15` と正確な推論設定を記録する。CFGのアニメ・CG・文字の除外指定も77トークンを超えれば停止し、従来の安全チェッカーを明示的に保持する。
- JPEGは1枚700KiB以下、1200×675、アーカイブ1.5GiB上限。ハッシュ、記事本文・見出し・画像指示の指紋、モデル名、生成元の一致を確認して初めて記事やOGPに表示する。
- CPUの速度・生成品質はSDXLをMacのGPUで動かす場合に及ばない可能性が高い。**実際の生成成功・本番表示・見栄えはGitHub Actions実行ログと公開URLで確認するまで未証明**。異常な画像を自動公開しないための安全検証はあるが、意味が伝わるかの人間のレビューは自動化していない。

## 初回確認

1. GitHubの [Actions](https://github.com/RollNuts/mihassei-news/actions) → `Free CPU staged-news photos (public repository only)` → `Run workflow` で対象の `slug` を必ず入力し、`limit=1`・`publish`はOFFのまま生成する。通常制作からこの補助へ渡す判断は[OPERATIONS.md](OPERATIONS.md)に従う。
2. 実行Summaryの`cpu-image-candidate/<slug>/<元記事SHA先頭16桁>-<実行ID>`ブランチにあるJPEG（または`cpu-scenes-review-<実行ID>` artifact）を開き、記事・小道具・明るさ・文字混入・人物に加えて、**実写の報道写真に見えるかを最初に目視する**。アニメ、イラスト、3DCG、プラスチック肌、広告・映画ポスター風の演出が見えるものは、内容が合っていても不採用。保存するのは今回成功した対象1記事のJPEGと、対応する記事原本・manifestだけ。レビュー専用の`publish=false`で成功した候補は、元JPEG・export時のmanifestと記事コピーのバイト列・元記事snapshot・各SHAを専用ブランチへ保存し、Gitから再読して照合する。採用記録、main更新、PR作成は行わない。モデルやPNGは保存せず、artifactは1日で期限切れになる。
3. 目視合格後の採用は[VISUAL_OPERATIONS.md](VISUAL_OPERATIONS.md)に従い、最新mainとの照合と必要な採用記録を通常PRで反映する。保存成功だけで採用や本番公開の完了としない。
4. Cloudflare反映後、公開JPEGのHTTP 200・SHA、記事・一覧・OGP・RSSを確認する。画像の絵作りとオチまで合うかは数値検査だけでは保証しない。

保存receiptの`review_only`は保存用途であり採否を示さない。既存のSHA一致採否は[VISUAL_OPERATIONS.md](VISUAL_OPERATIONS.md)に従い保持する。同じrunの再実行は、計画・モデル取得より先に既存候補ブランチをSHAで再照合する。ブランチ保存が未確認なら同じrunの既存artifactだけを回収し、元バイトがなければ停止する。再実行で新規生成せず、既存refの更新・削除・forceも行わない。main本文が後から変わった候補は元記事SHAのまま保持し、Summaryに`stale`と示す。現本文への採用には改めて原記事との差と実画像の確認が必要。

目視で記事と違う物体になった画像は、manifestの`rejected`へ記事指紋と理由を記録して保留する。Mac・CPUとも通常の生成対象から外れ、記事はSVGで公開を続ける。現在の呼び鈴の記事はこの保留対象。新しい正しいJPEGを実際に生成・確認するまで、古い画像の指紋やseedを変更して解除しない。

品質保留の再試験は`slug`の明示と`retry_held=true`が必要で、確認用候補を保存し、自動公開しない。すでに有効な画像がある記事を再生成する操作には使えない。保留解除には目視確認した本当に新しいJPEGを使い、旧JPEGの指紋やseedを付け替えない。通常の手動選定は保留記事を除外する。

既刊の写真化プレビューは、さらに `backfill_scene` に新しい場面指示を渡し、`limit=1`・`publish=false`で手動実行する。通常キューが残る場合や公開を指定した場合は生成せず、日次上限もモデル取得前に判定する。生成物は元の記事を束縛する `source_article_sha256` 付きのレビューbundleとして、専用候補ブランチと短期artifactへ保存する。実行中のcheckoutや本番素材は上書きしない。いつ試し、どう採用するかは[VISUAL_OPERATIONS.md](VISUAL_OPERATIONS.md)を正本とする。

文章だけの試作で必要な物が消える場合は、同じプレビューに `backfill_reference=true`（CLIでは`--backfill-reference`）を加え、現在の記事に合う専用SVGを構図の参照にできる。原記事の承認済みSVGを先に検証し、CairoSVG 2.7.1でメモリ内の800×448 RGBへ変換する。既存RVパイプラインの部品・4つの重み・安全フィルターを再利用し、追加モデルやControlNetは取得しない。初期画像の強さは0.65、指定24ステップのうち実際の変換は15ステップ。GitHub標準CPUで生成・形式検証まで確認済み。ただし初回のノート試作は参照SVGの穴が消えて不採用となり、形状の維持や品質改善は未確認。処理速度の一般化とメモリ使用量の評価もまだ行っていない。

この参照経路は手動の既刊プレビューだけで、既存の日次上限・新着優先・再実行禁止を共有する。参照bundleのCLI自動公開も拒否する。生成元は `github-cpu-sd15-img2img` として従来と区別し、固定設定、元記事指紋、参照SVGと実RGBのSHA、レンダラーの版を残す。参照SVGの承認を写真の承認に流用せず、実JPEGを本文と見比べてから通常PRで採用する。過去の参照SVGを後から修正しても、それだけで採用写真が失効する判定は加えない。

採用された写真は本文・一覧・OGP・Twitter画像・RSSで同じJPEGを使い、画像URLにはSHA由来の版を付ける。専用SVGの共有PNGは[画像運用](VISUAL_OPERATIONS.md)で原本と結び付けた派生ファイルを使う。X共有・リンクコピー・記事自動紹介では、実際に選ぶ検証済みJPEGまたは専用PNGのSHA先頭12文字を `image` として記事URLへ添える。同じ画像なら共有URLも固定し、未検品・失効画像の版は使わない。canonical・OG URL・RSSの記事URLは従来どおり保つ。既存SNS投稿のキャッシュが即時更新される保証はない。

## 運用上の注意

- 公開標準ランナーの無償利用とプライベートランナーの課金条件はGitHubの規約次第で変更される。公開リポジトリ用ジョブのガードを外さない。
- 1回ごとに必要な重み約5.5GBを取得する場合がある（旧モデル一式や不要なVAEを重複取得しない）。Macへはモデルを追加せず、使い捨てランナー内だけで取得する。有料ストレージや大容量Actionsキャッシュは使用しない。確認用artifactは最大3枚・1日保持に制限する。
- ワークフローは手動補助と既存停止判定のためactiveを維持します。停止・再開は[MAC_SCENE_SETUP.md](MAC_SCENE_SETUP.md)の既存手順を使い、新しい停止スイッチは設けません。
- **GitHubのCPU方式が失敗しても追加サービスへ自動的に切り替えない。** 推論時は最大55分で停止。画像生成エラーで記事公開を止めない。
- 動画回収は `auto-motion-clips.yml` の独立した記事・採否・画像pushと既存scheduleで継続します。CPUを明示的に公開した場合の既存動画引継ぎも維持します。記事改稿や並行制作があれば最新mainと再照合し、有効な既存画像は上書きしません。

### 検証コマンド

```bash
# オフラインで画像構図と未生成記事の候補のみ表示
python3 scripts/github_cpu_scenes.py --dry-run --limit 3

# 外部アクセスなしのゲート試験
python3 -m unittest discover -s tests -p 'test_cpu_scenes.py' -v
```
