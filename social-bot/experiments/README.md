# X表示回数を獲得する実験フィード（稼働用）

## このレーンは「漫画の完成待ち」をしない

2026-11-03、アカウント運営者から **「出さないことは許さない。実際に投稿して反応を見る」** という明示的な方針変更があった。

- `experiments/posts.json`：**未投稿のオリジナル短文**を日付固定なしの配信順で保存。初期12本。既存の不採用漫画や古い試験漫画の再利用はしない。
- `experiments/config.json`：この短文実験だけの配信ON/OFF、実際のXチャンネルの固定ID、オリジナル最大予約9件・1回最大7件（同一Xチャンネルの全体は10件）を定義。
- `experiments/dispatch.py`：Buffer Freeの既存APIキーから安全に予約投稿。X有料API、OpenAI API、画像生成APIは使わない。
- `experiments/state.json`：登録した本文のSHA256とBuffer投稿IDを保存し、処理再実行でも同一投稿を2度予約しない。
- `.github/workflows/x-original-experiments.yml`：**main反映直後に1回、その後00:05・12:05 JSTごろ**に予約キューを補充。公開時刻はBuffer側でオーナーが設定した7枠/日に従う。
- `metrics/records.json`：Xの画面・正規に接続された集計などの実測が確認できた場合のみ、24h/72hの表示回数を記録。GitHub更新・漫画話数・Buffer予約件数は**実測インプではない**。

## 漫画との分離

- `social-bot/ip/publication_quality_gate.json` は現時点で **false** のまま。これは**未承認の漫画**への出稿防止であり、独立したオリジナル短文実験を止めるためには使わない。
- 第6話『おおきな影』は明示的に不採用。勝手に復活・改稿・予約しない。
- キャラの見た目の新案は許容するが、承認済みの本編画像や台帳を書き換えず、**新しいビジュアル候補**として試験する。短文実験が好調でも漫画の模倣リスクを放置しない。
- `posts.json` の既存漫画2本はBuffer下書き退避済み。**下書きを自動で本番へ戻さない。**

## ハッシュタグのAB実験（追加課金なし）

X公式は投稿内のハッシュタグを**2個まで推奨**するが、必ずインプレッションが増えるという根拠はない。短いオチの視認性も大切なため、**タグなし vs 関連タグ1個**を比較する。

- シュールな短文の一部に `#シュールネタ`、ネジまちの日常ネタの一部に `#ネジまち`。それぞれ投稿の末尾に空行を挟んで1個だけ付ける。
- 残りの投稿はタグなしの比較対象。投稿形式そのものの差とタグの差を混ぜず、**同じ形式内だけで比較**。
- `experiments/posts.json` の `hashtag` を明示した投稿だけに適用。**すでにBufferへ送った最初の3本を一切変更しない**。タグ追加/変更後に既存のBuffer投稿を編集したり再送したりしない。
- `dispatch.py` は原稿SHA256と**最終投稿本文（タグ付き）のSHA256**も投稿台帳へ保存。送信後のタグ改変は自動検証で拒否。Buffer成功・Git保存失敗時も、タグ付きの完全一致で再予約を防ぐ。
- 追加タグは各形式の上記1種類に限定。無関係なトレンドタグ、`#拡散希望` のような参加を煽るタグ、ハッシュタグの羅列は不採用。
- X投稿別のインプレッションの実測が得られたとき、`metrics/records.json` の記録へ任意フィールド `"experiment_id": "xexp-ufo-ticket"` を付けると `experiments/review.md` で24hのタグあり/なしを照合できる。実測X投稿IDは別の `post_id` に記載する。
- **両群それぞれ5本以上の実測が揃うまで効果判定保留。** 実績未取得を0インプ扱いせず、「X投稿にハッシュタグが付いた」ことだけを成果とは呼ばない。

## ネタ構造の多様化（2026-11-03 実装）

