"""Lightweight magazine covers and contents, referencing reviewed feature URLs."""
from __future__ import annotations

import hashlib
import json
from email.utils import format_datetime
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

if __package__:
    from .photo_editions import edition_url
    from .photo_issues import cover_shot, cover_url, issue_url, index_url, feed_url, _stamp
    from .photo_issue_pages import CREDIT, h, _image_size, _publication_date, _photo_navigation, _rankings, model_url, _issue_engagement
    from .reader_comments import render_reader_comments
else:
    from photo_editions import edition_url
    from photo_issues import cover_shot, cover_url, issue_url, index_url, feed_url, _stamp
    from photo_issue_pages import CREDIT, h, _image_size, _publication_date, _photo_navigation, _rankings, model_url, _issue_engagement
    from reader_comments import render_reader_comments


def feature_edition_map(editions):
    mapping = {}
    for edition in editions:
        for feature in edition["ordered_features"]:
            if feature["slug"] in mapping:
                raise ValueError("A photo feature cannot be republished in another edition")
            mapping[feature["slug"]] = edition
    return mapping


def edition_cover(edition):
    return edition["ordered_features"][-1 if edition.get("release_mode") == "daily" else 0]


def edition_updated_at(edition):
    return edition.get("modified_at") or edition["published_at"]


def _cover_image(edition, *, eager=False):
    feature = edition_cover(edition)
    shot = cover_shot(feature)
    width, height, _supplied = _image_size(feature, shot["id"])
    loading = 'loading="eager" fetchpriority="high"' if eager else 'loading="lazy"'
    return (f'<img src="{h(cover_url(feature))}" alt="{h(shot["alt"])}" '
            f'width="{width}" height="{height}" {loading} decoding="async">')


def _counts(edition):
    features = edition["ordered_features"]
    return f'{len(features)}人・{sum(len(feature["shots"]) for feature in features)}枚'


def _edition_card(edition, *, featured=False):
    daily_latest = featured and edition.get("release_mode") == "daily"
    publication = edition_cover(edition) if daily_latest else edition
    target = issue_url(publication) if daily_latest else edition_url(edition)
    action = "最新の特集を読む" if daily_latest else "この号の目次へ"
    return ('<li><article class="photo-issue-card photo-edition-card">'
            f'<a class="photo-issue-card-link" href="{h(target)}">{_cover_image(edition,eager=featured)}'
            '<div class="photo-issue-card-copy">'
            f'<p class="photo-issue-number">第{h(edition["number"])}号 · {_counts(edition)}</p>'
            f'<h2>{h(publication["title"])}</h2><p class="photo-issue-card-summary">{h(publication["summary"])}</p>'
            f'<p class="photo-issue-card-date">公開日 {_publication_date(publication)}</p>'
            f'<span class="photo-issue-open">{action} <span aria-hidden="true">↗</span></span>'
            '</div></a></article></li>')


def _daily_feature_navigation(edition, models):
    """Link the latest daily issue's released features without repeating photos."""
    if edition.get("release_mode") != "daily":
        return ""
    by_id = {model["id"]: model for model in models}
    links = []
    for feature in edition["ordered_features"]:
        model = feature.get("model_snapshot") or by_id[feature["model_id"]]
        links.append(f'<a href="{h(issue_url(feature))}">{h(model["name"])}</a>')
    number = h(edition["number"])
    return ('<div class="photo-edition-shortcuts">'
            f'<h2 class="photo-issue-number" id="photo-edition-shortcuts-title">第{number}号の掲載特集</h2>'
            '<nav class="photo-section-nav" aria-labelledby="photo-edition-shortcuts-title">'
            + ''.join(links)
            + f'<a href="{h(edition_url(edition))}#contents">第{number}号の目次へ <span aria-hidden="true">→</span></a>'
            '</nav></div>')


