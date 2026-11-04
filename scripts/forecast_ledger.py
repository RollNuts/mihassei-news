"""Transparent, evidence-aware public log of market predictions.

This module never downloads prices, invents outcomes or calculates a misleading
accuracy rate. A forecast can be marked verified only with explicit evidence.
The markup is static, accessible, and works without JavaScript or paid APIs.
"""
from __future__ import annotations

import html
from datetime import date, datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

SITE = "https://veripsa.com"


def _escape(value):
    return html.escape(str(value), quote=True)


def _review_day(value):
    if not isinstance(value, str):
        raise ValueError("Forecast review date must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid forecast review date") from exc
    if parsed.isoformat() != value:
        raise ValueError("Noncanonical forecast review date")
    return parsed


def _verified_outcome(forecast):
    """Verified results are never accepted as bare claims without a source."""
    outcome = forecast.get("result")
    status = str(forecast.get("status", "未検証"))
    if outcome is None:
        if status not in ("未検証", "検証中"):
            raise ValueError("A verified forecast requires a cited result")
        return None
    if status != "検証済み":
        raise ValueError("Result requires status='検証済み'")
    if not isinstance(outcome, dict) or outcome.get("outcome") not in (
        "的中", "外れ", "判定不能",
    ):
        raise ValueError("Verified result requires a clear outcome")
    if not isinstance(outcome.get("explanation"), str) or len(outcome["explanation"].strip()) < 25:
        raise ValueError("Verified result requires reasoning")
    if not isinstance(outcome.get("evidence"), list) or not outcome["evidence"]:
        raise ValueError("Verified result requires independently inspectable evidence")
    _review_day(outcome.get("verified_on"))
    for item in outcome["evidence"]:
        if not isinstance(item, dict) or not item.get("title"):
            raise ValueError("Evidence needs a title")
        url = urlsplit(str(item.get("url", "")))
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("Verification evidence must be a valid HTTPS URL")
    return outcome


def prediction_entries(series, *, today=None):
    """Data-derived entries, with no silent outcome fabrication."""
    if series.get("kind") != "analysis":
        return []
    now = today if today is not None else datetime.now(ZoneInfo("Asia/Tokyo")).date()
    if not isinstance(now, date):
        raise ValueError("Expected date for forecast ledger")
    entries = []
    for ep in series["episodes"]:
        fc = ep.get("forecast")
        if fc is None:
            continue
        published = _review_day(ep["published_at"])
        review = _review_day(fc["reviewed_on"])
        if review <= published:
            raise ValueError("Market prediction review must be after publication")
        result = _verified_outcome(fc)
        if result:
            label = "判定済み：" + result["outcome"]
            state = "verified"
        elif now >= review:
            label = "検証期日到来・判定待ち"
            state = "due"
        else:
            label = "検証予定"
            state = "upcoming"
        entries.append({
            "episode": ep,
            "forecast": fc,
            "published": published,
            "review": review,
            "result": result,
            "state": state,
            "state_label": label,
        })
    return sorted(entries, key=lambda row: (row["published"], row["episode"]["number"]), reverse=True)


def render_forecast_ledger(series, *, today=None):
    if series.get("kind") != "analysis":
        return ""
    rows = prediction_entries(series, today=today)
    if not rows:
        return ""
    cards = []
    for row in rows:
        ep = row["episode"]
        fc = row["forecast"]
        slug = _escape(series["slug"])
        ep_slug = _escape(ep["slug"])
        result = row["result"]
        url = f"/serials/{slug}/{ep_slug}/"
        outcome_html = ""
        if result:
            sources = "".join(
                f'<li><a href="{_escape(x["url"])}" target="_blank" rel="noopener noreferrer external">'
                f'{_escape(x["title"])}</a></li>'
                for x in result["evidence"]
            )
            outcome_html = (
                '<div class="forecast-ledger-result">'
                f'<p><strong>判定結果：</strong>{_escape(result["outcome"])}'
                f'（検証日：<time datetime="{_escape(result["verified_on"])}">'
                f'{_escape(result["verified_on"])}</time>）</p>'
                f'<p>{_escape(result["explanation"])}</p>'
                '<p>判定に用いた資料</p><ul>' + sources + '</ul></div>'
            )
        cards.append(
            f'<article class="forecast-ledger-card" aria-label="{_escape(ep["title"])}の検証状況">'
            f'<div class="forecast-ledger-topline"><span>予測 #{ep["number"]:02d}</span>'
            f'<span class="forecast-ledger-status forecast-ledger-status--{row["state"]}">'
            f'{_escape(row["state_label"])}</span></div>'
            f'<h3><a href="{url}">{_escape(ep["title"])}</a></h3>'
            f'<p class="forecast-ledger-statement">{_escape(fc["statement"])}</p>'
            '<dl class="forecast-ledger-facts">'
            f'<div><dt>公開日</dt><dd><time datetime="{row["published"].isoformat()}">'
            f'{row["published"].isoformat()}</time></dd></div>'
            f'<div><dt>検証予定</dt><dd><time datetime="{row["review"].isoformat()}">'
            f'{row["review"].isoformat()}</time></dd></div>'
            '</dl><details class="forecast-ledger-details"><summary>当初の検証方法</summary>'
            f'<p>{_escape(fc["method"])}</p></details>{outcome_html}</article>'
        )
    return (
        '<section class="forecast-ledger" id="forecast-ledger" aria-labelledby="forecast-ledger-heading">'
        '<div class="section-head"><div>'
        '<h2 id="forecast-ledger-heading">予想の検証一覧</h2></div></div>'
        '<p class="forecast-ledger-intro">検証日以降、結果を確認できるまで「判定待ち」と表示します。</p>'
        '<div class="forecast-ledger-grid">' + "".join(cards) + '</div></section>'
    )
