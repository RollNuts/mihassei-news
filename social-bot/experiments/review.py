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


def hashtag_summary(posts, observations, fmt, hours=24):
    """Compare tagged/control within the SAME format using verified X metrics.

    A record must carry experiment_id linking it to the exact tested post;
    similar-looking X text, Buffer IDs and unlinked X post IDs are not guesses.
    """
    by_id={post["id"]:post for post in posts}
    latest={}
    for item in observations:
        date=validate_observation(item)
        eid=item.get("experiment_id")
        post=by_id.get(eid)
        if not post or post["format"]!=fmt or item["window_hours"]!=hours:
            continue
        if item["format"]!=post["format"]:
            raise ValueError("X measurement format differs from experiment")
        if eid not in latest or date>latest[eid][0]:
            latest[eid]=(date,item["impressions"])
    buckets={"one_relevant_tag":[],"no_tag":[]}
    for eid,(_,impressions) in latest.items():
        group="one_relevant_tag" if by_id[eid].get("hashtag") else "no_tag"
        buckets[group].append(impressions)
    return {
        k:{"n":len(values),
           "median":statistics.median(values) if values else None}
        for k,values in buckets.items()
    }


def satire_summary(posts, observations, hours=24):
    """Observed post impressions only, never X's qualified 90d reward counter."""
    by_id={p["id"]:p for p in posts}
    latest={}
    for item in observations:
        date=validate_observation(item)
        eid=item.get("experiment_id")
        post=by_id.get(eid)
        if not post or post["format"]!="surreal_text" or item["window_hours"]!=hours:
            continue
        if item["format"]!=post["format"]:
            raise ValueError("X measurement format differs from experiment")
        if eid not in latest or date>latest[eid][0]:
            latest[eid]=(date,item["impressions"])
    groups={"everyday_satire":[], "other_surreal":[]}
    for eid,(_,count) in latest.items():
        group="everyday_satire" if by_id[eid].get("angle")=="everyday_satire" else "other_surreal"
        groups[group].append(count)
    return {group:{"n":len(vals), "median":statistics.median(vals) if vals else None}
            for group,vals in groups.items()}


def compose(inventory, ledger, delivery, metric_data, now=None):
    now=now or datetime.now(JST)
    posts=sorted(inventory["experiments"],key=lambda x:x["sequence"])
    posted=set(ledger.get("queued",{}))
    pending=[p for p in posts if p["id"] not in posted]
    formats=Counter(p["format"] for p in posts)
    mechanisms=Counter(p.get("mechanism","unspecified") for p in posts)
    queued_mechanisms=Counter(
        p.get("mechanism","unspecified") for p in posts if p["id"] in posted
    )
    status={x["post_id"]:x["buffer_status"] for x in delivery.get("items",[])}
    states=Counter(status.get(pid,"not_checked") for pid in posted)
    image_ids={p["id"] for p in posts if p.get("visual")}
    registered_image_ids=image_ids & posted
    sent_image_ids={pid for pid in registered_image_ids if status.get(pid)=="sent"}
    provider_x_links=sum(1 for item in delivery.get("items",[])
        if item.get("post_id") in sent_image_ids and item.get("buffer_external_link"))
    snapshots=[
        (item["post_id"],item["buffer_latest_impressions"])
        for item in delivery.get("items",[])
        if item.get("buffer_status")=="sent"
        and isinstance(item.get("buffer_latest_impressions"),dict)
    ]
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
        f"- 笑いの構造（全在庫）：{dict(sorted(mechanisms.items()))}",
        f"- 笑いの構造（Bufferへ一度でも登録済み）：{dict(sorted(queued_mechanisms.items()))}",

        f"- 最終確認できたBuffer状態：{dict(sorted(states.items())) if states else '未確認'}",
        f"- Buffer状態確認日時：{delivery.get('checked_at_utc','未取得')}",
        f"- 独自画像：在庫{len(image_ids)}枚・Buffer登録{len(registered_image_ids)}枚・Buffer sent {len(sent_image_ids)}枚",
        f"- Buffer提供のX投稿URL：{provider_x_links}件（独立したX公開確認とは別）",
        f"- Buffer暫定インプ取得：{len(snapshots)}件（各投稿の取得時点が異なり24h/72h比較には使わない）",
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
    # Hashtag effects can only be compared with linked, observed X metrics.
    # A Buffer scheduled/sent record is NOT an X impression.
    lines+=["","## ハッシュタグ実験：タグ1個 vs タグなし（同一形式内）",""]
    for fmt in formats_to_test:
        stats=hashtag_summary(posts,observations,fmt,24)
        for group in ("one_relevant_tag","no_tag"):
            row=stats[group]
            label="タグ1個" if group=="one_relevant_tag" else "タグなし"
            value=(f"{row['n']}件 / 24h中央値 {row['median']:g}" if row["n"]
                   else "未取得（0回ではない）")
            lines.append(f"- {fmt} / {label}：{value}")
        enough=all(row["n"]>=5 for row in stats.values())
        if enough:
            tag=stats["one_relevant_tag"]["median"]
            control=stats["no_tag"]["median"]
            lead=("タグあり" if tag>control else
                  "タグなし" if tag<control else "同値")
            lines.append(f"- {fmt}：観測上の先行は{lead}（投稿時刻・ネタ差は未調整）")
        else:
            lines.append(f"- {fmt}：効果は判定保留（各群5本の実測未満）")
    lines+=["", "## 身近な理不尽ネタの比較パイロット", ""]
    satire=satire_summary(posts,observations,24)
    for name,label in (("everyday_satire","身近な理不尽"),
                       ("other_surreal","その他のシュール短文")):
        row=satire[name]
        desc=(f"{row['n']}件 / 24h中央値 {row['median']:g}" if row["n"]
              else "未取得（0回ではない）")
        lines.append(f"- {label}：{desc}")
    if all(group["n"]>=5 for group in satire.values()):
        lines.append("- 両群とも実測5件以上。題材と配信時間の交絡に注意して比較する。")
    else:
        lines.append("- 効果判定保留：両群の実測が各5件に達するまでは優劣を決めない。")
    lines+=["","## 来週の検証と判断",""]
    cases={}
    for fmt in formats_to_test:
        cases[fmt]=actual_summary(observations,fmt,24)
    enough=all(cases[k]["n"]>=5 for k in formats_to_test)
    if not enough:
        lines+= [
            "- **勝ち形式はまだ判断しない。** 両形式で24h実測が各5本未満。",
            "- **次のテスト**：従来の形式分散を維持し、身近な理不尽を題材にした新作を約4枠に1件だけ試す。既存予約は触らない。",
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
            "- X収益化画面の過去90日の対象インプレッションは、ここに記録する投稿別の通常インプレッションと別指標。自動取得不能なら未取得。",
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