- 新しいオリジナル短文を**10本追加**し、候補を合計22本に拡充。手が届く変な現象（透明人間とネイル、返事が遅いこだま、勝手に出勤するほうき、先に帰宅した影など）や、ネジまちの乾いた会話を追加。既存の12本、予約3件の本文やタグは変更しない。
- 各投稿の `mechanism` に「視覚的なオチ」「言葉の字義通り」「視点の反転」「遅い返事」などの**笑いの仕組み**を編集メタデータとして記録。X本文には表示されない。
- `dispatch.py` はすでに予約済みの投稿を保持しつつ、次に選ぶ候補の直近2件と同じ仕組みをできるだけ避ける。無理に見た目だけ変えた焼き直しを連続投稿しない。
- 5件目ごとの枠では、新しい `featured:true` の有力候補を優先して**古い在庫を全部消費する前に新しい仮説を試す**。最大予約枠3件・1回2件・1日1投稿の既存無料運用は変えない。新候補でも、Xのインプが伸びる保証はない。
- 毎週月曜の `review.md` に候補と予約済みの笑いの仕組みの分布を併記し、人気の**数字が取れた場合だけ**仮説と実績を照合する。インプ実測がない時は構造の多様化を成果や勝ち筋とは呼ばない。
- `featured` と `mechanism` は内側の選定メモで、Bufferに送るのはオリジナル本文と実験用の明示ハッシュタグだけ。本文のSHA256保護はこれまで通り。

## 改善フローを回す実装（2026-11-03）

- **先に違う形式を試す**：Buffer予約は原稿の単純な並び順ではなく、原則「短文3本→キャラ日常1本」を繰り返す。該当タイプが品切れなら他形式へ切り替え、投稿ゼロを避ける。すでにBufferへ予約済みの本文と順序は変えない。
- **週に一度、事実と仮説を分離**：`experiments/review.py` が `posts.json`、`state.json`、`delivery.json` と `metrics/records.json` を読み、配信済み・予約中・未登録、24/72時間後の表示回数を照合する。結果は `experiments/review.md` に保存する。
- **日次の新ネタ補充**：定期制作タスクが `review.md` を参照し、**実測が十分な形式を優先**。数値が未取得なら勝敗を捏造せず、オチの違いと形式のバリエーションを試す。
- **勝ち判定の最低条件**：比較する各形式で実測24時間後の投稿が5本以上。そこまで「勝ち形式」は断言しない。条件を満たしても時刻・フォロワー等は未調整なので、観測上の先行としか呼ばない。
- **投稿品質**：新作は一読で状況とオチが伝わる短文で、過去ネタの焼き直し・不自然な説明・誰かを見下す笑いを避ける。新しいX施策のためにX有料APIや画像生成従量課金はしない。

## 反応からの改善

最優先KPIはXの実測表示回数。形式ごとの中央値・母数・公開後の観測時間を揃える。まずテキストを実運用し、後から画像・漫画・キャラの日常を同じルールで比較する。実測不可のときは「未取得」であり0ではない。**0インプを推計で作らない。**

日々の補充は10〜15本単位の完全オリジナル創作を低頻度に行い、模倣・重複・同じオチの再利用を防ぐ。「数を増やすこと」が目的ではないが、在庫切れによる無投稿を避ける。

## 停止・復旧（ユーザーが必要な時のみ）

誤ったXアカウントへ送らないようXチャンネルIDを固定。認証違い・上限超え・本文変更・同一投稿を検知すると失敗して停止し、次回リトライで同じBuffer投稿を検索して復元する。

この短文実験だけを止める場合、`experiments/config.json` の `experiment_publishing_enabled` を `false` にする。**既に予約したBufferの投稿は自動では取り消されない**ので、予約取消が必要ならBufferでも操作する。旧漫画の別ゲートを変更する必要はない。

## コストとプラン

GitHub公開リポジトリの無料Actions、Buffer Freeの既存チャンネル・既存APIキーのみ。OpenAI API / 画像生成 API / X API従量課金 / 新たな有料サービスは使用しない。Buffer無料プランの機能と制限は将来変更される可能性があるため、失敗したら**有料化して回避せず**理由を記録する。

