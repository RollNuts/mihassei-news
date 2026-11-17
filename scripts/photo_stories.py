"""Read-only fictional photo stories bound to currently published issue JPEGs.

The build may pass its already-validated catalog and published releases to avoid
revalidating the whole image archive. No master, candidate, or article image is a
story source. Browser speech uses local Japanese voices, never generated files.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

if __package__:
    from .photo_story_motion import validate_story_motion
    from .photo_issues import (ROOT, image_path, issue_url, load_photo_issues,
                               photo_image_url, published_issues)
else:
    from photo_story_motion import validate_story_motion
    from photo_issues import (ROOT, image_path, issue_url, load_photo_issues,
                             photo_image_url, published_issues)

JST = timezone(timedelta(hours=9))
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
# Gentle delivery settings are character direction, not an imitation of a person.
# Voice timbre is supplied by the reader's device and can differ between devices.
VOICE_PROFILES = {
    "nagino-saya": (0.98, 1.02), "shioba-rio": (0.94, 0.98),
    "tsukishiro-rena": (0.92, 0.96), "hanase-mio": (1.02, 1.05),
    "shiramine-yuki": (0.90, 1.00), "amane-hikari": (1.04, 1.06),
    "kiritani-sumire": (0.95, 0.99),
}
FIELDS = {"id", "model_id", "published_at", "setting", "text", "source"}
MAX_PHOTO_STORIES = 5000
STORY_STRIP_POSTS_PER_MODEL = 3
# Preserve the initial same-day history; new daily publication uses JST dates.
DAILY_POST_LIMIT_START_JST = date(2026, 11, 17)


def _stamp(value):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid photo story timestamp") from exc
    if stamp.tzinfo is None:
        raise ValueError("Photo story timestamp needs a timezone")
    return stamp


def _text(value, limit, label, *, multiline=False):
    controls = r"[<>\x00-\x09\x0b-\x1f\x7f]" if multiline else r"[<>\x00-\x1f\x7f]"
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or len(value) > limit or re.search(controls, value)):
        raise ValueError("Invalid photo story " + label)
    return value


def validate_photo_stories(data, *, catalog, published, root=ROOT, now=None):
    """Return reader fields only; refuse stale identities or changed source bytes."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Photo story clock needs a timezone")
    if (not isinstance(data, dict) or set(data) != {"schema_version", "stories"}
            or data["schema_version"] != 1 or not isinstance(data["stories"], list)
            or len(data["stories"]) > MAX_PHOTO_STORIES):
        raise ValueError("Invalid photo story catalog")
    models = {model["id"]: model for model in catalog["models"]}
    current = {issue["slug"]: issue for issue in catalog["issues"]}
    releases = {issue["slug"]: issue for issue in published if issue.get("state") == "published"}
    seen, texts, days, result = set(), set(), set(), []
    for story in data["stories"]:
        if (not isinstance(story, dict) or set(story) not in (FIELDS, FIELDS | {"motion"})
                or not isinstance(story.get("id"), str) or len(story["id"]) > 100
                or not SLUG.fullmatch(story["id"]) or story["id"] in seen):
            raise ValueError("Invalid photo story identity")
        seen.add(story["id"])
        model = models.get(story.get("model_id"))
        if (not model or model["id"] not in VOICE_PROFILES or model.get("fictional") is not True
                or type(model.get("age")) is not int or not 20 <= model["age"] <= 99):
            raise ValueError("Photo story needs a fictional adult model")
        text = _text(story["text"], 180, "text", multiline=True)
        # Japanese personal posts naturally omit pronouns. Authorship comes
        # from the model/source binding, never the literal word 私 or わたし.
        if len(text) < 3:
            raise ValueError("Photo story needs a short personal text")
        signature = (model["id"], text)
        if signature in texts:
            raise ValueError("Duplicate photo story text for this model")
        texts.add(signature)
        source = story["source"]
        if (not isinstance(source, dict) or set(source) != {"issue_slug", "cut_id", "image_sha256"}
                or not isinstance(source["issue_slug"], str) or not SLUG.fullmatch(source["issue_slug"])
                or not isinstance(source["cut_id"], str) or not re.fullmatch(r"(?:0[1-9]|10)", source["cut_id"])
                or not isinstance(source["image_sha256"], str) or not SHA.fullmatch(source["image_sha256"])):
            raise ValueError("Invalid photo story source binding")
        issue = current.get(source["issue_slug"])
        release = releases.get(source["issue_slug"])
        if (not issue or not release or issue.get("state") != "published"
                or issue.get("issue_sha256") != release.get("issue_sha256")
                or issue.get("model_id") != model["id"] or release.get("model_id") != model["id"]):
            raise ValueError("Photo story source is not a current published issue for this model")
        stamp, issue_stamp = _stamp(story["published_at"]), _stamp(issue["published_at"])
        if stamp < issue_stamp:
            raise ValueError("Photo story predates its source publication")
        day = stamp.astimezone(JST).date()
        if day >= DAILY_POST_LIMIT_START_JST:
            day_key = (model["id"], day)
            if day_key in days:
                raise ValueError("More than one photo story for this model on the same JST day")
            days.add(day_key)
        setting = story["setting"]
        if (not isinstance(setting, dict) or set(setting) != {"year", "month"}
                or type(setting["year"]) is not int or type(setting["month"]) is not int
                or setting["year"] != issue["setting"]["year"]
                or setting["month"] != issue["setting"]["month"]):
            raise ValueError("Photo story setting must match its fictional source setting")
        cut, sha = source["cut_id"], source["image_sha256"]
        shot = next((shot for shot in issue["shots"] if shot["id"] == cut), None)
        image = issue["images"].get(cut)
        review = image.get("review", {}) if isinstance(image, dict) else {}
        if (not shot or not image or image.get("sha256") != sha
                or image.get("path") != image_path(issue, cut)
                or review.get("decision") != "accepted" or review.get("image_sha256") != sha
                or review.get("cut_id") != cut or review.get("issue_sha256") != issue["issue_sha256"]):
            raise ValueError("Photo story source has no matching accepted published cut")
        path = Path(root) / image["path"]
        if (any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != sha):
            raise ValueError("Photo story source JPEG bytes changed")
        width, height = image.get("width", 1200), image.get("height", 675)
        if (type(width) is not int or type(height) is not int
                or min(width, height) <= 0 or max(width, height) > 10000):
            raise ValueError("Invalid photo story image dimensions")
        # Scheduled entries and sources are omitted, never advertised as already posted.
        if stamp > now or issue_stamp > now:
            continue
        date = stamp.astimezone(JST)
        rate, pitch = VOICE_PROFILES[model["id"]]
        motion = (validate_story_motion(story["motion"], story_id=story["id"],
                                        image_sha256=sha, root=root) if "motion" in story else None)
        result.append({"id": story["id"], "modelId": model["id"], "name": _text(model["name"], 50, "model name"),
                       "text": text, "publishedAt": story["published_at"],
                       "dateLabel": f"{date.year}年{date.month}月{date.day}日 {date:%H:%M}",
                       "settingLabel": f'舞台：{setting["year"]}年{setting["month"]}月',
                       "image": photo_image_url(issue, cut), "alt": _text(shot["alt"], 300, "image alt"),
                       "sourceUrl": issue_url(issue) + "#photo-" + cut,
                       "profileUrl": "/photo/models/" + model["id"] + "/",
                       "width": width, "height": height,
                       **({"motion": motion} if motion else {}),
                       "voice": {"rate": rate, "pitch": pitch}})
    return tuple(sorted(result, key=lambda story: _stamp(story["publishedAt"]), reverse=True))


