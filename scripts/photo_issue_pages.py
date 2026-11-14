"""Static reader pages for validated, published weekly photo issues."""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

if __package__:
    from .photo_issues import cover_shot, cover_url, index_url, issue_url, photo_image_url, feed_url
else:
    from photo_issues import cover_shot, cover_url, index_url, issue_url, photo_image_url, feed_url


CREDIT = "登場人物とコラムは創作、写真はAI生成です。"
JST = timezone(timedelta(hours=9))


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


def render_photo_index(published_issues, models, layout):
    """Show the latest published issue first, independently of input order."""
    model_by_id = {model["id"]: model for model in models}
    newest_first = sorted(published_issues, key=lambda issue: datetime.fromisoformat(
        issue["published_at"].replace("Z", "+00:00")), reverse=True)
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
        f'{subscription}</header>'
        f'{archive}</div>'
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
        '<article class="container photo-issues-page photo-issue-page">'
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
        f'<p class="photo-issue-model">{_model_name(model)}</p>'
        f'<p class="photo-issue-date">公開日 {_publication_date(issue)}</p></div>'
        f'<p class="photo-issue-setting">舞台：{h(setting["year"])}年{h(setting["month"])}月、'
        f'{h(setting["location"])}</p>'
        f'<p class="photo-issue-credit">{CREDIT}</p></header></div>'
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
        '<nav class="photo-issue-footer" aria-label="週刊グラビアの案内">'
        f'<a href="{h(index_url())}">号一覧に戻る <span aria-hidden="true">→</span></a>'
        '</nav></article>'
    )
    return layout(issue["title"], issue["summary"], issue_url(issue), main,
                  active="photos", kind="photo_issue", photo_issue=issue, feed_url=feed_url())
