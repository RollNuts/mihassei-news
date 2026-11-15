"""Append-only intermediate market outlooks, separate from formal forecast verdicts.

A daily observation can support or challenge a thesis without creating a
fictitious "win" ahead of the published prediction's review date.
No prices are fetched and there is no paid dependency.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from html import escape
import json
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

DATA_PATH = Path("content/market_outlook.json")
STANCES = ("強気", "中立", "弱気", "判断保留")
SIGNALS = {
    "supporting": "支持材料あり",
    "challenging": "反証材料あり",
    "mixed": "材料が交錯",
    "unavailable": "判定材料不足",
}


def _date(raw):
    if not isinstance(raw, str):
        raise ValueError("Outlook date must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("Invalid market outlook date") from exc
    if parsed.isoformat() != raw:
        raise ValueError("Noncanonical market outlook date")
    return parsed


def _text(item, field, *, limit=600):
    raw = item.get(field)
    if not isinstance(raw, str) or not raw.strip() or len(raw) > limit:
        raise ValueError("Market outlook requires " + field)
    return raw


def validate_outlook_records(entries, series, *, today=None):
    """Validate dates, an inspectable source, and episode linkage; no truth claims."""
    if not isinstance(entries, list):
        raise ValueError("Market outlook archive must be an array")
    today = today or datetime.now(ZoneInfo("Asia/Tokyo")).date()
    published = {
        ep["slug"]: ep
        for s in series if s.get("kind") == "analysis"
        for ep in s.get("episodes", [])
    }
    last_day = {}
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("Market outlook item must be an object")
        slug = _text(item, "episode_slug", limit=120)
        if slug not in published:
            raise ValueError("Market outlook refers to unknown analysis episode")
        day = _date(item.get("observed_on"))
        checked = _date(item.get("checked_on"))
        source_day = _date(item.get("source_published_on"))
        episode_day = _date(published[slug]["published_at"])
        if not episode_day <= source_day <= checked <= day <= today:
            raise ValueError("Market observation outside source or publication window")
        if slug in last_day and day <= last_day[slug]:
            raise ValueError("Market outlook history must be chronologically appended")
        last_day[slug] = day
        if item.get("stance") not in STANCES or item.get("signal") not in SIGNALS:
            raise ValueError("Invalid outlook stance or evidence signal")
        for field in ("summary", "reason", "next_trigger", "source_title"):
            _text(item, field)
        source = _text(item, "source_url", limit=1200)
        parsed = urlsplit(source)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Market outlook evidence requires a valid HTTPS source")
        if "result" in item or "hit_rate" in item or "verified" in item:
            raise ValueError("Intermediate market outlook cannot announce a forecast verdict")
    return entries


def load_market_outlook(root: Path, series, *, today=None):
    filepath = Path(root) / DATA_PATH
    if not filepath.exists():
        return {}
    entries = json.loads(filepath.read_text(encoding="utf-8"))
    validate_outlook_records(entries, series, today=today)
    grouped = defaultdict(list)
    for item in entries:
        grouped[item["episode_slug"]].append(item)
    return dict(grouped)


def guard_market_outlook_history(old, new):
    """Previously published daily observations may only gain appended entries."""
    if not isinstance(old, list) or not isinstance(new, list):
        raise ValueError("Outlook history must be arrays")
    if new[:len(old)] != old:
        raise ValueError("Previously published market outlook rewritten or removed")
    return len(old)


def _stance_change(history, index):
    current = history[index]
    if index == 0:
        return "観測の初回記録"
    old_stance = history[index - 1]["stance"]
    new_stance = current["stance"]
    if old_stance == new_stance:
        return "見方は据え置き"
    return f"見方を変更：{old_stance} → {new_stance}"


def _stance_scale(stance):
    """Discrete opinion scale, intentionally not a probability or return chart."""
    labels = []
    for name in ("強気", "中立", "弱気"):
        active = stance == name
        labels.append(
            f'<span class="market-stance-option{" is-current" if active else ""}"'
            f'{" aria-current=\"true\"" if active else ""}>{escape(name)}</span>'
        )
    return '<div class="market-stance-scale" aria-label="見方（確率ではありません）">' + "".join(labels) + "</div>"


def render_market_outlook_index(series, observations):
    if series.get("kind") != "analysis":
        return ""
    items = []
    for episode in reversed(series["episodes"]):
        history = observations.get(episode["slug"], [])
        href = f'/serials/{escape(series["slug"])}/{escape(episode["slug"])}/#forecast-progress'
        if history:
            latest = history[-1]
            tag = SIGNALS[latest["signal"]]
            mode = latest["signal"]
            statement = escape(latest["summary"])
            stamped = (f'<time datetime="{latest["observed_on"]}">'
                       f'{latest["observed_on"]}</time>')
            stance = escape(latest["stance"])
            scale = _stance_scale(latest["stance"])
            switch = escape(_stance_change(history, len(history)-1))
        else:
            tag = "資料確認待ち"
            mode = "unavailable"
            statement = "途中経過の確認済み記録はありません。判定条件は公開時のままです。"
            stamped = "更新記録なし"
            stance = "判断保留"
            scale = _stance_scale("判断保留")
            switch = "更新待ち"
        items.append(
            f'<article class="market-outlook-card market-outlook--{mode}">'
            f'<div class="market-outlook-top"><span>予測 #{episode["number"]:02d}</span>'
            f'<span class="market-signal-label">{tag}</span></div>'
            f'<h3><a href="{href}">{escape(episode["title"])}</a></h3>'
            f'<p class="market-outlook-stance"><strong>現在の見方：{stance}</strong> ／ {stamped}</p>'
            f'{scale}<p>{statement}</p><p class="market-outlook-change">{switch}</p>'
            f'<a class="market-outlook-more" href="{href}">情勢の変化と根拠を見る →</a>'
            f'</article>'
        )
    return (
        '<section class="market-outlook-section" id="market-outlook"'
        ' aria-labelledby="market-outlook-heading">'
        '<div class="section-head"><h2 id="market-outlook-heading">予測は今、どう見える？</h2></div>'
        '<p class="market-outlook-lead">途中経過は「追い風・逆風」などの確認材料と、強気・中立・弱気の見方を記録したものです。'
        '的中・外れを確定する判定とは別です。過去の予測は変更しません。</p>'
        '<div class="market-outlook-grid">' + "".join(items) + '</div></section>'
    )


def render_market_outlook_episode(episode, history):
    if not history:
        return (
            '<section class="market-outlook-timeline" id="forecast-progress">'
            '<h2>その後の情勢と見方</h2>'
            '<p>確認済みの途中経過はまだありません。予測の的中・外れは判定日の資料で確認します。</p>'
            '</section>'
        )
    rows = []
    for idx in reversed(range(len(history))):
        entry = history[idx]
        signal = SIGNALS[entry["signal"]]
        change = _stance_change(history, idx)
        rows.append(
            f'<li class="market-outlook-event market-outlook--{entry["signal"]}">'
            f'<div class="market-outlook-event-head"><time datetime="{entry["observed_on"]}">'
            f'{entry["observed_on"]}</time><span>{escape(signal)}</span></div>'
            f'<h3>{escape(change)} ／ {escape(entry["stance"])}</h3>'
            f'{_stance_scale(entry["stance"])}'
            f'<p><strong>何が起きた？</strong> {escape(entry["summary"])}</p>'
            f'<p><strong>そう判断した理由：</strong> {escape(entry["reason"])}</p>'
            f'<p><strong>次に見方を変える条件：</strong> {escape(entry["next_trigger"])}</p>'
            f'<p class="market-outlook-source">資料公表：{escape(entry["source_published_on"])}'
            f' ／ 確認：{escape(entry["checked_on"])} ／ '
            f'<a href="{escape(entry["source_url"], quote=True)}" target="_blank" rel="noopener noreferrer external">'
            f'{escape(entry["source_title"])}</a></p></li>'
        )
    return (
        '<section class="market-outlook-timeline" id="forecast-progress">'
        '<h2>その後の情勢と見方</h2>'
        '<p class="market-outlook-lead">強気・中立・弱気は公開時の予測を変更するものではありません。'
        '予測の判定前は「未検証」です。途中の支持材料があっても的中とは数えません。</p>'
        '<ol class="market-outlook-events">' + "".join(rows) + '</ol></section>'
    )
