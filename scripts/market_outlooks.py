"""Append-only daily market forecasts, snapshots, and evidence-linked stance changes.

This ledger is separate from the immutable long-horizon serial forecasts.
It never fetches prices. Outcomes are computed only from explicitly recorded
official evidence after the fixed judgement date. All displayed dates are real
JST dates, not fictional newspaper dates.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from html import escape
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
import json
import math
import re

if __package__:
    from .market_sharing import render_market_share
    from .forecast_ledger import reader_reason_sentences, forecast_horizon
else:
    from market_sharing import render_market_share
    from forecast_ledger import reader_reason_sentences, forecast_horizon

PATH = Path("content/market_outlooks.json")
JST = timedelta(hours=9)
STANCES = ("強気", "中立", "弱気")
TRENDS = ("暫定的に整合", "暫定的に逆行", "判断材料不足")
DIRECTIONS = ("上昇", "下落", "横ばい")
BASE_URL = "/serials/market-lab/outlooks/"
FORECAST_ID = re.compile(r"20\d{2}-\d{2}-\d{2}-topix\Z")
REVIEW_ID = re.compile(r"20\d{2}-\d{2}-\d{2}-(?:am|open|noon|close)-[a-z0-9-]+\Z")


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


def _observed_direction(baseline, close):
    # Compare the recorded decimal prices directly. Exactly ±0.5% is flat.
    start = Decimal(str(baseline))
    movement = (Decimal(str(close)) - start) * 200
    if movement > start:
        return "上昇"
    if movement < -start:
        return "下落"
    return "横ばい"


def _change_label(baseline, close):
    start = Decimal(str(baseline))
    change = (Decimal(str(close)) - start) * 100 / start
    rounded = change.quantize(Decimal("0.01"))
    # Rounding must not make a directional verdict look exactly flat.
    if abs(rounded) == Decimal("0.5") and abs(change) > abs(rounded):
        return "＋0.5%超" if change > 0 else "−0.5%未満"
    if not rounded:
        return "0.00%" if not change else "変化幅0.01%未満"
    return ("＋" if change > 0 else "−") + f'{abs(rounded):.2f}%'


def _forecast_result(forecast, observed):
    actual = _observed_direction(forecast["baseline"]["value"], observed)
    return "的中" if forecast["direction"] == actual else "外れ"


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
    """Keep each forecast's visible reviews and the recorded view time together."""
    visible = [entry for entry in events
               if before is None or _timestamp(entry["at"]) <= before]
    asof = before if before is not None else max(
        (_timestamp(entry["at"]) for entry in visible), default=None)
    forecasts = {}
    for entry in visible:
        if entry["type"] == "forecast":
            forecasts[entry["id"]] = {"forecast": entry, "updates": [], "asof": asof}
        elif entry["forecast_id"] in forecasts:
            forecasts[entry["forecast_id"]]["updates"].append(entry)
    return sorted(forecasts.values(), key=lambda f: f["forecast"]["at"], reverse=True)


def _event_day(at):
    return _timestamp(at).strftime("%Y年%m月%d日 %H:%M")


def _confirmed_result(row):
    """Only an evidenced final verdict counts; interim sentiment never does."""
    fc = row["forecast"]
    latest = row["updates"][-1] if row["updates"] else None
    result = latest.get("result") if latest else None
    if not isinstance(result, dict) or result.get("outcome") not in ("的中", "外れ"):
        return None
    try:
        close = _number(result.get("close"))
        if (_timestamp(latest["at"]).date() < _day(fc["reviewed_on"])
                or result.get("observed_on") != fc["reviewed_on"]
                or _https(result.get("source_url")) != latest["source"]["url"]
                or result["outcome"] != _forecast_result(fc, close)):
            return None
        _text(result.get("explanation"), "result explanation", minimum=35)
    except (KeyError, ValueError, TypeError):
        return None
    return result


def _status(row):
    result = _confirmed_result(row)
    if result:
        return "判定済み：" + result["outcome"]
    latest = row["updates"][-1] if row["updates"] else None
    outcome = latest.get("result") if latest else None
    if isinstance(outcome, dict) and outcome.get("outcome") in ("判定不能", "保留"):
        return "判定保留"
    if row["asof"].astimezone(ZoneInfo("Asia/Tokyo")).date() >= _day(row["forecast"]["reviewed_on"]):
        return "結果日到来・確認待ち"
    return "結果待ち"


def _forecast_anchor(forecast):
    identity = forecast.get("id")
    if not isinstance(identity, str) or not FORECAST_ID.fullmatch(identity):
        raise ValueError("Unsafe market forecast anchor")
    _day(identity[:10])
    return "forecast-" + identity


def market_forecast_url(forecast):
    return BASE_URL + "#" + _forecast_anchor(forecast)


