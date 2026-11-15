"""Static reader pages for validated, published weekly photo issues."""
from __future__ import annotations

import html
import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

if __package__:
    from .photo_issues import cover_shot, cover_url, index_url, issue_url, photo_image_url, feed_url
    from .reader_comments import render_reader_comments
    from .photo_profile_portraits import ROOT, validate_profile_portrait, copy_profile_portrait
else:
    from photo_issues import cover_shot, cover_url, index_url, issue_url, photo_image_url, feed_url
    from reader_comments import render_reader_comments
    from photo_profile_portraits import ROOT, validate_profile_portrait, copy_profile_portrait


CREDIT = "登場人物とコラムは創作、写真はAI生成です。"
JST = timezone(timedelta(hours=9))
MODEL_INDEX_URL = "/photo/models/"


def h(value):
    return html.escape(str(value), quote=True)


def _publication_date(issue):
    stamp = issue["published_at"]
    if not isinstance(stamp, str):
        raise ValueError("A reader page needs a publication date")
    published = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if published.tzinfo is not None:
        published = published.astimezone(JST)
    return (f'<time datetime="{h(stamp)}">'
            f'{published.year}年{published.month}月{published.day}日</time>')


def _model_name(model):
    return f'{h(model["name"])}（{h(model["age"])}歳）'


def model_url(model):
    return MODEL_INDEX_URL + model["id"] + "/"


def _released_issues(issues):
    """Only the caller's already-validated releases can reach reader routes."""
    return sorted((issue for issue in issues if issue.get("state") == "published"),
                  key=lambda issue: datetime.fromisoformat(
                      issue["published_at"].replace("Z", "+00:00")), reverse=True)


def _photo_navigation(active):
    links = ((index_url(), "号一覧", "issues"), (MODEL_INDEX_URL, "モデル一覧", "models"))
    return ('<nav class="photo-section-nav" aria-label="グラビアの案内">' + "".join(
        f'<a href="{h(url)}"' + (' aria-current="page"' if key == active else '') + f'>{label}</a>'
        for url, label, key in links) + '</nav>')


def _rankings(issues, model_by_id):
    if not issues:
        return ""
    rows = []
    for issue in issues:
        model = issue.get("model_snapshot") or model_by_id[issue["model_id"]]
        rows.append(f'<li data-ranking-key="{h("photo-issue:" + issue["slug"])}">'
                    f'<a href="{h(issue_url(issue))}">{h(model["name"])} · {h(issue["title"])}</a>'
                    '<span data-ranking-pv></span></li>')
    return ('<section class="photo-rankings" data-photo-rankings>'
            '<h2>7日間の閲覧ランキング</h2>'
            '<p data-ranking-status role="status">集計を読み込んでいます…</p>'
            f'<ol data-ranking-list hidden>{"".join(rows)}</ol></section>')


def _image_size(issue, cut):
    image = issue["images"][cut]
    supplied = image.get("provenance", {}).get("kind") == "user_supplied_photo"
    if not supplied:
        return 1200, 675, False
    width, height = image.get("width"), image.get("height")
    if type(width) is not int or type(height) is not int or min(width, height) <= 0:
        raise ValueError("A supplied photo needs its actual JPEG width and height")
    return width, height, True


def _issue_card(issue, model):
    shot = cover_shot(issue)
    width, height, supplied = _image_size(issue, shot["id"])
    modifier = " photo-issue-card--original-ratio" if supplied else ""
    ratio = f' style="aspect-ratio:{width}/{height}"' if supplied else ""
    return (
        f'<li><article class="photo-issue-card{modifier}">'
        f'<a class="photo-issue-card-link" href="{h(issue_url(issue))}">'
        f'<img src="{h(cover_url(issue))}" alt="{h(shot["alt"])}" '
        f'width="{width}" height="{height}"{ratio} loading="lazy" decoding="async">'
        '<div class="photo-issue-card-copy">'
        f'<p class="photo-issue-number">第{h(issue["number"])}号 · {_model_name(model)}</p>'
        f'<p class="photo-issue-cover-name" aria-hidden="true">{h(model["name"])}</p>'
        f'<h2>{h(issue["title"])}</h2>'
        f'<p class="photo-issue-card-summary">{h(issue["summary"])}</p>'
        f'<p class="photo-issue-card-date">公開日 {_publication_date(issue)}</p>'
        '<span class="photo-issue-open">この号を読む <span aria-hidden="true">↗</span></span>'
        '</div></a></article></li>'
    )


