"""Static, cost-free serial features: original mystery and clearly labelled market analysis.

Standalone editorial data in content/serials.json; no API, key, paid service or JS
required for navigation, SEO, image previews, or the current episodes.
"""
from __future__ import annotations

import html
import json
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo
from xml.etree import ElementTree as ET
from pathlib import Path
from urllib.parse import urlencode, urlsplit

if __package__:
    from .social_cards import Canvas, WIDTH, HEIGHT
    from .forecast_ledger import render_forecast_ledger
    from .market_figures import load_market_figures, render_market_figure, write_market_figures
    from .reader_comments import render_reader_comments
    from .market_outlook import load_market_outlook, render_market_outlook_index, render_market_outlook_episode
    from .market_outlooks import load_market_outlooks, render_market_outlook_board, build_market_outlooks
    from .market_bulletins import load_market_bulletins, render_market_bulletins_index, build_market_bulletins
else:
    from social_cards import Canvas, WIDTH, HEIGHT
    from forecast_ledger import render_forecast_ledger
    from market_figures import load_market_figures, render_market_figure, write_market_figures
    from reader_comments import render_reader_comments
    from market_outlook import load_market_outlook, render_market_outlook_index, render_market_outlook_episode
    from market_outlooks import load_market_outlooks, render_market_outlook_board, build_market_outlooks
    from market_bulletins import load_market_bulletins, render_market_bulletins_index, build_market_bulletins


# Publication retirement is separate from immutable editorial source/history.
WITHDRAWN_SERIES = frozenset({"case-files"})


def public_serials(serials):
    return [series for series in serials if series.get("slug") not in WITHDRAWN_SERIES]


def _require_public(series):
    if series.get("slug") in WITHDRAWN_SERIES:
        raise ValueError("Serial has been withdrawn from publication")


def h(value):
    return html.escape(str(value), quote=True)