**公開済みの検証方法**：Actionsの実行ログでBufferのAPI受付成功を確認し、Bufferで予約時刻・画像/本文を確認する。その後Xに公開されたURLと表示回数を確認する。予約受付＝実際のX公開成功ではない。

## Original X illustrations (2026-11-03)

- Four independent surreal stories have scene-only original 1200×675 PNG illustrations: UFO parking ticket, invisible person's nail salon/train, commuter broom, shadow home. This is a visual experiment, **not** an approved Nejimachi manga episode or a photoreal AI image.
- `visuals.py` generates assets locally using Pillow only. CI generates the PNGs and commits them to `social-bot/assets/xexp-*.png` on main **before** submitting the Buffer image URL. No paid image API, X API, additional cloud storage, or extra credits.
- `dispatch.py` reserves the images through the proven Buffer `assets.image.url` mutation. First original visual post is prioritized on the next available FREE queue slot; existing first three text reservations never change. The initial image experiments use one relevant `#シュールネタ` tag; text-only/no-tag items remain comparison controls.
- The Buffer post ID, final-text SHA256, and image SHA256 are persisted. Once an image is hosted or queued, the script does not overwrite it, and a mismatching hash fails validation. Unknown visuals and unapproved character comics cannot enter this path.
- The shared queue stays capped at 3 scheduled / 2 new per refill. A successful Buffer request is only a reservation; confirm actual X posting and 24h/72h impressions separately before judging creative impact.

### One-off instant illustration launch (owner request, 2026-11-03)

- `launch_first_visual_now: true` selects the first unsubmitted original **surreal** illustration with one relevant hashtag and sends it using Buffer `mode: shareNow`. This runs at most once per ledger; the result is never interpreted as a verified X post until checked against Buffer/X. No unapproved manga, paid API or Buffer pricing change.
- Only this first image can bypass the three **scheduled** post slots; it is an immediate attempt, not an extra queued post or permission to spam. All later posts use existing queue caps and scheduling. Even if the image launch fails, subsequent daily runs can safely retry without rewriting reserved posts.
- Normal scheduling now gives original X experiments priority over optional article promotion, so an article cannot take the last free slot before the visual experiment.

## Everyday satire pilot (2026-11-03)

- Added six new, unsubmitted self-contained Japanese jokes about familiar daily friction: self-checkout, surveys, waiting, notifications, bento pricing and paperless receipts. Previously registered Buffer texts, tags, URLs and images remain immutable.
- New items use internal metadata `angle: everyday_satire`. The dispatcher samples a relatable-joke candidate approximately once every four available slots, without raising the current daily publishing frequency or Buffer Free queue limit. This is an unproven creative hypothesis, not an algorithm hack.
- Three pilot posts use one relevant `#シュールネタ` tag, and three have no tag. Do not conflate tag effects with topic/format differences.
- `review.py` separately compares **linked, measured 24-hour ordinary X post impressions** for satire vs the other surreal short posts, keeping verdicts on hold until each group has at least five measured posts. This ordinary metric is **not** the unique Premium verified-user Home Timeline impressions from the 90-day Original Content Rewards eligibility screen.
- Never infer monetization progress from normal impressions, Buffer sent/scheduled states or the website traffic. Do not put private creator dashboard screenshots or identifiers in this public repository without explicit owner permission.
- Zero new cost: existing Buffer Free + GitHub Actions only. No paid X API, ads, automated bulk follows/replies or release of unapproved comic IP.

### Continuous original picture rotation and provider-status audit (2026-11-03)

- After the one-off original shareNow image, `launch_first_visual_now` is OFF. Future originals alternate image/plain surreal candidates where inventory allows; text-only and new everyday satire experiments remain live, as does every fourth character moment.
- `probe_delivery.py` makes read-only Buffer queries for saved Buffer IDs, verifies exact Veripsa channel and immutable caption, records provider status, image attachment evidence and Buffer-returned X permalink. No invented views, no sending on this audit route, and no resurfacing unapproved Nejimachi episodes.
- `delivery.json` is committed after each live queue run and the weekly review reports image/Buffer sent separately from actual X impression evidence.

