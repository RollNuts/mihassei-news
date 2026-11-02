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
else:
    from social_cards import Canvas, WIDTH, HEIGHT
    from forecast_ledger import render_forecast_ledger


def h(value):
    return html.escape(str(value), quote=True)


def _check_slug(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
        raise ValueError("Invalid serial slug: " + str(value))


def _check_day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Invalid serial publication date")


def load_serials(base: Path):
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
    return serials


def series_url(series):
    return "/serials/" + series["slug"] + "/"


def episode_url(series, episode):
    return series_url(series) + episode["slug"] + "/"


def serial_feed_url(series):
    """A self-hosted RSS feed that works without an email platform or API."""
    return "/feeds/series-" + series["slug"] + ".xml"


def serial_feed_xml(series):
    """Release-only RSS. Date-only metadata never fabricates an exact publish time."""
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
    latest = max(e["published_at"] for s in serials for e in s["episodes"])
    routes = [("/serials/", latest)]
    for s in serials:
        routes.append((series_url(s), max(e["published_at"] for e in s["episodes"])))
        routes.extend((episode_url(s, e), e["published_at"]) for e in s["episodes"])
    return routes


def _cover_img(series, loading="lazy"):
    src = series_url(series) + "cover.svg"
    return (f'<img class="serial-cover" src="{h(src)}" alt="{h(series["title"])}のオリジナル表紙イラスト"'
            f' width="1200" height="675" loading="{loading}" decoding="async">')


def _kind_label(series):
    return "創作ミステリー" if series["kind"] == "fiction" else "実際の市場に関する考察"


def episode_label(series, number):
    """Market columns are numbered 回, mystery fiction episodes are numbered 話."""
    return f"第{number}{'話' if series['kind'] == 'fiction' else '回'}"


def _series_card(series):
    ep = series["episodes"][-1]
    latest = (f'<span class="serial-latest">{episode_label(series,ep["number"])}：{h(ep["title"])}</span>')
    return (f'<article class="serial-card serial-card--{series["kind"]}">'
            f'<a href="{series_url(series)}" class="serial-card-image">{_cover_img(series)}</a>'
            f'<div class="serial-card-content"><span class="serial-type">{_kind_label(series)}</span>'
            f'<h3><a href="{series_url(series)}">{h(series["title"])}</a></h3>'
            f'<p>{h(series["description"])}</p>{latest}'
            f'<a class="serial-link" href="{episode_url(series, ep)}">{episode_label(series,ep["number"])}を読む <span aria-hidden="true">→</span></a>'
            f'<a class="serial-feed-link" href="{serial_feed_url(series)}" type="application/rss+xml">新着をRSSで購読 ↗</a>'
            f'</div></article>')


def render_serials_feature(serials):
    return ('''<section class="serials-feature" aria-labelledby="serials-home-heading">
      <div class="section-head"><div><span class="small-label red">SERIALS / 連載</span>
      <h2 id="serials-home-heading">続きを読みに、また来たくなる。</h2></div>
      <a href="/serials/">連載一覧を見る ↗</a></div>
      <p class="serials-lede">まだ起きていない事件を推理する。実際の市場を予測して答え合わせする。
      創作と現実の分析を、はっきり分けた2つの連載です。</p>
      <div class="serial-grid">''' +
            "".join(_series_card(s) for s in serials) + "</div></section>")


def render_serials_index(serials, layout):
    main = ('''<div class="container serials-page"><header class="serials-intro">
      <span class="small-label red">THE SERIAL DESK / 連載</span>
      <h1>物語を追う。未来を検証する。</h1>
      <p>このページには、2種類の読み物があります。推理小説はすべて創作。
      株式市場の予測は現実の資料と執筆時点の見解に基づく分析です。</p></header>
      <div class="serial-grid serial-grid--archive">''' +
      "".join(_series_card(s) for s in serials) + "</div>"
      '<p class="serials-disclosure">※創作は事実を示しません。市場分析は将来の成果を保証せず、特定銘柄の売買推奨でもありません。</p></div>')
    return layout("連載一覧", "未発生事件簿の連載推理小説と、未来相場観測室の市場予測・検証を読む。", "/serials/", main, active="serials")


def render_series_page(series, layout):
    episodes = sorted(series["episodes"], key=lambda ep: ep["number"], reverse=True)
    items = "".join(
        f'<li><a href="{episode_url(series, ep)}"><span>{episode_label(series,ep["number"])} <time datetime="{ep["published_at"]}">{ep["published_at"]}</time></span>'
        f'<strong>{h(ep["title"])}</strong><small>{h(ep["summary"])}</small></a></li>'
        for ep in episodes
    )
    state = ('架空の登場人物と事件を扱うオリジナル小説です。'
             if series["kind"] == "fiction" else
             '現実のデータを参照した分析です。架空ニュースとは異なり、見解には不確実性があります。')
    main = (f'<div class="container serials-page"><nav class="serial-breadcrumb" aria-label="現在位置">'
            f'<a href="/">トップ</a> / <a href="/serials/">連載</a> / {h(series["title"])}</nav>'
            f'<header class="serial-series-hero serial-series-hero--{series["kind"]}">'
            f'<div class="serial-series-media">{_cover_img(series, loading="eager")}</div>'
            f'<div class="serial-series-copy"><span class="serial-type">{_kind_label(series)}</span>'
            f'<h1>{h(series["title"])}</h1><p class="serial-series-subtitle">{h(series["subtitle"])}</p>'
            f'<p>{h(series["description"])}</p><span class="serial-series-counter">公開済み {len(episodes)}{"話" if series["kind"]=="fiction" else "回"}'
            + (f' / 全{series["planned_episodes"]}話構想' if series.get("planned_episodes") else '') +
            f'</span></div></header><p class="serials-disclosure">{state}</p>'
            f'<section class="serial-episode-index"><div class="section-head"><h2>エピソード一覧</h2>'
            f'<a class="serial-feed-link" href="{serial_feed_url(series)}" type="application/rss+xml">連載をRSS購読 ↗</a></div>'
            f'<ol>{items}</ol></section>{render_forecast_ledger(series)}</div>')
    return layout(series["title"], series["description"], series_url(series), main, active="serials",
                  serial={"slug": series["slug"], "kind": series["kind"]},
                  feed_url=serial_feed_url(series))


def _share_link(series, ep):
    url = "https://veripsa.com" + episode_url(series, ep)
    label = "創作推理小説" if series["kind"]=="fiction" else "出典付き市場シナリオ（投資助言ではありません）"
    subject = f'{ep["title"]}｜未発生新聞 {label}'
    return "https://x.com/intent/tweet?" + urlencode({"text": subject, "url": url})


def render_episode_page(series, ep, layout):
    idx = series["episodes"].index(ep)
    prev_ep = series["episodes"][idx - 1] if idx else None
    next_ep = series["episodes"][idx + 1] if idx + 1 < len(series["episodes"]) else None
    narrative = "".join(
        f'<section class="serial-prose-section"><h2>{h(s["heading"])}</h2>'
        + "".join(f'<p>{h(p)}</p>' for p in s["paragraphs"]) + '</section>'
        for s in ep["sections"]
    )
    extras = ""
    if series["kind"] == "fiction":
        clues = "".join(f"<li>{h(clue)}</li>" for clue in ep["clues"])
        extras = (f'<aside class="serial-reader-box"><span class="small-label">READERS CASE FILE</span>'
                  f'<h2>読者への手掛かり</h2><ul>{clues}</ul>'
                  f'<p><strong>あなたへの問い：</strong>{h(ep["reader_prompt"])}</p></aside>')
    else:
        scenarios = "".join(
            f'<article class="serial-scenario"><h3>{h(s["label"])}</h3><p>{h(s["text"])}</p></article>'
            for s in ep["scenarios"]
        )
        fc = ep["forecast"]
        refs = "".join(
            f'<li><a href="{h(ref["url"])}" target="_blank" rel="noopener noreferrer external">'
            f'{h(ref["title"])}</a></li>' for ref in ep["sources"])
        extras = (f'<section class="serial-scenario-section"><h2>3つのシナリオ</h2>'
                  f'<div class="serial-scenarios">{scenarios}</div></section>'
                  f'<aside class="serial-reader-box serial-forecast"><span class="small-label">PUBLIC FORECAST LOG</span>'
                  f'<h2>後日検証する予想</h2><p><strong>予想：</strong>{h(fc["statement"])}</p>'
                  f'<p><strong>検証日：</strong><time datetime="{fc["reviewed_on"]}">{h(fc["reviewed_on"])}</time>'
                  f'／ 判定状況：{h(fc["status"])}</p>'
                  f'<p><strong>判定方法：</strong>{h(fc["method"])}</p>'
                  f'</aside><p class="forecast-ledger-link"><a href="{series_url(series)}#forecast-ledger">公開予想の検証台帳を確認する ↗</a></p><section class="serial-references"><h2>参照した公開資料</h2>'
                  f'<ol>{refs}</ol><p>数値・見通しは参照資料に基づきますが、株価についての見立ては編集上の仮説です。</p></section>')
    navigation = (
        '<nav class="serial-next-prev" aria-label="連載の前後の話">'
        + (f'<a href="{episode_url(series, prev_ep)}">← 前の話を読む</a>' if prev_ep else
           '<span>ここが第1話です</span>')
        + (f'<a href="{episode_url(series, next_ep)}">次の話を読む →</a>' if next_ep else
           '<span>次回は未公開</span>')
        + '</nav>'
    )
    marker = ('※この物語の人物・事件・地名・書類はすべて創作です。実際の事件ではありません。'
              if series["kind"] == "fiction" else
              '※この記事は執筆時点の情報をもとにした市場考察です。予想は不確実であり、投資助言・売買推奨ではありません。')
    main = (f'<div class="container serials-page serial-article serial-article--{series["kind"]}">'
            f'<nav class="serial-breadcrumb" aria-label="現在位置"><a href="/">トップ</a> / '
            f'<a href="/serials/">連載</a> / <a href="{series_url(series)}">{h(series["title"])}</a>'
            f' / {episode_label(series,ep["number"])}</nav>'
            f'<header class="serial-article-head"><span class="serial-type">{_kind_label(series)} / {episode_label(series,ep["number"])}</span>'
            f'<h1>{h(ep["title"])}</h1><p class="serial-article-dek">{h(ep["summary"])}</p>'
            f'<div class="serial-pub"><time datetime="{ep["published_at"]}">公開日 {h(ep["published_at"])}</time>'
            f' · <span>{h(series["title"])}</span></div>'
            f'<p class="serial-article-warning">{marker}</p></header>'
            f'<figure class="serial-article-cover">{_cover_img(series, loading="eager")}<figcaption>編集部制作のオリジナル表紙イラスト</figcaption></figure>'
            f'<div class="serial-article-body"><p class="serial-article-kicker">{h(ep["kicker"])}</p>'
            f'{narrative}{extras}'
            f'<div class="serial-epilogue"><p>{h(ep.get("followup_note",""))}</p>'
            f'<a href="{h(_share_link(series, ep))}" target="_blank" rel="noopener noreferrer external">この回をXで共有する ↗</a>'
            f'<a class="serial-feed-link" href="{serial_feed_url(series)}" type="application/rss+xml">続編をRSSで確認 ↗</a></div>'
            f'{navigation}<a class="serial-back-link" href="{series_url(series)}">連載一覧に戻る →</a>'
            f'</div></div>')
    return layout(ep["title"], ep["summary"], episode_url(series, ep), main, active="serials",
                  kind="serial_episode", serial={"slug": series["slug"], "kind": series["kind"],
                                                   "episode": ep["slug"], "published_at": ep["published_at"]},
                  feed_url=serial_feed_url(series))


def render_cover_svg(series):
    """True vector original art with no hotlinked third-party images."""
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
    write("serials/index.html", render_serials_index(serials, layout))
    for series in serials:
        prefix = f'serials/{series["slug"]}'
        write(prefix + "/index.html", render_series_page(series, layout))
        write(serial_feed_url(series).lstrip("/"), serial_feed_xml(series))
        write(prefix + "/cover.svg", render_cover_svg(series))
        render_social_preview(series, output / "og" / "serials" / (series["slug"]+".png"))
        for ep in series["episodes"]:
            write(prefix + "/" + ep["slug"] + "/index.html",
                  render_episode_page(series, ep, layout))
            render_social_preview(series, output / "og" / "serials" /
                                  (series["slug"]+"-"+ep["slug"]+".png"), episode=ep)