def _check_slug(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
        raise ValueError("Invalid serial slug: " + str(value))


def _check_day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Invalid serial publication date")


def load_serials(base: Path, *, include_withdrawn=False):
    """Fail closed on broken editorial data, wrong labels, and unsafe outbound links."""
    serials = json.loads((base / "content/serials.json").read_text(encoding="utf-8"))
    if not isinstance(serials, list) or not serials:
        raise ValueError("Serials must be a non-empty list")
    seen = set()
    today_jst = datetime.now(ZoneInfo('Asia/Tokyo')).date()
    for series in serials:
        _check_slug(series["slug"])
        if series["slug"] in seen:
            raise ValueError("Duplicate serial slug")
        seen.add(series["slug"])
        if series["kind"] not in ("fiction", "analysis"):
            raise ValueError("Serial type must distinguish fiction and analysis")
        if not all(series.get(k) for k in ("title", "subtitle", "description", "eyebrow")):
            raise ValueError("Missing serial description")
        episodes = series.get("episodes")
        if not isinstance(episodes, list) or not episodes:
            raise ValueError("Every series needs a published episode")
        episode_slugs = set()
        for i, ep in enumerate(episodes, 1):
            _check_slug(ep["slug"])
            if ep["slug"] in episode_slugs or ep["number"] != i:
                raise ValueError("Duplicate or unsorted episode")
            episode_slugs.add(ep["slug"])
            _check_day(ep["published_at"])
            if date.fromisoformat(ep["published_at"]) > today_jst:
                raise ValueError("Unreleased future-dated chapter must not be published: "+ep["slug"])
            for field in ("title", "summary", "kicker"):
                if not ep.get(field):
                    raise ValueError("Missing episode " + field)
            sections = ep.get("sections")
            if not isinstance(sections, list) or len(sections) < 2:
                raise ValueError("Serial episode needs at least two sections")
            for section in sections:
                if not section.get("heading") or not isinstance(section.get("paragraphs"), list) or not section["paragraphs"]:
                    raise ValueError("Empty serial section")
                if any(not isinstance(p, str) or len(p) < 25 for p in section["paragraphs"]):
                    raise ValueError("Serial prose is missing or too short")
            if series["kind"] == "analysis":
                if not ep.get("sources") or len(ep.get("scenarios", [])) != 3 or not ep.get("forecast"):
                    raise ValueError("Market analysis needs sources, 3 scenarios and a forecast")
                _check_day(ep["forecast"]["reviewed_on"])
                if ep["forecast"]["reviewed_on"] <= ep["published_at"]:
                    raise ValueError("Forecast review date must be in future")
                if not ep["forecast"].get("method") or not ep["forecast"].get("statement"):
                    raise ValueError("Market forecast must be falsifiable")
                for source in ep["sources"]:
                    parsed = urlsplit(source["url"])
                    if parsed.scheme != "https" or not parsed.hostname or not source.get("title") or parsed.username or parsed.password:
                        raise ValueError("Untrusted analysis reference")
            elif not ep.get("clues") or not ep.get("reader_prompt"):
                raise ValueError("Mystery episodes need fair clues and a reader prompt")
    return serials if include_withdrawn else public_serials(serials)


def series_url(series):
    return "/serials/" + series["slug"] + "/"


def episode_url(series, episode):
    return series_url(series) + episode["slug"] + "/"


def serial_feed_url(series):
    """A self-hosted RSS feed that works without an email platform or API."""
    return "/feeds/series-" + series["slug"] + ".xml"


def serial_feed_xml(series):
    """Release-only RSS. Date-only metadata never fabricates an exact publish time."""
    _require_public(series)
    atom_ns = "http://www.w3.org/2005/Atom"
    media_ns = "http://search.yahoo.com/mrss/"
    ET.register_namespace("atom", atom_ns)
    ET.register_namespace("media", media_ns)
    root=ET.Element("rss",version="2.0")
    channel=ET.SubElement(root,"channel")
    description = (
        "未発生事件簿：完全オリジナルの創作推理小説。実際の事件ではありません。"
        if series["kind"]=="fiction" else
        "未来相場観測室：出典付き市場考察。予想には不確実性があり、投資助言や売買推奨ではありません。"
    )
    for tag,value in (("title",series["title"]+" | 未発生新聞 連載"),
                      ("link","https://veripsa.com"+series_url(series)),
                      ("description",description),("language","ja")):
        ET.SubElement(channel,tag).text=value
    ET.SubElement(channel,f"{{{atom_ns}}}link",{
        "href":"https://veripsa.com"+serial_feed_url(series),
        "rel":"self","type":"application/rss+xml"
    })
    for episode in reversed(series["episodes"][-30:]):
        url="https://veripsa.com"+episode_url(series,episode)
        item=ET.SubElement(channel,"item")
        for tag,value in (("title",episode["title"]+" | "+series["title"]),
                          ("link",url),("guid",url),
                          ("description",episode["summary"]+" "+description),
                          ("category","創作推理小説" if series["kind"]=="fiction" else "出典付き市場分析")):
            ET.SubElement(item,tag).text=value
        # The source tracks only the real publication DAY, not the clock time.
        # Omitting pubDate avoids constructing a false midnight timestamp.
        image=("https://veripsa.com/og/serials/"+series["slug"]+"-"+episode["slug"]+".png")
        ET.SubElement(item,f"{{{media_ns}}}thumbnail",{
            "url":image,"width":"1200","height":"630"
        })
        ET.SubElement(item,f"{{{media_ns}}}content",{
            "url":image,"type":"image/png","medium":"image"
        })
    return ET.tostring(root,encoding="unicode")


def serial_routes(serials):
    serials = public_serials(serials)
    if not serials:
        return []
    latest = max(e["published_at"] for s in serials for e in s["episodes"])
    routes = [("/serials/", latest)]
    for s in serials:
        routes.append((series_url(s), max(e["published_at"] for e in s["episodes"])))
        routes.extend((episode_url(s, e), e["published_at"]) for e in s["episodes"])
    return routes


def _cover_img(series, loading="lazy"):
    src = series_url(series) + "cover.svg"
    return (f'<img class="serial-cover" src="{h(src)}" alt="{h(series["title"])}の表紙"'
            f' width="1200" height="675" loading="{loading}" decoding="async">')


def _kind_label(series):
    return "推理小説" if series["kind"] == "fiction" else "市場分析"


def episode_label(series, number):
    """Market columns are numbered 回, mystery fiction episodes are numbered 話."""
    return f"第{number}{'話' if series['kind'] == 'fiction' else '回'}"


def progress_attributes(series):
    """Only published, validated episode IDs; HTML escape the JSON attribute."""
    episodes = [{"slug": ep["slug"], "number": ep["number"]} for ep in series["episodes"]]
    data = json.dumps(episodes, ensure_ascii=False, separators=(",", ":"))
    return (f'data-serial-progress-series data-series-id="{h(series["slug"])}" '
            f'data-kind="{h(series["kind"])}" data-serial-episodes="{h(data)}"')


def render_completed_reading(series, episode):
    """Optional per-browser marker; never claim a page view is a completed read."""
    return (f'<section class="serial-reading-marker" data-serial-progress-current '
            f'data-series-id="{h(series["slug"])}" data-episode-id="{h(episode["slug"])}" '
            'aria-label="自分の読了記録">'
            '<div><strong>読了記録</strong></div>'
            '<button type="button" data-serial-progress-toggle aria-pressed="false">この回を読了にする</button>'
            '<p data-serial-progress-status role="status" aria-live="polite">このブラウザに保存します。</p>'
            '</section>')


def _series_card(series):
    ep = series["episodes"][-1]
    latest = (f'<span class="serial-latest">最新公開：{h(ep["title"])}</span>')
    # A new mystery reader needs the beginning; a market reader needs the latest analysis.
    first = series["episodes"][0]
    main_ep = first if series["kind"] == "fiction" else ep
    main_label = "第1話から読む" if series["kind"] == "fiction" else f'最新の{episode_label(series, ep["number"])}を読む'
    newer_episode_link = (
        f'<a class="serial-latest-link" href="{episode_url(series, ep)}">最新の{episode_label(series, ep["number"])}へ →</a>'
        if series["kind"] == "fiction" and ep["slug"] != first["slug"] else ""
    )
    return (f'<article class="serial-card serial-card--{series["kind"]}" {progress_attributes(series)}>'
            f'<a href="{series_url(series)}" class="serial-card-image">{_cover_img(series)}</a>'
            f'<div class="serial-card-content"><span class="serial-type">{_kind_label(series)}</span>'
            f'<h3><a href="{series_url(series)}">{h(series["title"])}</a></h3>'
            f'<p>{h(series["description"])}</p>{latest}'f'<p class="serial-progress-summary" data-serial-progress-summary aria-live="polite"></p>'f'<a class="serial-resume" data-serial-progress-continue hidden href="{episode_url(series, series["episodes"][0])}">続きから読む →</a>'f'<span class="serial-completed" data-serial-progress-complete hidden>公開分を読了</span>'
            f'<a class="serial-link" href="{episode_url(series, main_ep)}">{main_label} <span aria-hidden="true">→</span></a>'
            f'{newer_episode_link}'
            f'<a class="serial-feed-link" href="{serial_feed_url(series)}" type="application/rss+xml">新着をRSSで購読 ↗</a>'
            f'</div></article>')


def render_serials_feature(serials, leading_card=""):
    serials = public_serials(serials)
    heading = "コーナー" if leading_card else "連載"
    more_link = ('<a href="/serials/market-lab/">未来相場観測室を見る ↗</a>' if leading_card else
                 '<a href="/serials/">連載一覧を見る ↗</a>')
    return (f'''<section class="serials-feature" aria-labelledby="serials-home-heading">
      <div class="section-head"><div>
      <h2 id="serials-home-heading">{heading}</h2></div>
      {more_link}</div>
      <div class="serial-grid">''' +
            leading_card + "".join(_series_card(s) for s in serials) + "</div></section>")


def render_serials_index(serials, layout):
    serials = public_serials(serials)
    main = ('''<div class="container serials-page"><header class="serials-intro">
      <h1>連載</h1></header>
      <div class="serial-grid serial-grid--archive">''' +
      "".join(_series_card(s) for s in serials) + "</div></div>")
    return layout("連載一覧", "未来相場観測室の市場予測と検証を読む。", "/serials/", main, active="serials", serial_collection=serials)


def _render_market_editorial_hub(series, layout, figures, bulletins, outlook, daily_outlooks):
    """A reading-first financial front page, not a console of forecast states.

    Daily and long-term ledgers remain reachable; empty feeds never occupy the
    lead position. Editorial content and the original prediction terms stay
    unchanged. The newest actually published analysis leads.
    """
    latest = series["episodes"][-1]
    older = list(reversed(series["episodes"][:-1]))
    latest_url = episode_url(series, latest)
    forecast = latest["forecast"]
    day = latest["published_at"]
    figures_count = len((figures or {}).get(latest["slug"], []))
    nowcast = ""
    if daily_outlooks:
        # One short summary only; the full dated history has its own page.
        from datetime import datetime as _datetime
        try:
            from .market_outlooks import view_models as _daily_models
        except ImportError:
            from market_outlooks import view_models as _daily_models
        models = _daily_models(daily_outlooks)
        if models:
            item = models[0]
            fc = item["forecast"]
            revised = item["updates"][-1] if item["updates"] else None
            stance = revised["stance"] if revised else fc["stance"]
            nowcast = (
                '<section class="market-paper-daily" id="daily-forecasts">'
                '<div class="market-paper-heading"><h2>直近の市場見通し</h2>'
                '<a href="/serials/market-lab/outlooks/">過去の見通しと変更履歴</a></div>'
                f'<p class="market-paper-daily-date"><time datetime="{h(fc["at"])}">{h(fc["at"][:10])}</time>'
                f' ／ 現在の見方：{h(stance)}</p>'
                f'<p>{h(fc["statement"])}</p>'
                f'<p class="market-paper-small">確認日：{h(fc["reviewed_on"])}'
                '。結果の判定は当初の基準に従います。</p>'
                '</section>'
            )

    older_rows = "".join(
        '<li>'
        f'<a href="{episode_url(series, ep)}">'
        f'<span class="market-paper-story-date"><time datetime="{ep["published_at"]}">{ep["published_at"]}</time>'
        f'　／　第{ep["number"]}回</span>'
        f'<strong>{h(ep["title"])}</strong>'
        f'<span class="market-paper-story-summary">{h(ep["summary"])}</span>'
        + (f'<span class="market-paper-story-figure">図解 {len((figures or {}).get(ep["slug"], []))} 点</span>'
           if (figures or {}).get(ep["slug"]) else '')
        + f'<small class="serial-episode-read" data-serial-episode-state data-episode="{h(ep["slug"])}"></small>'
        + '</a></li>'
        for ep in older
    )
    history = render_market_outlook_index(series, outlook or {})
    bulletins_html = render_market_bulletins_index(bulletins) if bulletins else ""
    main = (
        f'<div class="container serials-page market-paper" {progress_attributes(series)}>'
        '<nav class="serial-breadcrumb" aria-label="現在位置">'
        '<a href="/">トップ</a> / <a href="/serials/">連載</a> / 未来相場観測室</nav>'
        '<header class="market-paper-masthead">'
        '<p class="market-paper-topline"><span>株式・企業分析</span>'
        f'<span>最終掲載 <time datetime="{day}">{day}</time> ／ 公開済み {len(series["episodes"])}回</span></p>'
        '<div class="market-paper-brand">'
        '<div><p class="market-paper-brand-kicker">MARKET INSIGHT</p>'
        f'<h1>{h(series["title"])}</h1>'
        '<p>決算資料の数字を読み、これからの市場を考える。</p></div>'
        f'<div class="market-paper-symbol">{_cover_img(series, loading="eager")}</div></div>'
        '<div class="market-paper-reading-progress">'
        '<p class="serial-progress-summary" data-serial-progress-summary aria-live="polite"></p>'
        f'<a class="serial-resume" data-serial-progress-continue hidden href="{episode_url(series, latest)}">続きを読む →</a>'
        '<span class="serial-completed" data-serial-progress-complete hidden>公開分を読了</span>'
        '</div>'
        '<nav class="market-paper-nav" aria-label="このページの案内">'
        '<a href="#market-focus">最新の分析</a>'
        '<a href="#market-stories">過去の記事</a>'
        '<a href="#market-proof">予測と検証</a>'
        '</nav></header>'
        '<div class="market-paper-content">'
        '<section class="market-paper-lead" id="market-focus" aria-labelledby="market-focus-heading">'
        '<div class="market-paper-lead-story">'
        f'<p class="market-paper-eyebrow">最新の分析 ／ 第{latest["number"]}回 '
        f'<time datetime="{day}">{day}</time></p>'
        f'<h2 id="market-focus-heading"><a href="{latest_url}">{h(latest["title"])}</a></h2>'
        f'<p class="market-paper-dek">{h(latest["summary"])}</p>'
        f'<a class="market-paper-read" href="{latest_url}">記事と図解を読む →</a>'
        f'<small class="serial-episode-read" data-serial-episode-state data-episode="{h(latest["slug"])}"></small>'
        '</div>'
        '<aside class="market-paper-follow" aria-label="この分析の注目点">'
        '<p class="market-paper-eyebrow">今後の焦点</p>'
        f'<h3>{h(forecast["statement"])}</h3>'
        '<dl>'
        f'<div><dt>確かめる日</dt><dd><time datetime="{h(forecast["reviewed_on"])}">{h(forecast["reviewed_on"])}</time></dd></div>'
        f'<div><dt>現在</dt><dd>{("結果を確認済み" if forecast.get("status") == "検証済み" else "結果の確認前")}</dd></div>'
        '</dl>'
        f'<a href="{latest_url}#forecast-detail">見通しの根拠を読む →</a>'
        '</aside></section>'
        + nowcast
        + bulletins_html
        + '<section class="market-paper-stories" id="market-stories">'
        '<div class="market-paper-heading"><h2>これまでの分析</h2>'
        '<span class="market-paper-visually-hidden">エピソード一覧</span>'
        f'<a href="{serial_feed_url(series)}">連載をRSS購読</a></div>'
        f'<p class="market-paper-section-intro">過去の公表資料をもとにした分析です。掲載時点の見通しはそのまま残しています。</p>'
        f'<ol class="market-paper-story-list">{older_rows}</ol></section>'
        + history
        + '<details class="market-paper-ledger" id="market-proof">'
        f'<summary>予測の判定日と詳しい検証方法を見る（{len(series["episodes"])}件）</summary>'
        + render_forecast_ledger(series)
        + '</details>'
        '<p class="market-paper-disclosure">実際に公表された資料をもとにした分析です。'
        '架空ニュースではありません。投資助言・売買推奨ではありません。</p>'
        '</div></div>'
    )
    return layout(series["title"], series["description"], series_url(series), main, active="serials",
                  serial={"slug": series["slug"], "kind": series["kind"], "title": series["title"],
                          "episodes": [{"slug": ep["slug"], "title": ep["title"]}
                                       for ep in series["episodes"]]},
                  feed_url=serial_feed_url(series))


def render_series_page(series, layout, figures=None, bulletins=None, outlook=None, daily_outlooks=None):
    _require_public(series)
    if series["kind"] == "analysis":
        return _render_market_editorial_hub(series, layout, figures, bulletins, outlook, daily_outlooks)
    episodes = sorted(series["episodes"], key=lambda ep: ep["number"], reverse=True)
    figures = figures or {}
    items = "".join(
        f'<li><a href="{episode_url(series, ep)}"><span>{episode_label(series,ep["number"])} <time datetime="{ep["published_at"]}">{ep["published_at"]}</time></span>'
        f'<strong>{h(ep["title"])}</strong><small>{h(ep["summary"])}</small>'
        + (f'<small class="market-figure-count">図解 {len(figures.get(ep["slug"], []))} 点</small>'
           if series["kind"] == "analysis" and figures.get(ep["slug"]) else '')
        + f'<small class="serial-episode-read" data-serial-episode-state data-episode="{h(ep["slug"])}"></small></a></li>'
        for ep in episodes
    )
    disclosure = ('<p class="serials-disclosure">投資助言・売買推奨ではありません。</p>'
                  if series["kind"] == "analysis" else '')
    main = (f'<div class="container serials-page" {progress_attributes(series)}><nav class="serial-breadcrumb" aria-label="現在位置">'
            f'<a href="/">トップ</a> / <a href="/serials/">連載</a> / {h(series["title"])}</nav>'
            f'<header class="serial-series-hero serial-series-hero--{series["kind"]}">'
            f'<div class="serial-series-media">{_cover_img(series, loading="eager")}</div>'
            f'<div class="serial-series-copy"><span class="serial-type">{_kind_label(series)}</span>'
            f'<h1>{h(series["title"])}</h1><p class="serial-series-subtitle">{h(series["subtitle"])}</p>'
            f'<p>{h(series["description"])}</p><span class="serial-series-counter">公開済み {len(episodes)}{"話" if series["kind"]=="fiction" else "回"}</span>'
            + (f'<a class="serial-start-link" href="{episode_url(series, series["episodes"][0])}">第1話から読む →</a>'
               if series["kind"] == "fiction" else '') +
            f'<div class="serial-progress-inline"><p class="serial-progress-summary" data-serial-progress-summary aria-live="polite"></p>'f'<a class="serial-resume" data-serial-progress-continue hidden href="{episode_url(series, series["episodes"][0])}">続きから読む →</a>'f'<span class="serial-completed" data-serial-progress-complete hidden>公開分を読了</span></div></div></header>{disclosure}'
            f'{render_market_outlook_board(daily_outlooks or []) if series["slug"] == "market-lab" else ""}'
            f'{render_market_bulletins_index(bulletins or []) if series["slug"] == "market-lab" else ""}'
            f'{render_market_outlook_index(series, outlook or {}) if series["kind"] == "analysis" else ""}'
            f'<section class="serial-episode-index"><div class="section-head"><h2>エピソード一覧</h2>'
            f'<a class="serial-feed-link" href="{serial_feed_url(series)}" type="application/rss+xml">連載をRSS購読 ↗</a></div>'
            f'<ol>{items}</ol></section>{render_forecast_ledger(series)}</div>')
    return layout(series["title"], series["description"], series_url(series), main, active="serials",
                  serial={"slug": series["slug"], "kind": series["kind"], "title": series["title"],
                          "episodes": [{"slug": ep["slug"], "title": ep["title"]}
                                       for ep in series["episodes"]]},
                  feed_url=serial_feed_url(series))


def _share_link(series, ep):
    _require_public(series)
    url = "https://veripsa.com" + episode_url(series, ep)
    label = "創作推理小説" if series["kind"]=="fiction" else "出典付き市場シナリオ（投資助言ではありません）"
    subject = f'{ep["title"]}｜未発生新聞 {label}'
    return "https://x.com/intent/tweet?" + urlencode({"text": subject, "url": url})


def render_episode_page(series, ep, layout, figures=None, outlook=None):
    _require_public(series)
    idx = series["episodes"].index(ep)
    prev_ep = series["episodes"][idx - 1] if idx else None
    next_ep = series["episodes"][idx + 1] if idx + 1 < len(series["episodes"]) else None
    # Figures belong to the presentation layer; published text and forecasts
    # remain immutable. Drawings follow the relevant passage, not the cover.
    figures = figures or []
    narrative_parts = []
    for section_number, section in enumerate(ep["sections"], 1):
        anchor = ' id="article-evidence"' if section_number == 1 and series["kind"] == "analysis" else ""
        narrative_parts.append(
            f'<section class="serial-prose-section"{anchor}><h2>{h(section["heading"])}</h2>'
            + "".join(f'<p>{h(p)}</p>' for p in section["paragraphs"]) + '</section>'
        )
        if series["kind"] == "analysis":
            narrative_parts.extend(
                render_market_figure(series, ep, figure)
                for figure in figures if figure["after_section"] == section_number
            )
    narrative = "".join(narrative_parts)
    extras = ""
    if series["kind"] == "fiction":
        clues = "".join(f"<li>{h(clue)}</li>" for clue in ep["clues"])
        # Hints stay optional and do not interrupt the ending or explain the answer.
        extras = (f'<details class="serial-clues"><summary>読了後に伏線を振り返る</summary>'
                  f'<div><ul>{clues}</ul>'
                  f'<p><strong>考えるヒント：</strong>{h(ep["reader_prompt"])}</p></div></details>')
    else:
        scenarios = "".join(
            f'<article class="serial-scenario"><h3>{h(s["label"])}</h3><p>{h(s["text"])}</p></article>'
            for s in ep["scenarios"]
        )
        fc = ep["forecast"]
        refs = "".join(
            f'<li><a href="{h(ref["url"])}" target="_blank" rel="noopener noreferrer external">'
            f'{h(ref["title"])}</a></li>' for ref in ep["sources"])
        extras = (
                  '<section class="market-article-outlook" id="article-outlook">'
                  '<p class="market-paper-eyebrow">今後の焦点</p>'
                  '<h2>これからどうなるか</h2>'
                  f'<p class="market-article-thesis">{h(fc["statement"])}</p>'
                  f'<p class="market-article-review-date">結果を確かめる日：'
                  f'<time datetime="{fc["reviewed_on"]}">{h(fc["reviewed_on"])}</time></p>'
                  f'<details class="market-article-method" id="forecast-detail">'
                  '<summary>予測の判定方法と基準値を読む</summary>'
                  f'<p><strong>判定状況：</strong>{h(fc["status"])}</p>'
                  f'<p><strong>当初の検証方法：</strong>{h(fc["method"])}</p>'
                  '</details></section>'
                  f'<section class="serial-scenario-section"><h2>3つの見通し</h2>'
                  f'<div class="serial-scenarios">{scenarios}</div></section>'
                  f'{render_market_outlook_episode(ep, outlook or [])}'
                  f'<section class="serial-references" id="article-sources"><h2>参照した公開資料</h2>'
                  f'<ol>{refs}</ol></section>'
                  f'<p class="forecast-ledger-link"><a href="{series_url(series)}#market-proof">ほかの予測と判定日を見る →</a></p>'
                  )
    # Publishing a sequel must not leave an old "next chapter in preparation"
    # message in the first chapter. Preserve immutable editorial source data.
    followup = ep.get("followup_note", "")
    if next_ep is not None and ("準備中" in followup or "未公開" in followup):
        followup = f'{episode_label(series, next_ep["number"])}が公開されています。'
    unit = "話" if series["kind"] == "fiction" else "回"
    navigation = (
        '<nav class="serial-next-prev" aria-label="連載の前後のページ">'
        + (f'<a href="{episode_url(series, prev_ep)}">← 前の{unit}を読む</a>' if prev_ep else '')
        + (f'<a href="{episode_url(series, next_ep)}">次の{unit}を読む →</a>' if next_ep else
           '<span>次回は未公開</span>')
        + '</nav>'
    )
    marker = ('登場人物・事件はすべて創作です。'
              if series["kind"] == "fiction" else
              '市場の見通しは仮説であり、投資助言・売買推奨ではありません。')
    kicker = ep["kicker"].replace("ここから先はすべて創作です。", "").strip()
    if kicker == "現実の市場に関する独立した分析記事です。創作ニュースではありません。":
        kicker = ""
    kicker_html = f'<p class="serial-article-kicker">{h(kicker)}</p>' if kicker else ""
    main = (f'<div class="container serials-page serial-article serial-article--{series["kind"]}">'
            f'<nav class="serial-breadcrumb" aria-label="現在位置"><a href="/">トップ</a> / '
            f'<a href="/serials/">連載</a> / <a href="{series_url(series)}">{h(series["title"])}</a>'
            f' / {episode_label(series,ep["number"])}</nav>'
            f'<header class="serial-article-head"><span class="serial-type">{_kind_label(series)} / {episode_label(series,ep["number"])}</span>'
            f'<h1>{h(ep["title"])}</h1><p class="serial-article-dek">{h(ep["summary"])}</p>'
            f'<div class="serial-pub"><time datetime="{ep["published_at"]}">公開日 {h(ep["published_at"])}</time>'
            f' · <span>{h(series["title"])}</span></div>'
            f'<p class="serial-article-warning">{marker}</p></header>'
            f'<figure class="serial-article-cover">{_cover_img(series, loading="eager")}</figure>'
            f'<div class="serial-article-body">{kicker_html}'
            + ('<nav class="market-article-jump" aria-label="この記事の目次">'
               '<a href="#article-evidence">数字と背景</a>'
               '<a href="#article-outlook">今後の見通し</a>'
               '<a href="#article-sources">参照資料</a>'
               '</nav>' if series["kind"] == "analysis" else '')
            + f'{narrative}{extras}'
            f'<div class="serial-epilogue"><p>{h(followup)}</p>'
            f'<a href="{h(_share_link(series, ep))}" target="_blank" rel="noopener noreferrer external">この回をXで共有する ↗</a>'
            + (f'<button class="market-share-copy" type="button" data-copy-url="https://veripsa.com{episode_url(series, ep)}"><span aria-hidden="true">↗</span><span>リンクをコピー</span></button>'
               if series["kind"] == "analysis" else "")
            + f'<a class="serial-feed-link" href="{serial_feed_url(series)}" type="application/rss+xml">続編をRSSで確認 ↗</a></div>'
            f'{render_completed_reading(series, ep)}'
            + (render_reader_comments('market-episode-' + ep['slug'],
                heading='この市場分析へのコメント', placeholder='データや予測について感じたこと…')
               if series['kind'] == 'analysis' else '')
            + f'{navigation}<a class="serial-back-link" href="{series_url(series)}">目次に戻る →</a>'
            f'</div></div>')
    return layout(ep["title"], ep["summary"], episode_url(series, ep), main, active="serials",
                  kind="serial_episode", serial={"slug": series["slug"], "kind": series["kind"],
                                                   "title": series["title"], "episode_title": ep["title"],
                                                   "episode": ep["slug"], "published_at": ep["published_at"]},
                  feed_url=serial_feed_url(series))


def render_cover_svg(series):
    """True vector original art with no hotlinked third-party images."""
    _require_public(series)
    fiction = series["kind"] == "fiction"
    ink = "#101e25" if fiction else "#132d31"
    accent = "#e56e61" if fiction else "#d3a963"
    label = "MYSTERY / 2031" if fiction else "MARKET / OUTLOOK"
    if fiction:
        art = ('''<rect x="704" y="100" width="308" height="465" rx="6" fill="none" stroke="#b4d0d0" stroke-width="12"/>
        <rect x="750" y="149" width="221" height="371" rx="3" fill="#263c42" stroke="#e4ede3" stroke-width="3"/>
        <circle cx="914" cy="348" r="14" fill="#e56e61"/>
        <path d="M1070 142l-80 77M1104 350l-103 0M1045 544l-83-77" stroke="#e56e61" stroke-width="5"/>
        <rect x="825" y="220" width="180" height="233" fill="#f2ede2" transform="rotate(9 915 336)"/>
        <path d="M864 279h98M862 307h81M862 335h93M860 362h64" stroke="#71828a" stroke-width="7" transform="rotate(9 915 336)"/>
        <circle cx="925" cy="397" r="40" fill="none" stroke="#e56e61" stroke-width="10"/>''')
    else:
        art = ('''<g fill="#739b91"><rect x="730" y="388" width="45" height="143"/><rect x="802" y="326" width="45" height="205"/>
        <rect x="874" y="267" width="45" height="264"/><rect x="946" y="314" width="45" height="217"/>
        <rect x="1018" y="183" width="45" height="348"/></g>
        <path d="M719 395L818 367 898 272 981 311 1076 153" stroke="#e0b675" stroke-width="14" fill="none"/>
        <circle cx="1076" cy="153" r="19" fill="#e0b675"/>
        <path d="M692 553h432M713 129h411" stroke="#c6ddd3" stroke-opacity=".5" stroke-width="4"/>''')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675" viewBox="0 0 1200 675" role="img"'
            f' aria-label="{h(series["title"])}のオリジナル表紙"><rect width="1200" height="675" fill="{ink}"/>'
            f'<rect width="18" height="675" fill="{accent}"/><rect x="65" y="80" width="6" height="510" fill="{accent}"/>'
            f'<text x="107" y="180" fill="#e9eee7" font-family="Georgia,serif" font-size="62" font-weight="bold">VERIPSA</text>'
            f'<text x="109" y="240" fill="{accent}" font-family="Georgia,serif" font-size="29" letter-spacing="6">{label}</text>'
            f'<path d="M106 315h486" stroke="#77918c" stroke-width="4"/>'
            f'<text x="106" y="460" fill="#f4eee2" font-family="Georgia,serif" font-size="104">01</text>'
            f'<text x="108" y="556" fill="#a9c1bc" font-family="Georgia,serif" font-size="25">THE UNHAPPENED TIMES</text>'
            f'{art}<rect x="0" y="640" width="1200" height="35" fill="{accent}"/></svg>')