## Owner-configured seven X posting slots per day (2026-11-03)

- The owner set **seven recurring daily posting times** in the existing Buffer Veripsa X channel. The repository uses `mode: addToQueue`, so **Buffer owns the actual release times**. Verify the seven times apply to all seven weekdays in the correct channel timezone; GitHub cannot confirm UI settings.
- Buffer Free permits **ten concurrently scheduled posts per channel**, not a ten-post daily limit. The original text/image/character feed reserves up to **nine**, with a maximum of **seven new entries per queue refill**. One remaining slot is available for a genuinely measured-interest article, at most once every 72 hours and with a permanent article-slug ledger. The shared total hard cap is ten.
- The GitHub Action refills only at **00:05 and 12:05 JST** (best effort, may run late). Buffer's Free API also permits **1,000 requests per channel per 30 days**, so the high-cost per-post `probe_delivery.py` no longer runs in every refill. The daily read-only `x-delivery-state.yml` continues to track provider scheduled/sent states.
- The retired 12:30 `customScheduled` second-post workflow has **no scheduled trigger**, and `two_daily_midday_enabled` is false. It must not produce an eighth post beyond the owner's seven Buffer slots. Already queued posts are untouched; none are canceled or edited.
- `publish.list_existing` paginates a bounded recent 30-day sent/scheduled window, combining provider recovery with permanent original and article ledgers. If pagination, authentication or provider limits fail, abort writes instead of guessing.
- Daily original-idea replenishment should maintain **21–28 unsubmitted, genuinely distinct** short texts where quality permits. Prioritize an understandable setup and concrete ending; no identical/near-identical posts, click bait, blind auto-likes, bulk follows, trend hijacking or unapproved manga.
- Seven daily slots is a **target**, not evidence of successful X publishing, impressions or monetization. Preserve X actual-post links, provider sent/scheduled statuses and verified-user 90-day reward metric as different data sources. Do not fabricate missing statistics.
- Additional cost remains **0 yen**: free Buffer, public GitHub Actions and local rendering only; no upgraded subscriptions, metered X/GPT/image APIs, paid GPUs or extra credits.

### Daily read-only delivery audit

One dedicated GitHub Actions workflow checks Buffer delivery once each day at 23:45 JST, plus its initial deployment. It requests at most 8 posts and 1 matured metric snapshot per run; the historic record remains cached with its real last-check timestamp. It never sends to X. The Free plan's 1,000 API calls per channel per rolling 30 days are shared with publishing, so do not run this on every content push.

## Original content restock and free evidence audit (2026-11-03)

- Added 10 new, unsubmitted original posts (three fresh Pillow scene-only illustrations, two small Nejimachi moments, five unrelated short/satirical jokes). Existing Buffer-registered texts, image hashes and the unapproved-comic gate are immutable.
- The new scenes are deterministic 1200x675 PNGs with a 1MB cap. The existing main workflow generates and hosts them before Buffer can queue them; no image model API, credit, GPU or paid storage is needed.
- Keep **exactly one** daily read-only delivery audit: `x-delivery-audit.yml` at **23:45 JST**. The older status-only `x-delivery-state.yml` has **no daily schedule or main-push trigger** to avoid writing the same delivery.json twice.
- Daily audit fetches up to **20 post statuses in two 10-item GraphQL alias requests** and **one** mature provider metric snapshot. Buffer explicitly documents this batching method for reducing API usage. Earlier IDs, permalinks and observation timestamps stay cached rather than rescanned. A provider read error may preserve an earlier link as a *historical* link without asserting a current sent state.
- `delivery.json.health` now distinguishes all original scripts, ever-queued scripts, unused originals, unused image scenes, last-known provider sent and links, and warnings below **14 unused scripts or 2 unused images**. These are not verified impressions or reach. The weekly report independently includes unused image inventory.
- Increase creativity and quality with a separate daily restock, but never duplicate near-identical jokes or spam seven posts to game X. Keep Buffer Free (ten queued slots/channel), pinned X account, no paid APIs, and no unapproved canonical manga.

