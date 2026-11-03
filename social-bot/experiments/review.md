# Veripsa X 改善ログ（実測と仮説の区別）

作成日（日本時間）：2026-11-03 20:06

## 観測済みの運営状況

- 原稿在庫：28本（内訳 character_moment=4, surreal_text=24）
- Bufferに一度でも登録した原稿：4本（現在予約中の本数ではない）
- 未登録原稿：24本
- 笑いの構造（全在庫）：{'delayed_response': 1, 'dry_dialogue': 3, 'inverted_expectation': 4, 'literal_wordplay': 3, 'meta_commentary': 2, 'perspective_flip': 2, 'physical_scale': 1, 'role_reversal': 2, 'self_reference': 3, 'small_action': 2, 'unexpected_kindness': 1, 'visual_reveal': 4}
- 笑いの構造（Bufferへ一度でも登録済み）：{'inverted_expectation': 2, 'literal_wordplay': 1, 'self_reference': 1}
- 最終確認できたBuffer状態：{'not_checked': 1, 'scheduled': 3}
- Buffer状態確認日時：2026-11-03T09:03:54.740822+00:00

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
- 未登録 24本。新作を大量に増やす前に既存ネタの重複とオチの質を確認する。

## できていないこと（自動で成功と呼ばない）

- Bufferの sent はX上の表示回数ではない。X URL・実際の配信・インプは別途確認。
- X収益化画面の過去90日の対象インプレッションは、ここに記録する投稿別の通常インプレッションと別指標。自動取得不能なら未取得。
- このレポートは定型の結果整理。AIが勝手にXのアルゴリズムを発見・学習したことを意味しない。
- 他の漫画・記事・Xアカウントの数字は混ぜない。