def render_photo_index(published_issues, models, layout, *, stories_markup=""):
    """Show the latest published issue first, independently of input order."""
    model_by_id = {model["id"]: model for model in models}
    newest_first = _released_issues(published_issues)
    cards = "".join(_issue_card(issue, issue.get("model_snapshot") or model_by_id[issue["model_id"]])
                    for issue in newest_first)
    archive = (f'<ol class="photo-issues-grid">{cards}</ol>' if cards else
               '<p class="photo-issues-empty">公開号はまだありません。</p>')
    subscription = (f'<p class="photo-issues-subscribe"><a href="{h(feed_url())}" '
                    'type="application/rss+xml">RSSで購読</a></p>' if newest_first else "")
    main = (
        '<div class="container photo-issues-page">'
        '<nav class="photo-issue-breadcrumb" aria-label="現在位置">'
        '<a href="/">トップ</a> / <span aria-current="page">週刊グラビア</span></nav>'
        '<header class="photo-issues-intro">'
        '<p class="photo-issue-eyebrow">未発生新聞 / PHOTO & COLUMN</p>'
        '<h1>週刊グラビア</h1>'
        f'<p class="photo-issue-credit">{CREDIT}</p>'
        f'{subscription}</header>{_photo_navigation("issues")}{stories_markup}'
        f'{_rankings(newest_first, model_by_id)}{archive}</div>'
    )
    feed = {"feed_url": feed_url()} if newest_first else {}
    return layout("週刊グラビア", "写真と、架空のモデルが語る短い未来コラム。",
                  index_url(), main, active="photos", kind="photo_collection", **feed)


def _photo_figure(issue, shot, *, cover=False):
    modifier = " photo-issue-shot--cover" if cover else ""
    width, height, supplied = _image_size(issue, shot["id"])
    if supplied:
        modifier += " photo-issue-shot--original-ratio"
        if height > width:
            modifier += " photo-issue-shot--portrait"
    ratio = f' style="aspect-ratio:{width}/{height}"' if supplied else ""
    loading = 'loading="eager" fetchpriority="high"' if cover else 'loading="lazy"'
    return (
        f'<figure class="photo-issue-shot{modifier}" id="photo-{h(shot["id"])}">'
        f'<img src="{h(photo_image_url(issue, shot["id"]))}" alt="{h(shot["alt"])}" '
        f'width="{width}" height="{height}"{ratio} {loading} decoding="async">'
        '<figcaption>'
        f'<span class="photo-issue-shot-number" aria-hidden="true">{h(shot["id"])}</span>'
        f'<span>{h(shot["caption"])}</span></figcaption></figure>'
    )


def _share_controls(issue):
    cover = issue["images"][cover_shot(issue)["id"]]
    url = "https://veripsa.com" + issue_url(issue) + "?v=" + cover["sha256"][:12]
    intent = "https://x.com/intent/tweet?" + urlencode({
        "text": "【週刊グラビア】" + issue["title"] + "\n#未発生新聞",
        "url": url + "&utm_source=x&utm_medium=social&utm_campaign=reader_share&utm_content=reader_entry_v3",
    })
    return (
        '<nav class="engagement-actions" aria-label="この号を共有">'
        f'<a class="share-control" href="{h(intent)}" target="_blank" rel="noopener noreferrer" '
        'aria-label="Xでこの号を共有"><span aria-hidden="true">𝕏</span><span>Xで共有</span></a>'
        f'<button class="copy-control" type="button" data-copy-url="{h(url)}">'
        '<span aria-hidden="true">↗</span><span>リンクをコピー</span></button></nav>'
    )