def render_social_preview(series, path: Path, episode=None):
    """Distinct 1200x630 PNG for share previews; stdlib Canvas, zero image APIs."""
    _require_public(series)
    fiction = series["kind"] == "fiction"
    ink = (15, 30, 38) if fiction else (17, 43, 45)
    accent = (229, 110, 97) if fiction else (211, 169, 99)
    paper = (239, 239, 228)
    canvas = Canvas()
    canvas.rect(0, 0, WIDTH, HEIGHT, ink)
    canvas.rect(0, 0, WIDTH, 18, accent)
    canvas.rect(0, HEIGHT-45, WIDTH, HEIGHT, accent)
    canvas.rect(65, 86, 75, 536, accent)
    canvas.text("VERIPSA", 113, 102, 10, paper)
    canvas.text("SERIAL", 112, 208, 8, accent)
    canvas.text("FICTION" if fiction else "MARKET", 110, 307, 8, paper)
    episode_name = f"EP {episode['number']:02d}" if episode else "ALL EPISODES"
    canvas.text(episode_name, 110, 419, 5, accent)
    canvas.text("VERIPSA.COM", 105, 575, 5, ink)
    canvas.rect(682, 69, 1138, 545, (37, 61, 69) if fiction else (36, 75, 75))
    if fiction:
        canvas.rect(770, 104, 1024, 503, paper)
        canvas.rect(788, 121, 1007, 484, ink)
        canvas.rect(828, 165, 1086, 440, paper)
        for j in range(5):
            canvas.rect(860, 216+j*31, 1030-j*11, 224+j*31, (85, 112, 112))
        canvas.circle(992, 380, 43, accent)
        canvas.circle(992, 380, 27, paper)
        canvas.circle(918, 352, 10, accent)
    else:
        for i, v in enumerate((178, 245, 308, 235, 370)):
            x=738+i*78
            canvas.rect(x, 500-v, x+43, 500, (123, 157, 147))
        for p, q in zip(((740, 391),(816, 326),(894, 214),(975, 264)),
                        ((816, 326),(894, 214),(975, 264),(1060, 139))):
            canvas.line(p[0],p[1],q[0],q[1],accent,9)
        canvas.circle(1060, 139, 15, accent)
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path)