def load_photo_stories(root=ROOT, *, catalog=None, published=None, now=None):
    path = Path(root) / "content/photo_stories.json"
    if not path.exists():
        return ()
    if path.is_symlink():
        raise ValueError("Unsafe photo story catalog symlink")
    catalog = catalog if catalog is not None else load_photo_issues(root)
    published = published if published is not None else published_issues(catalog, root=root, now=now)
    return validate_photo_stories(json.loads(path.read_text(encoding="utf-8")),
                                  catalog=catalog, published=published, root=root, now=now)


def _h(value):
    return html.escape(str(value), quote=True)


def render_story_strip(stories=None, *, root=ROOT):
    """Render only real published stories; the caller loads assets in its layout."""
    stories = load_photo_stories(root) if stories is None else stories
    if not stories:
        return ""
    # The full history remains in each person's native archive, not the modal.
    recent, counts = [], {}
    for story in stories:
        model_id = story["modelId"]
        if counts.get(model_id, 0) < STORY_STRIP_POSTS_PER_MODEL:
            recent.append(story)
            counts[model_id] = counts.get(model_id, 0) + 1
    stories = recent
    latest = {}
    for story in stories:
        latest.setdefault(story["modelId"], story)
    circles = "".join(
        f'<li><a class="photo-story-open" href="{_h(story["sourceUrl"])}" '
        f'data-story-open="{_h(model_id)}" aria-haspopup="dialog" '
        f'aria-label="{_h(story["name"])}、{_h(story["dateLabel"])}投稿のストーリーズを開く">'
        f'<span class="photo-story-circle"><img src="{_h(story["image"])}" alt="" '
        'width="80" height="80" loading="lazy" decoding="async"></span>'
        f'<span class="photo-story-name">{_h(story["name"])}</span>'
        f'<time class="photo-story-posted" datetime="{_h(story["publishedAt"])}">'
        f'{_stamp(story["publishedAt"]).astimezone(JST):%m/%d %H:%M}</time></a></li>'
        for model_id, story in latest.items())
    payload = json.dumps(list(stories), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    for char, escaped in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"),
                          ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        payload = payload.replace(char, escaped)
    fallback = "".join(f'<p>{_h(story["name"])}：{_h(story["text"])}</p>' for story in stories)
    return ('<section class="photo-stories" data-photo-stories aria-label="ストーリーズ">'
            '<h2>ストーリーズ</h2><p class="photo-story-label">創作投稿・端末の合成音声</p>'
            f'<ul class="photo-stories-list">{circles}</ul><noscript>{fallback}</noscript>'
            f'<script type="application/json" data-photo-story-data>{payload}</script>'
            '<dialog class="photo-story-dialog" data-story-dialog aria-label="ストーリーズ">'
            '<div class="photo-story-dialog-top"><h3 data-story-name></h3>'
            '<button type="button" data-story-close autofocus>閉じる</button></div>'
            '<p class="photo-story-label">創作投稿・端末の合成音声</p>'
            '<img class="photo-story-image" data-story-image alt="" hidden>'
            '<video class="photo-story-image" data-story-video controls playsinline muted preload="metadata" hidden></video>'
            '<p class="photo-story-label" data-story-motion-label hidden>写真モーション · 12秒・音声なし</p>'
            '<p class="photo-story-text" data-story-text></p>'
            '<p class="photo-story-meta">投稿日 <time data-story-date></time>'
            ' · <span data-story-setting></span></p>'
            '<div class="photo-story-actions"><button type="button" data-story-audio disabled>音声を聴く</button>'
            '<button type="button" data-story-previous hidden>前の投稿</button>'
            '<button type="button" data-story-next hidden>次の投稿</button>'
            '<a data-story-source>写真の掲載号</a></div>'
            '<p class="photo-story-status" data-story-status role="status" aria-live="polite"></p>'
            '</dialog></section>')


def render_model_stories(model_id, stories=None, *, root=ROOT):
    if not isinstance(model_id, str) or not SLUG.fullmatch(model_id):
        raise ValueError("Invalid photo story model id")
    stories = load_photo_stories(root) if stories is None else stories
    own_stories = tuple(story for story in stories if story["modelId"] == model_id)
    if not own_stories:
        return ""
    return _render_timeline(own_stories, title=own_stories[0]["name"] + "の近況",
                            title_id="model-timeline-title")


def render_photo_timeline(stories):
    """Static, dated posts remain readable without JavaScript or speech support."""
    return _render_timeline(stories, title="7人の近況", title_id="cast-timeline-title",
                            limit=7, model_directory=True)


def _render_timeline(stories, *, title, title_id, limit=None, model_directory=False):
    if not stories:
        return ""
    cards = []
    for story in stories[:limit]:
        image = (f'<img src="{_h(story["image"])}" alt="{_h(story["alt"])}" '
                 f'width="{story["width"]}" height="{story["height"]}" loading="lazy" decoding="async">')
        motion = story.get("motion")
        media = (f'<a class="photo-post-image" href="{_h(story["sourceUrl"])}">{image}</a>')
        if motion:
            media = ('<figure class="photo-post-motion" data-story-motion>'
                     f'<video controls playsinline muted preload="none" poster="{_h(story["image"])}" '
                     f'width="{motion["width"]}" height="{motion["height"]}" aria-label="{_h(story["name"])}の写真モーション">'
                     f'<source src="{_h(motion["url"])}" type="video/mp4">{image}</video>'
                     f'<a class="photo-post-image" href="{_h(story["sourceUrl"])}" data-story-motion-fallback hidden>{image}</a>'
                     '<figcaption>写真モーション · 12秒・音声なし</figcaption></figure>')
        permalink = story["profileUrl"] + "#post-" + story["id"]
        cards.append(f'<li><article class="photo-post" id="post-{_h(story["id"])}">'
                     f'<header><a class="photo-post-person" href="{_h(story["profileUrl"])}">'
                     f'<img src="{_h(story["image"])}" alt="" width="44" height="44" loading="lazy">'
                     f'<strong>{_h(story["name"])}</strong></a>'
                     f'<a href="{_h(permalink)}"><time datetime="{_h(story["publishedAt"])}">'
                     f'{_h(story["dateLabel"])}</time></a></header>'
                     f'<p class="photo-post-text">{_h(story["text"])}</p>{media}'
                     f'<footer><span>{_h(story["settingLabel"])}</span>'
                     f'<a href="{_h(story["sourceUrl"])}">写真の掲載特集 ↗</a></footer></article></li>')
    earlier = ('<details class="photo-post-archive">'
               f'<summary>以前の投稿（{len(cards) - 3}件）</summary>'
               '<ol class="photo-post-list" start="4">' + ''.join(cards[3:]) + '</ol></details>'
               if len(cards) > 3 else '')
    directory = '<a href="/photo/models/">プロフィール一覧 ↗</a>' if model_directory else ''
    label = ('架空の7人による創作投稿・写真は掲載特集から' if model_directory else
             '創作投稿・写真は掲載特集から')
    return (f'<section class="cast-timeline" aria-labelledby="{_h(title_id)}">'
            f'<div class="section-head"><h2 id="{_h(title_id)}">{_h(title)}</h2>{directory}</div>'
            f'<p class="photo-story-label">{label}</p>'
            + render_story_strip(stories) + '<ol class="photo-post-list">' + ''.join(cards[:3])
            + '</ol>' + earlier + '</section>')