def _issue_engagement(issue):
    key = h("photo-issue:" + issue["slug"])
    return (
        '<div class="photo-issue-engagement">'
        f'<span class="article-pv" data-article-pv="{key}" data-pv-label="この号" '
        'title="計測開始後のページ表示回数" aria-label="この号のPVを取得中">'
        '<strong data-article-pv-count>—</strong><span>PV</span></span>'
        '<div class="engagement-actions">'
        f'<button class="like-control" type="button" data-like-slug="{key}" '
        'data-like-label="この号にいいね" aria-label="この号にいいね" aria-pressed="false">'
        '<span aria-hidden="true">♡</span><span class="like-label">いいね</span>'
        f'<span class="like-count" data-like-count="{key}" hidden></span></button>'
        '<a class="comment-control" href="#reader-comments" aria-label="この号のコメント欄へ">'
        '<span aria-hidden="true">✎</span><span>コメント</span>'
        f'<span class="comment-count" data-comment-count="{key}" hidden></span></a>'
        '</div><p class="photo-issue-engagement-status" data-engagement-status role="status"></p></div>'
    )


def render_photo_issue(issue, model, layout):
    """Render photographs and the model's column without exposing production data."""
    setting = issue["setting"]
    first = cover_shot(issue)
    remaining = [shot for shot in issue["shots"] if shot["id"] != first["id"]]
    column = "".join(f'<p>{h(paragraph)}</p>' for paragraph in issue["column"])
    opening = "".join(_photo_figure(issue, shot) for shot in remaining[:4])
    closing = "".join(_photo_figure(issue, shot) for shot in remaining[4:])
    source = issue.get("source_article", {}).get("slug")
    related = (f'<p class="photo-issue-related"><a href="/articles/{h(source)}/">'
               f'{h(model["name"])}が登場する記事を読む <span aria-hidden="true">↗</span></a></p>'
               if source else "")
    main = (
        '<article class="container photo-issues-page photo-issue-page" '
        f'data-engagement-key="{h("photo-issue:" + issue["slug"])}">'
        '<nav class="photo-issue-breadcrumb" aria-label="現在位置">'
        f'<a href="/">トップ</a> / <a href="{h(index_url())}">週刊グラビア</a>'
        f' / <span aria-current="page">第{h(issue["number"])}号</span></nav>'
        '<div class="photo-issue-masthead">'
        f'<a href="{h(index_url())}">週刊グラビア</a>'
        f'<span>第{h(issue["number"])}号</span></div>'
        '<div class="photo-issue-cover-spread">'
        f'{_photo_figure(issue, first, cover=True)}'
        '<header class="photo-issue-header">'
        '<p class="photo-issue-eyebrow">COVER STORY</p>'
        f'<h1>{h(issue["title"])}</h1>'
        f'<p class="photo-issue-dek">{h(issue["summary"])}</p>'
        '<div class="photo-issue-byline">'
        f'<p class="photo-issue-model"><a href="{h(model_url(model))}">{_model_name(model)}</a></p>'
        f'<p class="photo-issue-date">公開日 {_publication_date(issue)}</p></div>'
        f'<p class="photo-issue-setting">舞台：{h(setting["year"])}年{h(setting["month"])}月、'
        f'{h(setting["location"])}</p>'
        f'<p class="photo-issue-credit">{CREDIT}</p>'
        f'{_issue_engagement(issue)}</header></div>'
        '<nav class="photo-issue-contents" aria-label="この号の目次">'
        '<a href="#photo-issue-gallery-title">写真を見る <span aria-hidden="true">↓</span></a>'
        '<a href="#photo-issue-column-title">本人コラム <span aria-hidden="true">↓</span></a></nav>'
        '<section class="photo-issue-gallery" aria-labelledby="photo-issue-gallery-title">'
        '<h2 class="photo-issue-visually-hidden" id="photo-issue-gallery-title">写真</h2>'
        f'{opening}</section>'
        '<section class="photo-issue-column" aria-labelledby="photo-issue-column-title">'
        '<p class="photo-issue-eyebrow">COLUMN</p>'
        f'<h2 id="photo-issue-column-title">{h(model["name"])}の未来コラム</h2>'
        f'{column}</section>'
        '<section class="photo-issue-gallery" aria-label="写真の続き">'
        f'{closing}</section>'
        f'{related}'
        f'{_share_controls(issue)}'
        f'{render_reader_comments("photo-issue:" + issue["slug"], heading="この号へのコメント", placeholder="写真やコラムへの感想をひとこと…")}'
        '<nav class="photo-issue-footer" aria-label="週刊グラビアの案内">'
        f'<a href="{h(model_url(model))}">{h(model["name"])}のプロフィール</a>'
        f'<a href="{MODEL_INDEX_URL}">モデル一覧</a>'
        f'<a href="{h(index_url())}">号一覧に戻る <span aria-hidden="true">→</span></a>'
        '</nav></article>'
    )
    return layout(issue["title"], issue["summary"], issue_url(issue), main,
                  active="photos", kind="photo_issue", photo_issue=issue, feed_url=feed_url())


