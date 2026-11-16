"""Transparent, evidence-aware public log of market predictions.

This module never downloads prices, invents outcomes or calculates a misleading
accuracy rate. A forecast can be marked verified only with explicit evidence.
The markup is static, accessible, and works without JavaScript or paid APIs.
"""
from __future__ import annotations

import html
import re
from datetime import date, datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

SITE = "https://veripsa.com"


def _escape(value):
    return html.escape(str(value), quote=True)


def reader_reason_sentences(text):
    """Quote up to two complete source sentences, omitting editorial machinery."""
    if not isinstance(text, str):
        return ''
    operational = re.compile(
        r"^(?:この実行|この原稿|当サイトでは|数値を確認できない)|一次資料が足りず|"
        r"(?:必要な資料|公表資料|価格資料|^資料)を取得でき(?:ない|ていない)|"
        r"判定条件|検証方法|判定方法|事前に固定|当初の基準|未検証にとどめ|"
        r"^これは確実な予告ではない"
    )
    sentences = [part.strip() for part in re.findall(r"[^。！？!?]+[。！？!?]?", text) if part.strip()]
    usable = [part for part in sentences if not operational.search(part)]
    return ''.join(usable[:2])


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


def forecast_horizon(published, reviewed_on):
    """Calendar-day display category; preserve the original forecast dates."""
    def day(value):
        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise ValueError("Forecast timestamp needs its original timezone")
            return value.astimezone(ZoneInfo("Asia/Tokyo")).date()
        if isinstance(value, date):
            return value
        if isinstance(value, str) and "T" in value:
            return day(datetime.fromisoformat(value))
        return _review_day(value)
    difference = (day(reviewed_on) - day(published)).days
    if difference < 0:
        raise ValueError("Forecast target cannot predate publication")
    return "短期" if difference <= 7 else "中期" if difference < 180 else "長期"


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
        if result and _review_day(result["verified_on"]) < review:
            raise ValueError("Confirmed result cannot predate the forecast review day")
        if result and _review_day(result["verified_on"]) > now:
            result = None  # A historical view cannot know a later verdict.
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
        ep, fc, result = row["episode"], row["forecast"], row["result"]
        sections = ep.get("sections", [])
        paragraphs = sections[0].get("paragraphs", []) if sections and isinstance(sections[0], dict) else []
        reason = reader_reason_sentences(paragraphs[0] if paragraphs else '')
        reason = reason or reader_reason_sentences(ep.get("summary", ""))
        reason_html = f'<p class="market-reader-reason"><strong>理由</strong>{_escape(reason)}</p>' if reason else ''
        url = f'/serials/{_escape(series["slug"])}/{_escape(ep["slug"])}/'
        sources = ''.join(
            f'<a href="{_escape(source["url"])}" title="{_escape(source["title"])}" '
            f'target="_blank" rel="noopener noreferrer external">資料{index}</a>'
            for index, source in enumerate(ep.get("sources", []), 1)
        )
        result_sources = ''.join(
            f'<a href="{_escape(source["url"])}" title="{_escape(source["title"])}" '
            f'target="_blank" rel="noopener noreferrer external">結果の出典{index}</a>'
            for index, source in enumerate(result["evidence"], 1)
        ) if result else ''
        label = "判定済み：" + result["outcome"] if result else "結果待ち"
        cards.append(
            '<article class="forecast-ledger-card market-reader-card">'
            f'<p class="market-reader-date"><span class="market-reader-horizon">'
            f'{forecast_horizon(row["published"],row["review"])}</span> '
            f'<time datetime="{row["published"].isoformat()}">'
            f'{row["published"].isoformat()}</time>の予想</p>'
            f'<h3><a href="{url}">{_escape(ep["title"])}</a></h3>'
            f'<p class="market-reader-direction">{_escape(fc["statement"])}</p>'
            + reason_html
            + f'<div class="market-reader-result"><strong>{_escape(label)}</strong>'
            f'<span>判定予定 <time datetime="{row["review"].isoformat()}">{row["review"].isoformat()}</time></span>'
            f'{result_sources}</div>'
            f'<p class="market-reader-sources">出典 {sources}</p></article>'
        )
    past = ('<details class="market-reader-past"><summary>過去の予想</summary>'
            '<div class="market-reader-cards">' + ''.join(cards[3:]) + '</div></details>') if cards[3:] else ''
    return ('<section class="forecast-ledger market-reader-ledger" id="forecast-ledger" aria-labelledby="forecast-ledger-heading">'
            '<div class="section-head"><h2 id="forecast-ledger-heading">連載の予想と結果</h2></div>'
            '<div class="market-reader-cards">' + ''.join(cards[:3]) + '</div>' + past + '</section>')