def render_edition_index(editions, models, layout, *, stories_markup=""):
    features = list(feature_edition_map(editions))
    by_slug = {feature["slug"]: feature for edition in editions for feature in edition["ordered_features"]}
    older = (f'<ol class="photo-issues-grid" start="2">{"".join(_edition_card(edition) for edition in editions[1:])}</ol>'
             if len(editions) > 1 else "")
    main = ('<div class="container photo-issues-page photo-edition-index">'
            '<nav class="photo-issue-breadcrumb" aria-label="現在位置">'
            '<a href="/">トップ</a> / <span aria-current="page">週刊グラビア</span></nav>'
            '<header class="photo-issues-intro"><p class="photo-issue-eyebrow">未発生新聞 / PHOTO & COLUMN</p>'
            f'<h1>週刊グラビア</h1><p class="photo-issue-credit">{CREDIT}</p>'
            f'<p><a href="{h(feed_url())}" type="application/rss+xml">RSSで購読</a></p></header>'
            f'{_photo_navigation("issues")}'
            f'<ol class="photo-issues-grid">{_edition_card(editions[0],featured=True)}</ol>'
            f'{_daily_feature_navigation(editions[0], models)}{stories_markup}'
            f'{_rankings([by_slug[slug] for slug in features], {model["id"]:model for model in models})}'
            f'{older}</div>')
    return layout("週刊グラビア", "架空の成人モデルの写真と、短い未来コラム。",
                  index_url(), main, active="photos", kind="photo_collection", feed_url=feed_url(),
                  photo_cover_issue=edition_cover(editions[0]))


