# Veripsa X 改善ログ（実測と仮説の区別）

作成日（日本時間）：2026-11-03 23:03

## 観測済みの運営状況

- 原稿在庫：38本（内訳 character_moment=6, surreal_text=32）
- Bufferに一度でも登録した原稿：10本（現在予約中の本数ではない）
- 未登録原稿：28本（うち独自画像3本）
- 笑いの構造（全在庫）：{'delayed_response': 2, 'dry_dialogue': 4, 'inverted_expectation': 5, 'literal_wordplay': 3, 'meta_commentary': 2, 'perspective_flip': 4, 'physical_scale': 1, 'role_reversal': 4, 'self_reference': 3, 'small_action': 2, 'unexpected_kindness': 2, 'visual_reveal': 6}
- 笑いの構造（Bufferへ一度でも登録済み）：{'dry_dialogue': 1, 'inverted_expectation': 2, 'literal_wordplay': 1, 'meta_commentary': 1, 'role_reversal': 1, 'self_reference': 1, 'small_action': 1, 'visual_reveal': 2}
- 最終確認できたBuffer状態：{'scheduled': 8, 'sent': 2}
- Buffer状態確認日時：2026-11-03T12:45:41.578982+00:00
- 独自画像：在庫7枚・Buffer登録4枚・Buffer sent 1枚
- Buffer提供のX投稿URL：1件（独立したX公開確認とは別）
- Buffer暫定インプ取得：0件（各投稿の取得時点が異なり24h/72h比較には使わない）

## 日別Buffer送信確認（部分記録）

- 2026-11-01：オリジナルBuffer sent確認 0件 / 設定7枠（未確認分は含まない）
- 2026-11-02：オリジナルBuffer sent確認 0件 / 設定7枠（未確認分は含まない）
- 2026-11-03：オリジナルBuffer sent確認 2件 / 設定7枠（未確認分は含まない）
- 上記は保存済みのBuffer送信時刻がある原稿だけ。記事紹介・手動投稿・取得漏れは含まない。0件は実際の投稿0件とは限らない。

## Xインプレッション（主KPI）

- 24h surreal_text：未取得（0回ではない）
- 24h character_moment：未取得（0回ではない）
- 72h surreal_text：未取得（0回ではない）
- 72h character_moment：未取得（0回ではない）

## ハッシュタグ実験：タグ1個 vs タグなし（同一形式内）

- surreal_text / タグ1個：未取得（0回ではない）
- surreal_text / タグなし：未取得（0回ではない）
- surreal_text：効果は判定保留（各群5本の実測未満）
- character_moment / タグ1個：未取得（0回ではない）
- character_moment / タグなし：未取得（0回ではない）
- character_moment：効果は判定保留（各群5本の実測未満）

## 身近な理不尽ネタの比較パイロット

- 身近な理不尽：未取得（0回ではない）
- その他のシュール短文：未取得（0回ではない）
- 効果判定保留：両群の実測が各5件に達するまでは優劣を決めない。

## 来週の検証と判断

- **勝ち形式はまだ判断しない。** 両形式で24h実測が各5本未満。
- **次のテスト**：従来の形式分散を維持し、身近な理不尽を題材にした新作を約4枠に1件だけ試す。既存予約は触らない。
- 投稿の公開が確認できたら、X公式インサイトから24h/72h表示回数を取得して social-bot/metrics/records.json に記録する。
- 自動取得できない指標を推計で埋めない。
- 未登録 28本。既存ネタの重複とオチの質を確認し、日次補充を継続する。

## できていないこと（自動で成功と呼ばない）

- Bufferの sent はX上の表示回数ではない。X URL・実際の配信・インプは別途確認。
- X収益化画面の過去90日の対象インプレッションは、ここに記録する投稿別の通常インプレッションと別指標。自動取得不能なら未取得。
- このレポートは定型の結果整理。AIが勝手にXのアルゴリズムを発見・学習したことを意味しない。
- 他の漫画・記事・Xアカウントの数字は混ぜない。
