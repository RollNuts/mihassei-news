# 未発生新聞：Macなしでも生成する無料CPU画像ルート

公開リポジトリ `RollNuts/mihassei-news` の GitHub Actions 標準 Linux CPU ランナーで、公開された軽量LCMモデルの**重みをダウンロードして自前推論**する。外部画像生成API、GPT API、ChatGPT追加クレジット、有料GPU、Hugging Face Inference Endpoint、Cloudflareの追加サービスは使わない。

## できること／できないこと

- `.github/workflows/free-cpu-scenes.yml` は「手動実行」・初回導入時の1枚検証・一日4回の定時実行。1回1〜3枚まで。無料GitHub Actionsの**公開リポジトリ用標準CPU**に厳格限定。プライベート化・フォーク・PR からは生成しない。
- `scripts/github_cpu_scenes.py` は最新記事の**画像未生成分だけ**処理。既存のMac製写真を上書きしない。ComfyUI/Macがまだ動かない時だけでも記事に再現画像を付けられる。
- モデル: [SimianLuo/LCM_Dreamshaper_v7](https://huggingface.co/SimianLuo/LCM_Dreamshaper_v7)（掲載時点のカード記載はMIT、リビジョン固定）。モデルファイルのダウンロードのみで、推論はGitHubのCPU内部で実行する。将来収益化するならモデル・基盤モデルそれぞれのライセンスを確認すること。
- JPEGは1枚700KiB以下、1200×675、アーカイブ1.5GiB上限。ハッシュ、記事本文・見出し・画像指示の指紋、モデル名、生成元の一致を確認して初めて記事やOGPに表示する。
- CPUの速度・生成品質はSDXLをMacのGPUで動かす場合に及ばない可能性が高い。**実際の生成成功・本番表示・見栄えはGitHub Actions実行ログと公開URLで確認するまで未証明**。異常な画像を自動公開しないための安全検証はあるが、意味が伝わるかの人間のレビューは自動化していない。

## 初回確認

1. mainへの初回マージ時には、ワークフローファイルの追加を契機に**1枚だけ**生成を試す（権限がある場合）。その結果を確認してから、必要ならGitHubの [Actions](https://github.com/RollNuts/mihassei-news/actions) → `Free CPU staged-news photos (public repository only)` → `Run workflow` → `limit=1` を選ぶ。
2. `CPU staging` と `Saved verified first-party JPEG` が出た後、`images: generate free CPU staged comedy scenes` がmainにコミットされるか確認する。
3. Cloudflareがmainから再ビルドし、該当記事の `/generated/<slug>.jpg` がHTTP 200、記事サムネイルも `AI生成` ラベル付きになるか確認する。画像の絵作りと意図が合うか目視する。
4. パイプラインが稼働して初めて、通常の画像生成済み件数が0から増える。途中で失敗した場合は従来のSVGを表示し、毎日の記事発行は止めない。

## 運用上の注意

- 公開標準ランナーの無償利用とプライベートランナーの課金条件はGitHubの規約次第で変更される。公開リポジトリ用ジョブのガードを外さない。
- 1回ごとにモデル約数GBを取得する場合がある。キャッシュによる将来の課金を避けるため、現状は有料ストレージや大容量Actionsキャッシュを使用しない。
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