def _public_model(model):
    """Explicit reader fields only; never serialize the internal model record."""
    character = model.get("character", {})
    personality = character.get("personality", "")
    if isinstance(personality, list):
        personality = "、".join(personality)
    return {"id": model["id"], "name": model["name"], "age": model["age"],
            "occupation": character.get("occupation", ""),
            "personality": personality,
            "likes": list(character.get("likes", [])), "quirk": character.get("weakness", "")}


def _model_photo(model, issue, *, eager=False, portrait=None):
    if issue is None and portrait is None:
        return ('<div class="photo-model-nameplate" aria-hidden="true">'
                f'<span>{h(model["name"])}</span></div>')
    if issue is not None:
        shot = cover_shot(issue)
        width, height, _supplied = _image_size(issue, shot["id"])
        url, alt = cover_url(issue), shot["alt"]
    else:
        width, height = portrait["width"], portrait["height"]
        url, alt = portrait["url"], portrait["alt"]
    loading = 'loading="eager" fetchpriority="high"' if eager else 'loading="lazy"'
    return (f'<img class="photo-model-image" src="{h(url)}" '
            f'alt="{h(alt)}" width="{width}" height="{height}" '
            f'{loading} decoding="async">')


def _model_share_controls(model, latest, portrait=None):
    cover_hash = (latest["images"][cover_shot(latest)["id"]]["sha256"] if latest else
                  portrait["sha256"] if portrait else "")
    version = hashlib.sha256(json.dumps([model, cover_hash], ensure_ascii=False,
                                       sort_keys=True).encode("utf-8")).hexdigest()[:12]
    url = "https://veripsa.com" + model_url(model) + "?v=" + version
    intent = "https://x.com/intent/tweet?" + urlencode({
        "text": "【週刊グラビア】" + model["name"] + "のプロフィール\n#未発生新聞", "url": url,
    })
    return ('<nav class="engagement-actions" aria-label="プロフィールを共有">'
            f'<a class="share-control" href="{h(intent)}" target="_blank" rel="noopener noreferrer">'
            '<span aria-hidden="true">𝕏</span><span>Xで共有</span></a>'
            f'<button class="copy-control" type="button" data-copy-url="{h(url)}">'
            '<span aria-hidden="true">↗</span><span>リンクをコピー</span></button></nav>')