def forecast_reader_statement(forecast):
    """Display the original direction and target day without its test machinery."""
    target = _day(forecast["reviewed_on"])
    year = f'{target.year}年' if target.year != _timestamp(forecast["at"]).year else ''
    return f'{year}{target.month}月{target.day}日の{forecast["subject"]}は{forecast["direction"]}を予想。'


def forecast_reader_reason(forecast):
    """Keep up to two complete existing market-material sentences, never new claims."""
    return reader_reason_sentences(forecast["reason"])


def render_market_accuracy(events, *, before=None):
    """Accuracy for all evidenced short-term verdicts in this historical view."""
    rows = [row for row in view_models(events, before=before)
            if forecast_horizon(row["forecast"]["at"], row["forecast"]["reviewed_on"]) == "短期"]
    settled = [(row["forecast"], result) for row in rows if (result := _confirmed_result(row)) is not None]
    hits = sum(result["outcome"] == "的中" for _, result in settled)
    total = len(settled)
    pending = len(rows) - total
    subjects = {row["forecast"]["subject"] for row in rows}
    scope = escape(next(iter(subjects))) + 'の短期予想' if len(subjects) == 1 else '短期相場予想'
    percent = (f'{hits * 100 / total:.1f}'.rstrip('0').rstrip('.') + '%') if total else '—'
    count = f'判定済み{total}件のうち{hits}件が的中' if total else '確定0件'
    bar = (f'<meter class="market-score-meter" min="0" max="{total}" value="{hits}" aria-hidden="true"></meter>'
           if total else '')
    counts = ([f'外れ{total-hits}件'] if total else []) + ([f'未確定{pending}件は集計外'] if pending else [])
    when = ''
    if before is not None:
        asof = before.astimezone(ZoneInfo("Asia/Tokyo"))
        when = (f'<time datetime="{asof.isoformat(timespec="seconds")}">'
                f'{asof.month}月{asof.day}日 {asof:%H:%M} JST 掲載時点</time>')
    example = ''
    if settled:
        fc, result = max(settled, key=lambda pair: (pair[0]["reviewed_on"], pair[0]["at"]))
        day = _day(fc["reviewed_on"])
        move = _change_label(fc["baseline"]["value"], result["close"])
        actual = _observed_direction(fc["baseline"]["value"], result["close"])
        example = (f'<a class="market-score-example" href="{market_forecast_url(fc)}">'
                   f'{day.month}/{day.day}判定　{escape(fc["direction"])}予想 → {actual}（{move}）で{result["outcome"]}</a>')
    return ('<div class="market-reader-accuracy" aria-label="短期相場予想の的中率">'
            f'<div class="market-score-heading"><b>{scope}の的中率</b>{when}</div>'
            f'<div class="market-score-value"><strong>{percent}</strong><span class="market-reader-count">{count}</span></div>'
            + bar + (f'<small>{"　／　".join(counts)}</small>' if counts else '')
            + '<p class="market-score-basis">予想時に記録した基準終値と、判定日の終値を比較します。'
              '対象は公表から7日以内に判定する予想です。実際の方向が予想と合えば的中です。</p>'
            + '<ul class="market-score-ranges" aria-label="終値の変化率と判定">'
              '<li>上昇 <b>＋0.5%超</b></li><li>横ばい <b>±0.5%以内</b></li><li>下落 <b>−0.5%未満</b></li></ul>'
            + example + '</div>')


