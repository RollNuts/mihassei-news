"""A small human-curated introduction to the fictional newspaper.

This is not a ranking, personalization, conversion model, or an automated
assessment of article quality. Missing or stale editorial data never prevents
the high-frequency newspaper from publishing new material.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path


SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def escape(value):
    return html.escape(str(value), quote=True)


def starter_picks(articles, excluded_slugs=(), *, base=None, limit=3):
    """Choose distinct existing articles and avoid repeating the headline lead.

    The source is an explicitly curated list of already published articles.
    Skip stale or malformed entries rather than failing the daily build.
    """
    if limit <= 0:
        return []
    root = Path(base) if base is not None else Path(__file__).resolve().parent.parent
    try:
        data = json.loads((root / "content" / "editorial_picks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("picks"), list):
        return []
    published = {a["slug"]: a for a in articles}
    excluded = set(excluded_slugs)
    chosen = []
    for item in data["picks"][:20]:
        if not isinstance(item, dict):
            continue
        slug, hook, label = item.get("slug"), item.get("hook"), item.get("label")
        if (not isinstance(slug, str) or not SLUG.fullmatch(slug) or
            slug not in published or slug in excluded or
            not isinstance(hook, str) or not 12 <= len(hook.strip()) <= 110 or
            not isinstance(label, str) or not 2 <= len(label.strip()) <= 32):
            continue
        chosen.append({"article": published[slug], "label": label.strip(), "hook": hook.strip()})
        excluded.add(slug)
        if len(chosen) == limit:
            break
    return chosen


def homepage_starter_picks(articles, lead_article, *, base=None, limit=3):
    """Use exactly the same occupied slots in build and live verification.

    The most recent stories are not necessarily the front-page lead; the lead
    may be a different, explicitly editor-picked published story. Exclude
    that article plus the first three actual sidebar stories before selecting
    the editorial starter list.
    """
    if not articles or not isinstance(lead_article, dict) or not lead_article.get("slug"):
        return []
    lead_slug = lead_article["slug"]
    remaining = [article for article in articles if article.get("slug") != lead_slug]
    occupied = {lead_slug}
    occupied.update(article["slug"] for article in remaining[:3])
    return starter_picks(articles, occupied, base=base, limit=limit)


def render_starter_picks(picks, image_tag, article_url):
    """Three distinct editorial choices, rendered as ordinary crawlable links."""
    if not picks:
        return ""
    cards = []
    for item in picks:
        a = item["article"]
        url = escape(article_url(a))
        picture = image_tag(a, "starter-pick-image", extra='loading="lazy" decoding="async"')
        cards.append(
            '<article class="starter-pick-card">'
            f'<a class="starter-pick-media" href="{url}">{picture}</a>'
            '<div class="starter-pick-copy">'
            f'<span class="starter-pick-eyebrow">{escape(item["label"])} / {escape(a["category"])}</span>'
            f'<h3><a href="{url}">{escape(a["title"])}</a></h3>'
            f'<p>{escape(item["hook"])}</p>'
            f'<a class="starter-pick-link" href="{url}">記事を読む →</a>'
            '</div></article>'
        )
    return (
        '<section class="starter-editorial" aria-labelledby="starter-editorial-title">'
        '<div class="starter-editorial-heading">'
        '<span class="small-label red">EDITOR’S PICKS / はじめての方へ</span>'
        '<h2 id="starter-editorial-title">まず、この3本から。</h2>'
        '<p>この新聞らしい、少しおかしな記事を編集部が選びました。'
        '人気ランキングではありません。気になった見出しをどうぞ。</p>'
        '</div><div class="starter-pick-grid">'
        + "".join(cards) + '</div></section>'
    )