def render_model_index(models, published_issues, layout, *, root=ROOT, portraits=None):
    released = _released_issues(published_issues)
    portraits = ({model["id"]: validate_profile_portrait(model, root=root) for model in models}
                 if portraits is None else portraits)
    cards = []
    for model in models:
        public = _public_model(model)
        latest = next((issue for issue in released if issue["model_id"] == public["id"]), None)
        cards.append('<li><article class="photo-model-card">'
                     f'<a href="{h(model_url(public))}" class="photo-model-card-link">'
                     f'{_model_photo(public, latest, portrait=portraits.get(public["id"]))}<div class="photo-model-card-copy">'
                     f'<h2>{_model_name(public)}</h2>'
                     f'<p class="photo-model-occupation">{h(public["occupation"])}</p>'
                     f'<p>{h(public["personality"])}</p>'
                     '<span class="photo-model-open">プロフィール <span aria-hidden="true">↗</span></span>'
                     '</div></a></article></li>')
    main = ('<div class="container photo-issues-page photo-models-page">'
            '<nav class="photo-issue-breadcrumb" aria-label="現在位置">'
            f'<a href="/">トップ</a> / <a href="{h(index_url())}">週刊グラビア</a>'
            ' / <span aria-current="page">モデル一覧</span></nav>'
            '<header class="photo-issues-intro"><p class="photo-issue-eyebrow">PHOTO & COLUMN</p>'
            '<h1>モデル一覧</h1><p class="photo-issue-credit">登場するモデルはすべて架空の成人です。写真はAI生成です。</p></header>'
            f'{_photo_navigation("models")}<ul class="photo-models-grid">{"".join(cards)}</ul></div>')
    cover = {"photo_cover_issue": released[0]} if released else {}
    if not released:
        portrait = next((value for value in portraits.values() if value), None)
        if portrait:
            cover["photo_model_image"] = portrait
    return layout("モデル一覧", "週刊グラビアに登場する架空の成人モデルのプロフィールと掲載号。",
                  MODEL_INDEX_URL, main, active="photos", kind="photo_collection", **cover)


def _model_photo_grid(released):
    photos = []
    for issue in released:
        for shot in issue["shots"]:
            width, height, _supplied = _image_size(issue, shot["id"])
            photos.append('<li><a class="photo-model-grid-link" '
                          f'href="{h(issue_url(issue))}#photo-{h(shot["id"])}">'
                          f'<img src="{h(photo_image_url(issue, shot["id"]))}" '
                          f'alt="{h(shot["alt"])}" width="{width}" height="{height}" '
                          'loading="lazy" decoding="async">'
                          f'<span class="photo-issue-visually-hidden">第{h(issue["number"])}号・写真{h(shot["id"])}</span>'
                          '</a></li>')
    return f'<ul class="photo-model-photo-grid">{"".join(photos)}</ul>'


