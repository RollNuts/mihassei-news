#!/usr/bin/env python3
"""Evidence-only weekly learning report for X experiments.

No X scraping, Buffer API, token expenses, or fabricated metrics.
"""
import json
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/"metrics"))
from report import validate as validate_observation

JST=timezone(timedelta(hours=9))


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def actual_summary(observations, fmt, hours):
    """Only observed metrics; one latest observation per original X post."""
    latest={}
    for item in observations:
        time=validate_observation(item)
        if item["format"]!=fmt or item["window_hours"]!=hours:
            continue
        k=item["post_id"]
        if k not in latest or time>latest[k][0]:
            latest[k]=(time,item["impressions"])
    values=[val for _,val in latest.values()]
    return {
        "n":len(values),
        "median":statistics.median(values) if values else None,
        "sum":sum(values) if values else None
    }


def compose(inventory, ledger, delivery, metric_data, now=None):
    now=now or datetime.now(JST)
    posts=sorted(inventory["experiments"],key=lambda x:x["sequence"])
    posted=set(ledger.get("queued",{}))
    pending=[p for p in posts if p["id"] not in posted]
    formats=Counter(p["format"] for p in posts)
    status={x["post_id"]:x["buffer_status"] for x in delivery.get("items",[])}
    states=Counter(status.get(pid,"not_checked") for pid in posted)
    observations=metric_data.get("measurements",[])
    for m in observations:
        validate_observation(m)
    lines=[
        "# Veripsa X 改善ログ（実測と仮説の区別）",
        "",
        f"作成日（日本時間）：{now.strftime('%Y-%m-%d %H:%M')}",
        "",
        "## 観測済みの運営状況",
        "",
        f"- 原稿在庫：{len(posts)}本（内訳 {', '.join(f'{key}={n}' for key,n in sorted(formats.items()))}）",
        f"- Bufferに一度でも登録した原稿：{len(posted)}本（現在予約中の本数ではない）",
        f"- 未登録原稿：{len(pending)}本",
        f"- 最終確認できたBuffer状態：{dict(sorted(states.items())) if states else '未確認'}",
        f"- Buffer状態確認日時：{delivery.get('checked_at_utc','未取得')}",
        "",
        "## Xインプレッション（主KPI）",
        ""
    ]
    formats_to_test=["surreal_text","character_moment"]
    for window in (24,72):
        for fmt in formats_to_test:
            stats=actual_summary(observations,fmt,window)
            if stats["n"]==0:
                desc="未取得（0回ではない）"
            else:
                desc=f"{stats['n']}件 / 中央値 {stats['median']:g} / 合計 {stats['sum']}（実測の範囲のみ）"
            lines.append(f"- {window}h {fmt}：{desc}")
    lines+=["","## 来週の検証と判断",""]
    cases={}
    for fmt in formats_to_test:
        cases[fmt]=actual_summary(observations,fmt,24)
    enough=all(cases[k]["n"]>=5 for k in formats_to_test)
    if not enough:
        lines+= [
            "- **勝ち形式はまだ判断しない。** 両形式で24h実測が各5本未満。",
            "- **次のテスト**：短文3本に対してキャラ日常1本を目安に投稿を分散し、片方が最後に固まる問題を避ける。",
            "- 投稿の公開が確認できたら、X公式インサイトから24h/72h表示回数を取得して social-bot/metrics/records.json に記録する。",
            "- 自動取得できない指標を推計で埋めない。"
        ]
    else:
        leader=max(cases,key=lambda k:cases[k]["median"])
        loser=min(cases,key=lambda k:cases[k]["median"])
        high,low=cases[leader]["median"],cases[loser]["median"]
        ratio=high / max(low,1)
        lines+= [
            f"- 実測24h中央値の**観測上の先行**：{leader}（中央値 {high:g}、{cases[leader]['n']}件）。",
            f"- 比較対象：{loser}（中央値 {low:g}、{cases[loser]['n']}件）。",
            f"- 参考倍率：{ratio:.2f}倍（投稿時刻やフォロワー数など未調整、因果関係を示さない）。",
            "- **次のテスト**：先行形式の新作を増やす仮説を立て、冒頭かオチのどちらか一つだけ変更して比較する。",
        ]
    if not pending:
        lines.append("- **在庫切れ**：既存の重複や弱いオチを避け、新作を補充して無投稿を防ぐ。")
    elif len(pending)<7:
        lines.append(f"- **在庫注意**：未登録 {len(pending)}本。火・金の補充タスクで完全オリジナルを追加する。")
    else:
        lines.append(f"- 未登録 {len(pending)}本。新作を大量に増やす前に既存ネタの重複とオチの質を確認する。")
    lines+=["","## できていないこと（自動で成功と呼ばない）","",
            "- Bufferの sent はX上の表示回数ではない。X URL・実際の配信・インプは別途確認。",
            "- このレポートは定型の結果整理。AIが勝手にXのアルゴリズムを発見・学習したことを意味しない。",
            "- 他の漫画・記事・Xアカウントの数字は混ぜない。",
            ""]
    return "\n".join(lines)


def main():
    inventory=load(HERE/"posts.json")
    state=load(HERE/"state.json")
    delivery=load(HERE/"delivery.json")
    metrics=load(HERE.parent/"metrics"/"records.json")
    review=compose(inventory,state,delivery,metrics)
    output=HERE/"review.md"
    output.write_text(review,encoding="utf-8")
    print(review)


if __name__=="__main__":
    main()