def build_serials(serials, layout, write, output):
    serials = public_serials(serials)
    if not serials:
        return
    figures = load_market_figures(Path(__file__).resolve().parents[1], serials)
    bulletins = load_market_bulletins(Path(__file__).resolve().parents[1])
    outlook = load_market_outlook(Path(__file__).resolve().parents[1], serials)
    daily_outlooks = load_market_outlooks(Path(__file__).resolve().parents[1])
    write("serials/index.html", render_serials_index(serials, layout))
    for series in serials:
        prefix = f'serials/{series["slug"]}'
        write(prefix + "/index.html", render_series_page(series, layout, figures, bulletins, outlook, daily_outlooks))
        write(serial_feed_url(series).lstrip("/"), serial_feed_xml(series))
        write(prefix + "/cover.svg", render_cover_svg(series))
        render_social_preview(series, output / "og" / "serials" / (series["slug"]+".png"))
        for ep in series["episodes"]:
            write(prefix + "/" + ep["slug"] + "/index.html",
                  render_episode_page(series, ep, layout, figures.get(ep["slug"], []), outlook.get(ep["slug"], [])))
            write_market_figures(series, ep, figures.get(ep["slug"], []), write)
            render_social_preview(series, output / "og" / "serials" /
                                  (series["slug"]+"-"+ep["slug"]+".png"), episode=ep)
    build_market_outlooks(daily_outlooks, layout, write)
    build_market_bulletins(bulletins, layout, write, outlooks=daily_outlooks)