def render_model_profile(model, published_issues, layout, *, stories_markup="", root=ROOT, portrait=None):
    public = _public_model(model)
    portrait = validate_profile_portrait(model, root=root) if portrait is None else portrait
    released = [issue for issue in _released_issues(published_issues) if issue["model_id"] == public["id"]]
    latest = released[0] if released else None
    facts = []
    for label, value in (("仕事", public["occupation"]), ("人柄", public["personality"]),
                         ("好きなもの", "、".join(public["likes"])), ("ちょっとした癖", public["quirk"])):
        if value:
            facts.append(f'<div><dt>{label}</dt><dd>{h(value)}</dd></div>')
    photo_credit = (f'<p class="photo-model-photo-source">プロフィール写真：<a href="{h(issue_url(latest))}">'
                    f'第{h(latest["number"])}号</a> · AI生成</p>'
                    if latest else f'<p class="photo-model-photo-source"><a href="{h(portrait["url"])}">'
                    'プロフィール写真を見る</a> · AI生成</p>' if portrait else "")
    issue_links = "".join(f'<li><a href="{h(issue_url(issue))}">第{h(issue["number"])}号 · {h(issue["title"])}</a>'
                          f'<span>公開日 {_publication_date(issue)}</span></li>' for issue in released)
    archive = f'<ol class="photo-model-issue-list">{issue_links}</ol>' if released else ""
    issue_section = ('<section class="photo-model-issues" aria-labelledby="model-issues-title">'
                     f'<h2 id="model-issues-title">{h(public["name"])}の掲載号</h2>{archive}</section>'
                     if released else "")
    photo_count = sum(len(issue["shots"]) for issue in released)
    grid = (_model_photo_grid(released) if released else
            '<p class="photo-issues-empty">掲載号はまだありません。</p>')
    main = ('<article class="container photo-issues-page photo-model-page">'
            '<nav class="photo-issue-breadcrumb" aria-label="現在位置">'
            f'<a href="/">トップ</a> / <a href="{h(index_url())}">週刊グラビア</a>'
            f' / <a href="{MODEL_INDEX_URL}">モデル一覧</a> / <span aria-current="page">{h(public["name"])}</span></nav>'
            f'{_photo_navigation("models")}<header class="photo-model-profile">'
            f'<div class="photo-model-avatar">{_model_photo(public, latest, eager=True, portrait=portrait)}</div>'
            '<div class="photo-model-bio"><p class="photo-issue-eyebrow">PROFILE</p>'
            f'<h1>{h(public["name"])}</h1><p class="photo-model-age">{h(public["age"])}歳 · 架空の成人モデル</p>'
            '<div class="photo-model-stats" aria-label="公開中の写真と掲載号">'
            f'<p><strong>{photo_count}</strong><span>掲載カット</span></p>'
            f'<p><strong>{len(released)}</strong><span>掲載号</span></p></div>'
            f'<dl>{"".join(facts)}</dl>{photo_credit}{_model_share_controls(public, latest, portrait)}</div></header>'
            f'{stories_markup}<section class="photo-model-photos" aria-labelledby="model-photos-title">'
            f'<h2 id="model-photos-title">写真</h2>{grid}</section>'
            f'{issue_section}</article>')
    description = f'{public["name"]}（{public["age"]}歳）のプロフィール。{public["occupation"]}。{public["personality"]}'
    return layout(public["name"] + "のプロフィール", description, model_url(public), main,
                  active="photos", kind="photo_model", photo_model=public,
                  photo_cover_issue=latest, photo_model_image=portrait if latest is None else None,
                  indexable=bool(released or portrait))


def build_photo_models(models, published_issues, layout, write, *, stories_renderer=None, root=ROOT, output=None):
    if not models:
        return
    portraits = {model["id"]: validate_profile_portrait(model, root=root) for model in models}
    if output is not None:
        released_ids = {issue["model_id"] for issue in _released_issues(published_issues)}
        for model_id, portrait in portraits.items():
            if portrait and model_id not in released_ids:
                copy_profile_portrait(portrait, output, root=root)
    write(MODEL_INDEX_URL.lstrip("/") + "index.html",
          render_model_index(models, published_issues, layout, root=root, portraits=portraits))
    for model in models:
        stories_markup = stories_renderer(model["id"]) if stories_renderer else ""
        write(model_url(model).lstrip("/") + "index.html",
              render_model_profile(model, published_issues, layout, stories_markup=stories_markup,
                                   root=root, portrait=portraits[model["id"]]))


def model_sitemap_entries(models, published_issues, *, root=ROOT):
    """Only a released cover or validated portrait makes a profile indexable."""
    released = _released_issues(published_issues)
    if not models:
        return []
    portraits = {model["id"]: validate_profile_portrait(model, root=root) for model in models}
    latest = released[0] if released else None
    first_portrait = next((value for value in portraits.values() if value), None)
    entries = [(MODEL_INDEX_URL, latest["published_at"] if latest else
                first_portrait["reviewed_at"] if first_portrait else None,
                cover_url(latest) if latest else first_portrait["url"] if first_portrait else None)]
    for model in models:
        latest = next((issue for issue in released if issue["model_id"] == model["id"]), None)
        if latest:
            entries.append((model_url(model), latest["published_at"], cover_url(latest)))
        elif portraits[model["id"]]:
            portrait = portraits[model["id"]]
            entries.append((model_url(model), portrait["reviewed_at"], portrait["url"]))
    return entries