def render_market_forecast_assessment(row):
    """Distinguish the original rationale, latest recorded assessment and verdict."""
    fc = row["forecast"]
    result = _confirmed_result(row)
    reason = forecast_reader_reason(fc)
    reason_html = f'<p class="market-reader-reason"><strong>当初の理由</strong>{escape(reason)}</p>' if reason else ''
    latest = row["updates"][-1] if row["updates"] else None
    assessment = '<p class="market-reader-date">この予想の更新評価はまだありません。</p>'
    if latest:
        source = latest["source"]
        date_label = '結果確認：' if result else '最新の評価：'
        reason_label = '判定の理由' if result else '評価の理由'
        source_label = '結果の出典：' if result else '評価の出典：'
        provisional = (f' ／ {escape(latest["stance"])}（{escape(latest["trend"])}）'
                       if not result else '')
        assessment = (
            f'<p class="market-reader-date">{date_label}'
            f'<time datetime="{escape(latest["at"], quote=True)}">{_event_day(latest["at"])}</time> JST'
            f'{provisional}</p>'
            f'<p class="market-reader-reason"><strong>{reason_label}</strong>{escape(latest["reason"])}</p>'
            '<p class="market-reader-sources">'
            f'<a href="{escape(_https(source["url"]), quote=True)}" target="_blank" '
            f'rel="noopener noreferrer external">{source_label}{escape(source["title"])}</a></p>'
        )
    status = _status(row)
    outcome = 'hit' if result and result["outcome"] == '的中' else 'miss' if result else 'pending'
    base = fc["baseline"]
    base_day = _day(base["date"])
    basis = (f'<span>基準終値 <time datetime="{base_day}">{base_day.month}/{base_day.day}</time> '
             f'{base["value"]:,.2f}ポイント</span>')
    confirmed = (f'<span>確認済み終値 {result["close"]:,.2f}ポイント（{_change_label(base["value"], result["close"])}）</span>'
                 if result else '')
    sources = ''.join(
        f'<a href="{escape(_https(source["url"]),quote=True)}" title="{escape(source["title"],quote=True)}" '
        f'target="_blank" rel="noopener noreferrer external">資料{index}</a>'
        for index, source in enumerate(fc["sources"], 1)
    )
    asof = row["asof"].astimezone(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
    actual = ''
    if result:
        direction = _observed_direction(fc["baseline"]["value"], result["close"])
        actual = f'<span class="market-direction-pair">予想 <b>{escape(fc["direction"])}</b> → 実際 <b>{direction}</b></span>'
    return (f'<div class="market-reader-result" data-outcome="{outcome}"><strong>{escape(status)}</strong>'
            + actual + f'<span>結果日：<time datetime="{escape(fc["reviewed_on"], quote=True)}">{escape(fc["reviewed_on"])}</time></span>'
            + basis + confirmed + '</div>' + reason_html
            + '<details class="market-reader-evidence"><summary>経過と出典</summary>'
            + assessment + f'<p class="market-reader-sources">当初の出典 {sources}</p>'
            + f'<p class="market-reader-date">集計時点：<time datetime="{asof}">{_event_day(asof)}</time> JST</p></details>')


def _reader_forecast_card(row):
    fc = row["forecast"]
    return (f'<article class="market-reader-card" id="{_forecast_anchor(fc)}">'
            f'<p class="market-reader-date"><span class="market-reader-horizon">'
            f'{forecast_horizon(fc["at"],fc["reviewed_on"])}</span> '
            f'<time datetime="{escape(fc["at"],quote=True)}">'
            f'{_event_day(fc["at"])}</time>の当初の予想</p>'
            f'<h3 class="market-reader-direction">{escape(forecast_reader_statement(fc))}</h3>'
            + render_market_forecast_assessment(row) + '</article>')


def render_market_outlook_board(events, *, limit=3, before=None, link_to_archive=True):
    if type(limit) is not int or limit < 0:
        raise ValueError("Forecast display limit must be nonnegative")
    reports = view_models(events, before=before)
    visible = min(limit, 3) if limit else 3
    cards = [_reader_forecast_card(row) for row in reports]
    recent = ('<div class="market-reader-cards">' + ''.join(cards[:visible]) + '</div>') if cards else (
        '<p class="market-desk-empty">新しい相場予想はまだありません。'
        '<a href="/serials/market-lab/">企業分析を読む</a></p>')
    past = ('<details class="market-reader-past"><summary>過去の予想</summary>'
            '<div class="market-reader-cards">' + ''.join(cards[visible:]) + '</div></details>') if cards[visible:] else ''
    archive = f'<p><a class="serial-feed-link" href="{BASE_URL}">相場の予想をすべて見る</a></p>' if link_to_archive else ''
    return ('<section class="daily-forecast-board" id="daily-forecasts" aria-labelledby="daily-forecasts-title">'
            '<div class="market-paper-heading"><h2 id="daily-forecasts-title">相場の予想と結果</h2></div>'
            + render_market_accuracy(events, before=before) + recent + past
            + archive + '</section>')


def render_market_outlook_archive(events, layout):
    latest_update = ''
    if events:
        latest = max(events, key=lambda event: _timestamp(event["at"]))
        target = {"id": latest["forecast_id"] if latest["type"] == "review" else latest["id"]}
        latest_update = (
            '<p class="market-latest-update"><strong>予想・結果の最終更新</strong>'
            f'<a href="{market_forecast_url(target)}">'
            f'<time datetime="{escape(latest["at"], quote=True)}">{_event_day(latest["at"])}</time> JST</a></p>')
    content = ('<div class="container serials-page market-desk-archive">'
               '<nav class="serial-breadcrumb"><a href="/">トップ</a> / '
               '<a href="/serials/market-lab/">未来相場観測室</a></nav>'
               '<header class="serials-intro"><h1>相場の予想と結果</h1>'
               + latest_update
               + render_market_share(BASE_URL, "相場の予想と結果") + '</header>'
               + render_market_outlook_board(events, link_to_archive=False) + '</div>')
    return layout("相場の予想と結果 | 未来相場観測室",
                  "日本市場の予想、その理由、確認できた結果と的中率。",
                  BASE_URL, content, active="serials",
                  serial={"slug":"market-lab","kind":"analysis","title":"未来相場観測室"})


def build_market_outlooks(events, layout, write):
    write(BASE_URL.lstrip("/") + "index.html", render_market_outlook_archive(events, layout))
