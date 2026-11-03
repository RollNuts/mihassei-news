#!/usr/bin/env python3
"""Offline, evidence-only summary of X post impressions, grouped by format.

Does NOT log into X, fabricate metrics, or use paid APIs. Empty data means
unmeasured, never 0 impressions. Designed to run safely in free GitHub Actions.
"""
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

HERE=Path(__file__).resolve().parent
FILE=HERE/"records.json"
FORMATS={"surreal_text", "nejimachi_comic", "character_moment", "other"}
WINDOWS={24,72}
SOURCES={"x_post_insights","buffer_visible_insights","user_supplied_x_insights"}


def load(path=FILE):
    data=json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version")!=1 or not isinstance(data.get("measurements"),list):
        raise ValueError("Invalid X metrics record schema")
    return data


def validate(record):
    if not isinstance(record,dict):
        raise ValueError("Metric entry must be object")
    for key in ("post_id","captured_at_jst","format","metric_source"):
        if not isinstance(record.get(key),str) or not record[key].strip():
            raise ValueError("Missing metric provenance: "+key)
    if record["format"] not in FORMATS:
        raise ValueError("Unsupported post format")
    if record["metric_source"] not in SOURCES:
        raise ValueError("Metric source must be specific and verifiable")
    if type(record.get("window_hours")) is not int or record["window_hours"] not in WINDOWS:
        raise ValueError("Use a 24h or 72h comparison window")
    if type(record.get("impressions")) is not int or record["impressions"]<0:
        raise ValueError("Impressions must be a measured nonnegative integer")
    date=datetime.fromisoformat(record["captured_at_jst"])
    if date.utcoffset() is None:
        raise ValueError("Recorded time must have a timezone offset")
    if "experiment_id" in record and (not isinstance(record["experiment_id"],str) or not record["experiment_id"].startswith("xexp-")):
        raise ValueError("Invalid X experiment identifier")
    for field in ("likes","replies","reposts","profile_visits"):
        if field in record and (type(record[field]) is not int or record[field]<0):
            raise ValueError("Invalid optional metric: "+field)
    return date


def summary(data, window):
    """Take latest observation for each post/window; never double-count revisions."""
    if window not in WINDOWS:
        raise ValueError("Unsupported measurement window")
    latest={}
    for record in data["measurements"]:
        captured=validate(record)
        if record["window_hours"] != window:
            continue
        identifier=record["post_id"]
        if identifier not in latest or latest[identifier][0] < captured:
            latest[identifier]=(captured,record)
    groups={}
    for _,record in latest.values():
        groups.setdefault(record["format"],[]).append(record["impressions"])
    stats={}
    for fmt,values in groups.items():
        stats[fmt]={
            "samples":len(values),
            "median_impressions":statistics.median(values),
            "total_impressions":sum(values),
            "max_impressions":max(values),
            "evidence_level":"比較可能（3本以上）" if len(values)>=3 else "参考値（3本未満）"
        }
    return stats


def format_report(data):
    count=len(data["measurements"])
    if count==0:
        return (
            "Xインプレッション：未取得（0ではない）\n"
            "投稿ごとのXインサイト実測値が登録されていない。"
            "成果・成長率・勝ち形式は判定しない。"
        )
    sections=[]
    for window in sorted(WINDOWS):
        stats=summary(data,window)
        rows=[f"{window}時間後の実測データ"]
        if not stats:
            rows.append("  未取得（0ではない）")
        for fmt,entry in sorted(stats.items(),key=lambda x:-x[1]["median_impressions"]):
            rows.append(
                f"  {fmt}: 件数={entry['samples']} 中央値={entry['median_impressions']:g} "
                f"合計={entry['total_impressions']} 最大={entry['max_impressions']} "
                f"[{entry['evidence_level']}]"
            )
        sections.append("\n".join(rows))
    sections.append("判定：実際の数値が揃うまで形式の勝敗を断定しない。")
    return "\n".join(sections)


def main():
    try:
        print(format_report(load()))
        return 0
    except (OSError,ValueError,TypeError) as exc:
        print("X metrics data rejected:",str(exc),file=sys.stderr)
        return 1


if __name__=="__main__":
    sys.exit(main())
