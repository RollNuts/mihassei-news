"""Context-sensitive links from fictional news to already-published serials.

The bridge is an editorial discovery surface, not an endorsement, fake popularity
ranking, recommendation based on reader identity, or evidence for fictional news.
No tracking code, paid APIs, external images or generated future URLs.
"""
from __future__ import annotations

import html
import re

MARKET_THEME = re.compile(
    r"(?<![a-z])AI(?![a-z])|人工知能|機械学習|半導体|ロボット|工場|製造|研究所"
    r"|電力|蓄電|電池|エネルギー|投資|市場|株価|株式|金融|金利|設備",
    re.IGNORECASE,
)


def escape(value):
    return html.escape(str(value), quote=True)


def choose_serial(article: dict, serials: list[dict]):
    """Only a grounded editorial match; never assert that unrelated works are sequels."""
    if not isinstance(article, dict) or not isinstance(serials, list):
        return None
    category = article.get("category")
    if category in ("社会", "暮らし", "エンタメ"):
        slug = "case-files"
    elif category in ("経済", "技術"):
        headline = str(article.get("title", ""))
        abstract = str(article.get("summary", ""))
        if not MARKET_THEME.search(headline + " " + abstract):
            return None
        slug = "market-lab"
    else:
        return None

    for series in serials:
        if series.get("slug") != slug or not series.get("episodes"):
            continue
        expected_kind = "analysis" if slug == "market-lab" else "fiction"
        if series.get("kind") != expected_kind:
            return None
        return series
    return None


def render_serial_bridge(article: dict, serials: list[dict]):
    """Static, crawlable links to a *real* episode with a visible editorial boundary."""
    series = choose_serial(article, serials)
    if series is None:
        return ""
    latest = series["episodes"][-1]
    prefix = "/serials/" + escape(series["slug"]) + "/"
    chapter = prefix + escape(latest["slug"]) + "/"
    analysis = series["kind"] == "analysis"
    label = "現実の市場分析" if analysis else "創作推理小説"
    introduction = (
        "こちらは実際の公開資料から市場を考える別枠の連載です。"
        "この記事の架空の出来事を裏付けるものではなく、投資助言・売買推奨でもありません。"
        if analysis else
        "こちらは架空の事件を追う独立した推理小説です。"
        "この記事の続報ではなく、実際の事件を扱うものでもありません。"
    )
    unit = "回" if analysis else "話"
    number = latest["number"]
    return (
        '<aside class="article-serial-bridge" aria-labelledby="article-serial-bridge-title">'
        '<div class="article-serial-bridge-media">'
        f'<a href="{chapter}" aria-label="{escape(series["title"])}の第{number}{unit}を読む">'
        f'<img src="{prefix}cover.svg" width="1200" height="675" loading="lazy" decoding="async"'
        f' alt="{escape(series["title"])}のオリジナル表紙"></a></div>'
        '<div class="article-serial-bridge-copy">'
        f'<span class="article-serial-bridge-label">{label} / 未発生新聞の連載</span>'
        '<h2 id="article-serial-bridge-title">ニュースの外側にも、読み物がある。</h2>'
        f'<h3><a href="{prefix}">{escape(series["title"])}</a></h3>'
        f'<p class="article-serial-bridge-explain">{introduction}</p>'
        f'<p class="article-serial-bridge-latest">公開中の第{number}{unit}：{escape(latest["title"])}</p>'
        '<div class="article-serial-bridge-links">'
        f'<a class="article-serial-bridge-primary" href="{chapter}">第{number}{unit}を読む →</a>'
        f'<a href="{prefix}">作品の目次を見る ↗</a></div></div>'
        '</aside>'
    )