def _share_controls(edition):
    public = {key:edition[key] for key in ("number","slug","title","summary","features")}
    version = hashlib.sha256(json.dumps(public,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:12]
    url = "https://veripsa.com" + edition_url(edition) + "?v=" + version
    intent = "https://x.com/intent/tweet?" + urlencode({"text":"【週刊グラビア】" + edition["title"] + "\n#未発生新聞", "url":url})
    return ('<nav class="engagement-actions" aria-label="この号を共有">'
            f'<a class="share-control" href="{h(intent)}" target="_blank" rel="noopener noreferrer">'
            '<span aria-hidden="true">𝕏</span><span>Xで共有</span></a>'
            f'<button class="copy-control" type="button" data-copy-url="{h(url)}">'
            '<span aria-hidden="true">↗</span><span>リンクをコピー</span></button></nav>')


def render_photo_edition(edition, models, layout):
    by_id = {model["id"]:model for model in models}
    rows = []
    for position, feature in enumerate(edition["ordered_features"], 1):
        model = feature.get("model_snapshot") or by_id[feature["model_id"]]
        rows.append('<li class="photo-edition-feature">'
                    f'<a class="photo-edition-feature-image" href="{h(issue_url(feature))}">{_cover_image({"ordered_features":[feature]})}</a>'
                    '<div class="photo-edition-feature-copy">'
                    f'<p class="photo-issue-eyebrow">{position:02d} / 10 PHOTOS & COLUMN</p>'
                    f'<p class="photo-edition-model"><a href="{h(model_url(model))}">{h(model["name"])}（{h(model["age"])}歳）</a></p>'
                    f'<h3><a href="{h(issue_url(feature))}">{h(feature["title"])}</a></h3>'
                    f'<p>{h(feature["summary"])}</p><a class="photo-edition-read" href="{h(issue_url(feature))}">'
                    'この特集を読む <span aria-hidden="true">→</span></a></div></li>')
    main = ('<article class="container photo-issues-page photo-edition-page" '
            f'data-engagement-key="{h("photo-issue:"+edition["slug"])}">'
            '<nav class="photo-issue-breadcrumb" aria-label="現在位置">'
            f'<a href="/">トップ</a> / <a href="{h(index_url())}">週刊グラビア</a>'
            f' / <span aria-current="page">第{h(edition["number"])}号</span></nav>'
            f'{_photo_navigation("issues")}<div class="photo-edition-cover">{_cover_image(edition,eager=True)}'
            '<header><p class="photo-issue-eyebrow">週刊グラビア</p>'
            f'<p class="photo-edition-number">第{h(edition["number"])}号</p><h1>{h(edition["title"])}</h1>'
            f'<p class="photo-edition-count">{_counts(edition)}</p><p>{h(edition["summary"])}</p>'
            f'<p class="photo-issue-date">公開日 {_publication_date(edition)}</p>'
            f'<p class="photo-issue-credit">{CREDIT}</p>{_issue_engagement(edition)}{_share_controls(edition)}</header></div>'
            '<section class="photo-edition-contents" id="contents" aria-labelledby="edition-contents-title">'
            '<h2 id="edition-contents-title">目次</h2>'
            f'<ol class="photo-edition-features">{"".join(rows)}</ol></section>'
            f'{render_reader_comments("photo-issue:"+edition["slug"],heading="この号へのコメント",placeholder="写真やコラムへの感想をひとこと…")}</article>')
    public = {key:edition[key] for key in ("number","title","published_at")}
    if edition.get("modified_at"):
        public["modified_at"] = edition["modified_at"]
    return layout(f'週刊グラビア 第{edition["number"]}号｜{edition["title"]}', edition["summary"],
                  edition_url(edition), main, active="photos", kind="photo_edition", photo_edition=public,
                  photo_cover_issue=edition_cover(edition), feed_url=feed_url())


def generate_edition_rss(editions, *, site="https://veripsa.com"):
    atom, media = "http://www.w3.org/2005/Atom", "http://search.yahoo.com/mrss/"
    ET.register_namespace("atom",atom); ET.register_namespace("media",media)
    channel = ET.SubElement(root := ET.Element("rss",version="2.0"),"channel")
    for tag,value in (("title","週刊グラビア | 未発生新聞（創作写真コラム）"),("link",site+index_url()),
                      ("description","架空の成人モデル7人の写真と未来コラム。"),("language","ja")):
        ET.SubElement(channel,tag).text=value
    ET.SubElement(channel,f"{{{atom}}}link",href=site+feed_url(),rel="self",type="application/rss+xml")
    if editions:
        ET.SubElement(channel,"lastBuildDate").text=format_datetime(max(_stamp(edition_updated_at(edition)) for edition in editions))
    entries = []
    for edition in editions:
        if edition.get("release_mode") == "daily":
            entries.extend((feature, issue_url(feature), feature, edition["number"]) for feature in reversed(edition["ordered_features"]))
        else:
            entries.append((edition, edition_url(edition), edition_cover(edition), edition["number"]))
    for publication, path, cover, number in entries:
        shot=cover_shot(cover); image=cover["images"][shot["id"]]
        entry=ET.SubElement(channel,"item")
        for tag,value in (("title",f'第{number}号 {publication["title"]} | 週刊グラビア（創作写真コラム）'),
                          ("link",site+path),("description",publication["summary"]),
                          ("pubDate",format_datetime(_stamp(publication["published_at"])))):
            ET.SubElement(entry,tag).text=value
        ET.SubElement(entry,"guid",isPermaLink="true").text=site+path
        ET.SubElement(entry,"enclosure",url=site+cover_url(cover),length=str(image["bytes"]),type="image/jpeg")
        ET.SubElement(entry,f"{{{media}}}thumbnail",url=site+cover_url(cover),
                      width=str(image.get("width",1200)),height=str(image.get("height",675)))
        ET.SubElement(entry,f"{{{media}}}content",url=site+cover_url(cover),medium="image",type="image/jpeg")
        ET.SubElement(entry,f"{{{media}}}description",type="plain").text=shot["alt"]
    return ET.tostring(root,encoding="unicode")


def edition_sitemap_entries(editions):
    if not editions:
        return []
    latest=editions[0]
    return [(index_url(),edition_updated_at(latest),cover_url(edition_cover(latest)))] + [
        (edition_url(edition),edition_updated_at(edition),cover_url(edition_cover(edition))) for edition in editions]


def build_photo_editions(editions, models, layout, write, *, stories_markup=""):
    if not editions:
        return
    write(index_url().lstrip("/")+"index.html",render_edition_index(editions,models,layout,stories_markup=stories_markup))
    for edition in editions:
        if not edition.get("legacy"):
            write(edition_url(edition).lstrip("/")+"index.html",render_photo_edition(edition,models,layout))
    write(feed_url().lstrip("/"),generate_edition_rss(editions))
