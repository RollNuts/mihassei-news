"""Append-only daily market forecasts, snapshots, and evidence-linked stance changes.

This ledger is separate from the immutable long-horizon serial forecasts.
It never fetches prices. Outcomes are computed only from explicitly recorded
official evidence after the fixed judgement date. All displayed dates are real
JST dates, not fictional newspaper dates.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
import json
import math
import re

if __package__:
    from .market_sharing import render_market_share
else:
    from market_sharing import render_market_share

PATH = Path("content/market_outlooks.json")
JST = timedelta(hours=9)
STANCES = ("強気", "中立", "弱気")
TRENDS = ("暫定的に整合", "暫定的に逆行", "判断材料不足")
DIRECTIONS = ("上昇", "下落", "横ばい")
BASE_URL = "/serials/market-lab/outlooks/"
FORECAST_ID = re.compile(r"20\d{2}-\d{2}-\d{2}-topix\Z")
REVIEW_ID = re.compile(r"20\d{2}-\d{2}-\d{2}-(?:am|noon|close)-[a-z0-9-]+\Z")


def _timestamp(s):
    if not isinstance(s, str):
        raise ValueError("A daily outlook needs a real publication timestamp")
    try:
        x = datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError("Invalid daily outlook timestamp") from exc
    if x.utcoffset() != JST or x.isoformat(timespec="seconds") != s:
        raise ValueError("Market outlook timestamps must use +09:00 and seconds")
    return x


def _day(s):
    if not isinstance(s, str):
        raise ValueError("Market outlook requires canonical YYYY-MM-DD")
    try:
        x = date.fromisoformat(s)
    except ValueError as exc:
        raise ValueError("Invalid market outlook date") from exc
    if x.isoformat() != s:
        raise ValueError("Noncanonical market outlook date")
    return x


def _text(value, name, minimum=1, maximum=1000):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError("Market outlook requires " + name)
    return value


def _https(url):
    s = urlsplit(_text(url, "HTTPS evidence URL"))
    if s.scheme != "https" or not s.hostname or s.username or s.password:
        raise ValueError("Market outlook evidence must be HTTPS")
    return url


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("Market outlook prices must be positive finite numbers")
    return value


def _source(source, *, asof):
    if not isinstance(source, dict):
        raise ValueError("Market outlook needs source record")
    _text(source.get("title"), "source title", maximum=180)
    _https(source.get("url"))
    published = _day(source.get("published_on"))
    checked = _day(source.get("checked_on"))
    if published > asof or checked > asof or checked < published:
        raise ValueError("Market outlook evidence cannot postdate publication")
    return source["url"]


def _forecast_result(forecast, observed):
    """The precise boundary is frozen at issue time: flat means +/-0.5%."""
    start = forecast["baseline"]["value"]
    change = 100 * (observed / start - 1)
    direction = forecast["direction"]
    good = (
        (direction == "上昇" and change > 0.5)
        or (direction == "下落" and change < -0.5)
        or (direction == "横ばい" and -0.5 <= change <= 0.5)
    )
    return "的中" if good else "外れ"


def load_market_outlooks(root, *, today=None):
    """Load evidence-checked chronological events; never infer a hit from trend."""
    f = Path(root) / PATH
    if not f.is_file():
        return []
    events = json.loads(f.read_text(encoding="utf-8"))
    if not isinstance(events, list):
        raise ValueError("Market outlook history must be a list")
    now = today or datetime.now(ZoneInfo("Asia/Tokyo")).date()
    forecasts = {}
    latest_stance = {}
    verified = set()
    seen = set()
    last_at = None
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("Invalid market outlook entry")
        kind, eid = event.get("type"), event.get("id")
        if not isinstance(eid, str) or eid in seen:
            raise ValueError("Duplicate or missing market outlook identity")
        seen.add(eid)
        at = _timestamp(event.get("at"))
        if at.date() > now or (last_at is not None and at < last_at):
            raise ValueError("Market outlook events must be chronological and not future dated")
        last_at = at
        if kind == "forecast":
            if not FORECAST_ID.fullmatch(eid) or eid[:10] != at.date().isoformat() or eid in forecasts:
                raise ValueError("Daily forecast must have one real JST-date identity")
            target_day = _day(event.get("reviewed_on"))
            if target_day <= at.date():
                raise ValueError("Daily forecast judgement must be after publication")
            if event.get("subject") != "TOPIX" or event.get("direction") not in DIRECTIONS:
                raise ValueError("Daily forecast must be a verifiable TOPIX direction")
            if event.get("stance") not in STANCES:
                raise ValueError("Daily forecast stance must be clear")
            for k, n in (("statement", 25), ("method", 35),
                         ("reason", 35), ("invalidated_if", 25)):
                _text(event.get(k), k, minimum=n)
            if "0.5" not in event["method"]:
                raise ValueError("Daily TOPIX direction requires the fixed 0.5 percent threshold")
            scenarios = event.get("scenarios")
            if not isinstance(scenarios, dict) or set(scenarios) != set(STANCES):
                raise ValueError("Daily forecast needs three scenarios")
            for val in scenarios.values():
                _text(val, "scenario", minimum=18)
            sources = event.get("sources")
            if not isinstance(sources, list) or not sources:
                raise ValueError("Daily forecast needs public evidence")
            urls = {_source(src, asof=at.date()) for src in sources}
            if len(urls) != len(sources):
                raise ValueError("Duplicate market outlook source")
            baseline = event.get("baseline")
            if not isinstance(baseline, dict):
                raise ValueError("Daily forecast requires a verified baseline")
            if _day(baseline.get("date")) > at.date():
                raise ValueError("Baseline cannot be from the future")
            _number(baseline.get("value"))
            if baseline.get("unit") != "ポイント" or baseline.get("source_url") not in urls:
                raise ValueError("Baseline needs TOPIX units and cited public source")
            _text(baseline.get("method"), "baseline method", minimum=20)
            forecasts[eid] = event
            latest_stance[eid] = event["stance"]
        elif kind == "review":
            if not REVIEW_ID.fullmatch(eid):
                raise ValueError("Market review identity must include real day and slot")
            if eid[:10] != at.date().isoformat():
                raise ValueError("Market review ID and date differ")
            fid = event.get("forecast_id")
            if fid not in forecasts or fid in verified:
                raise ValueError("Market review references absent or already verified forecast")
            fc = forecasts[fid]
            if at <= _timestamp(fc["at"]):
                raise ValueError("Market review must follow its original forecast")
            if event.get("stance") not in STANCES or event.get("trend") not in TRENDS:
                raise ValueError("Market review needs stance and provisional trend")
            _text(event.get("reason"), "review reason", minimum=35)
            source = event.get("source")
            _source(source, asof=at.date())
            if event["stance"] != latest_stance[fid]:
                _text(event.get("change_reason"), "stance-change reason", minimum=35)
            if event.get("observed_value") is not None:
                _number(event["observed_value"])
                if _day(event.get("observed_on")) > at.date():
                    raise ValueError("Observed value cannot be future-dated")
            outcome = event.get("result")
            if outcome is not None:
                if at.date() < _day(fc["reviewed_on"]):
                    raise ValueError("No verified result before fixed judgement date")
                if not isinstance(outcome, dict):
                    raise ValueError("Market result must contain evidence")
                value = _number(outcome.get("close"))
                if outcome.get("observed_on") != fc["reviewed_on"]:
                    raise ValueError("Market outcome must use its original evaluation day")
                if outcome.get("source_url") != source["url"]:
                    raise ValueError("Market outcome must cite the checked source")
                if outcome.get("outcome") != _forecast_result(fc, value):
                    raise ValueError("Market result inconsistent with frozen criterion")
                _text(outcome.get("explanation"), "result explanation", minimum=35)
                verified.add(fid)
            latest_stance[fid] = event["stance"]
        else:
            raise ValueError("Unknown market outlook event type")
    return events


def view_models(events, *, before=None):
    """Historical snapshots must not display reviews published later."""
    forecasts = {}
    for entry in events:
        if before is not None and _timestamp(entry["at"]) > before:
            continue
        if entry["type"] == "forecast":
            forecasts[entry["id"]] = {"forecast": entry, "updates": []}
        elif entry["forecast_id"] in forecasts:
            forecasts[entry["forecast_id"]]["updates"].append(entry)
    return sorted(forecasts.values(), key=lambda f: f["forecast"]["at"], reverse=True)


def _event_day(at):
    return _timestamp(at).strftime("%Y年%m月%d日 %H:%M")


def _status(row):
    updates = row["updates"]
    last = updates[-1] if updates else None
    if last and last.get("result"):
        return "判定済み：" + last["result"]["outcome"]
    return "結果待ち"


def render_market_outlook_board(events, *, limit=12, before=None):
    reports = view_models(events, before=before)
    if limit:
        reports = reports[:limit]
    cards = []
    for item in reports:
        fc, updates = item["forecast"], item["updates"]
        latest = updates[-1] if updates else None
        now_stance = latest["stance"] if latest else fc["stance"]
        trend = latest["trend"] if latest else "まだ途中経過なし"
        state = _status(item)
        changed = "変更あり" if now_stance != fc["stance"] else "変更なし"
        source_html = "".join(
            f'<li><a href="{escape(src["url"],quote=True)}" target="_blank" rel="noopener noreferrer external">{escape(src["title"])}</a>'
            f'（{escape(src["checked_on"])}確認）</li>'
            for src in fc["sources"]
        )
        history = [
            f'<li><time datetime="{escape(fc["at"])}">{_event_day(fc["at"])}</time>'
            f'<strong>当初：{escape(fc["stance"])}</strong><span>{escape(fc["reason"])}</span></li>'
        ]
        for e in updates:
            explanation = e.get("change_reason") or e["reason"]
            history.append(
                f'<li><time datetime="{escape(e["at"])}">{_event_day(e["at"])}</time>'
                f'<strong>{escape(e["stance"])} ／ {escape(e["trend"])}</strong>'
                f'<span>{escape(explanation)}</span>'
                f'<a href="{escape(e["source"]["url"],quote=True)}" target="_blank" rel="noopener noreferrer external">'
                '判断材料の出典</a></li>'
            )
        if latest and latest.get("result"):
            final = latest["result"]
            history.append(
                f'<li><strong>判定：{escape(final["outcome"])}</strong>'
                f'<span>対象日のTOPIX終値 {final["close"]:,.2f}ポイント。'
                f'{escape(final["explanation"])}</span></li>'
            )
        cards.append(
            '<article class="daily-forecast-card">'
            f'<div class="daily-forecast-top"><span>実際の予測日 {_event_day(fc["at"])}</span><strong>{escape(state)}</strong></div>'
            f'<h3>{escape(fc["statement"])}</h3>'
            '<div class="daily-forecast-direction">'
            f'<div><span>当初の見立て</span><strong>{escape(fc["stance"])}</strong></div>'
            f'<span aria-hidden="true">→</span><div><span>現在の見立て</span><strong>{escape(now_stance)}</strong></div></div>'
            f'<p class="daily-forecast-check"><strong>{escape(changed)}</strong> ／ '
            f'途中経過：{escape(trend)} ／ '
            f'判定日：<time datetime="{fc["reviewed_on"]}">{escape(fc["reviewed_on"])}</time></p>'
            f'<p>基準：TOPIX {_day(fc["baseline"]["date"]).isoformat()}終値 '
            f'{fc["baseline"]["value"]:,.2f}ポイント。'
            f'{escape(fc["baseline"]["method"])}</p>'
            f'<p class="daily-forecast-method"><strong>事前に固定した方法：</strong>{escape(fc["method"])}</p>'
            f'<p><strong>反証条件：</strong>{escape(fc["invalidated_if"])}</p>'
            '<details><summary>予測の根拠・3シナリオ・判断履歴</summary>'
            '<div class="daily-forecast-details">'
            f'<p>{escape(fc["reason"])}</p>'
            '<dl class="daily-forecast-scenarios">'
            + "".join(f'<div><dt>{escape(k)}</dt><dd>{escape(fc["scenarios"][k])}</dd></div>' for k in STANCES)
            + '</dl><h4>判断の変化</h4><ol class="daily-forecast-timeline">'
            + "".join(history)
            + '</ol><h4>当初の資料</h4><ul class="daily-forecast-sources">' + source_html
            + '</ul></div></details></article>'
        )
    lead = (
        '<p class="daily-forecast-intro">公表時点の見通しと、その後に分かった材料を日付順に掲載します。'
        '結果は当初決めた日に、確認できる実際の数字で判断します。</p>'
    )
    empty = '<p class="market-desk-empty">新しい市場見通しの記事はまだありません。<a href="/serials/market-lab/">企業分析を読む</a></p>'
    return (
        '<section class="daily-forecast-board" id="daily-forecasts" aria-labelledby="daily-forecasts-title">'
        '<div class="market-paper-heading"><h2 id="daily-forecasts-title">日々の見通し</h2></div>'
        + lead + ('<div class="daily-forecast-grid">' + "".join(cards) + '</div>' if cards else empty)
        + f'<p><a class="serial-feed-link" href="{BASE_URL}">予測・判断履歴を一覧で確認</a></p>'
        '</section>'
    )


def render_market_outlook_archive(events, layout):
    content = (
        '<div class="container serials-page market-desk-archive">'
        '<nav class="serial-breadcrumb"><a href="/">トップ</a> / '
        '<a href="/serials/market-lab/">未来相場観測室</a></nav>'
        '<header class="serials-intro"><h1>日々の予測・振り返り</h1>'
        '<p>日付も市場も現実のもの。未検証・途中評価・判定済みを分けて記録します。</p>'
        + render_market_share(BASE_URL, "日々の予測・振り返り") + '</header>'
        + render_market_outlook_board(events, limit=0)
        + '</div>'
    )
    return layout("毎日の相場予測と変更履歴 | 未来相場観測室",
                  "日本市場の予測、途中経過、判断変更、後日の的中・外れを事前基準で記録。",
                  BASE_URL, content, active="serials",
                  serial={"slug":"market-lab","kind":"analysis","title":"未来相場観測室"})


def build_market_outlooks(events, layout, write):
    write(BASE_URL.lstrip("/") + "index.html", render_market_outlook_archive(events, layout))
