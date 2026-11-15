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
        if not (episode_day <= day <= today and source_day <= checked <= day):
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
        current_class = " is-current" if active else ""
        current_attr = ' aria-current="true"' if active else ""
        labels.append(
            f'<span class="market-stance-option{current_class}"{current_attr}>{escape(name)}</span>'
        )
    return '<div class="market-stance-scale" aria-label="見方（確率ではありません）">' + "".join(labels) + "</div>"


def render_market_outlook_index(series, observations):
    """Public editorial update strip, never a wall of empty signal cards."""
    if series.get("kind") != "analysis":
        return ""
    updates = []
    for episode in reversed(series["episodes"]):
        history = observations.get(episode["slug"], [])
        if not history:
            continue
        recent = history[-1]
        # The original IR deck used to write the forecast is not a new
        # development just because an editor checked it again yesterday.
        if (recent["source_published_on"] <= episode["published_at"]
                or recent["signal"] == "unavailable"):
            continue
        href = f'/serials/{escape(series["slug"])}/{escape(episode["slug"])}/#forecast-progress'
        updates.append(
            '<li><div class="market-paper-update-meta">'
            f'<time datetime="{escape(recent["observed_on"])}">{escape(recent["observed_on"])}</time>'
            f' ／ {escape(SIGNALS[recent["signal"]])}</div>'
            f'<h3><a href="{href}">{escape(episode["title"])}</a></h3>'
            f'<p>{escape(recent["summary"])}</p>'
            f'<a href="{href}">資料と見方の変化を見る →</a></li>'
        )
    return (
        '<section class="market-outlook-section market-paper-updates" id="market-outlook"'
        ' aria-labelledby="market-outlook-heading">'
        '<div class="market-paper-heading"><h2 id="market-outlook-heading">見通しの変化</h2></div>'
        + ('<ol class="market-paper-update-list">' + "".join(updates) + '</ol>'
           if updates else
           '<p class="market-paper-section-intro">当初の見通しを変える新たな公表資料は、'
           'まだありません。各記事で今後の確認点を読むことができます。</p>')
        + '</section>'
    )


def render_market_outlook_episode(episode, history):
    if not history:
        return (
            '<section class="market-outlook-timeline" id="forecast-progress">'
            '<h2>その後の動き</h2>'
            '<p>この予測を見直す新しい発表はまだありません。結果は記事に記した期日に確認します。</p>'
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
    has_new_evidence = (
        episode is not None and any(
            entry["source_published_on"] > episode["published_at"]
            and entry["signal"] != "unavailable"
            for entry in history
        )
    )
    intro = (
        "新しい資料をもとに見通しを見直しています。"
        if has_new_evidence else
        "掲載後に見通しを変える新しい確定材料はありません。"
    )
    return (
        '<section class="market-outlook-timeline" id="forecast-progress">'
        '<h2>その後の動き</h2>'
        f'<p class="market-outlook-lead">{intro}</p>'
        '<details class="market-paper-history"><summary>資料の確認日と見方の履歴を見る</summary>'
        '<ol class="market-outlook-events">' + "".join(rows) + '</ol></details></section>'
    )