### 2026-11-03：無料API呼び出しのさらなる節約

- Buffer公式のGraphQL `post(input:{id})` エイリアスを使い、10投稿を1リクエストで照会。1日最大20投稿を**2リクエスト**で配信監査する。これまでの個別8照会よりAPIコストを下げつつ、7投稿/日の遷移を追いやすくする。
- レスポンスに欠損・不一致やAPIエラーがある場合は `unverified` として記録し、各件再照会への自動フォールバックはしない。既存Buffer投稿や予約・画像・漫画承認ゲートに変更はない。
- 以前のBuffer送信URLは歴史的な参考値として保持するが、現在のXインプレッションや公開確認と取り違えない。追加費用0円。

### 原稿の焼き直し防止と日別Buffer送信の部分実績

- 新規原稿はNFKC正規化＋句読点・空白を除いた同一性判定、および日本語3文字単位の類似度チェック（Jaccard 0.72以上）を投稿前に実施。ほんの少し書き換えただけの再利用を止める。過去にBufferに登録した本文と画像は一切書き換えない。
- 1日1回の既存Bufferバッチ監査は、追加APIなしで送信時刻が確認できた独自原稿を日本時間別に集計し、直近7日分の **部分記録** を `delivery.json.health.observed_original_sent_by_jst_day` に保存する。記事紹介、手動投稿、取得漏れは対象外で、未確認分を投稿ゼロや7枠未達と断定しない。
- 週次レビューには直近3日分だけ表示。Bufferが返す`sent`はXの独立公開確認ではなく、X投稿別表示回数や収益化対象の90日インプレッションと区別。既存のバッチ取得2回・追加メトリクス確認1回の無料API予算も維持する。

### 今後のBuffer予約時刻も検証する（2026-11-03）

- Buffer公式GraphQLのPost `dueAt` を既存の日次エイリアス取得項目に加えた。**追加のAPI呼び出しはゼロ**。同じBuffer ID・Xチャンネル・本文の一致が取れた `scheduled` 原稿だけに `buffer_due_at_utc` を付与し、日時にタイムゾーンがない場合は無効として扱う。
- `delivery.json.health.observed_scheduled_originals_by_jst_day` に、監査当日に実際に取得した **今後3日間のオリジナル予約**と日本時間での予定時刻をまとめる。古いキャッシュ・送信済み・予定日時が不明な投稿・期限切れの投稿は「今後の予約数」に加えない。
- 2時間以上予定時刻を過ぎてもBufferから `scheduled` が返る場合は要確認件数を報告する。ただしAPI反映遅延と実際の投稿失敗は区別し、ユーザーの了承なく再投稿やリスケジュールをしない。
- この部分記録では、Bufferの曜日別 **7件の繰り返し枠が有効かを証明できない**。無料プランのキュー上限10件、原稿在庫、投稿時刻、Bufferの実際の公開、Xインプをそれぞれ別指標として扱う。
- 無料Buffer APIのまとめ読み（最大20件/2リクエスト）、成熟したインプの任意確認1件、毎日1回の監査、画像/漫画の安全ゲート、追加費用0円は変更しない。

### Buffer予約の予定時刻が不明な場合の診断（2026-11-03）

- Buffer公式の `createPost` は `post { id dueAt }` を同じAPI受付応答で返せる。新しいオリジナル短文・画像予約では返却された予定日時を `state.json` に保存する（旧投稿・ID・本文は変更しない）。値がnullなら `buffer_due_at_missing_at_creation` を保存し、予定時刻は推定しない。
- 配信監査の `post(input:{id})` でも `scheduled` に対する `dueAt` 欠落を数え、GitHub Actionsにwarningを表示する。作成時の時刻があっても、現在の時刻として使わず過去の受付証拠と区別する。
- 1日7枠の設定だけでは、個々の投稿が7枠に割り当てられた証拠ではない。複数投稿で時刻が未取得ならBufferのVeripsaチャンネルの投稿スケジュール/タイムゾーンを確認。既存予約を消す・再送する・有料APIに切り替えることはしない。
