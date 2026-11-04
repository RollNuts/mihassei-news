"""Shared editorial topics for news and serials, independent of their frozen text.

The registry is the single source for static topic pages, article links and X
hashtags. It does not infer topics from keywords or modify published content.
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
JST = ZoneInfo("Asia/Tokyo")


def _valid_slug(value):
    return isinstance(value, str) and len(value) <= 80 and SLUG.fullmatch(value)


def _hashtag_label(value):
    if not isinstance(value, str):
        raise ValueError("Tag label must be a string")
    label = unicodedata.normalize("NFKC", value)
    if (not 1 <= len(label) <= 24 or not re.fullmatch(r"\w+", label)
            or not any(char.isalpha() for char in label)):
        raise ValueError("Tag label must be 1-24 letters/numbers/underscores and contain a letter")
    return label


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate tag registry key: " + key)
        result[key] = value
    return result


def _validate_registry(registry):
    if not isinstance(registry, dict) or type(registry.get("version")) is not int or registry["version"] != 1:
        raise ValueError("Tag registry version must be 1")
    definitions = registry.get("definitions")
    if not isinstance(definitions, list) or not definitions:
        raise ValueError("Tag definitions must be a non-empty list")
    slugs, labels = set(), set()
    for tag in definitions:
        if not isinstance(tag, dict) or not _valid_slug(tag.get("slug")):
            raise ValueError("Invalid tag slug")
        if tag["slug"] in slugs:
            raise ValueError("Duplicate tag slug: " + tag["slug"])
        label = _hashtag_label(tag.get("label"))
        if label != tag["label"]:
            raise ValueError("Tag label must use its NFKC spelling: " + tag["slug"])
        if label.casefold() in labels:
            raise ValueError("Duplicate tag label: " + label)
        description = tag.get("description", "")
        if not isinstance(description, str) or len(description) > 160:
            raise ValueError("Tag description must be at most 160 characters: " + tag["slug"])
        slugs.add(tag["slug"])
        labels.add(label.casefold())
    for section in ("articles", "episodes"):
        mappings = registry.get(section)
        if not isinstance(mappings, dict):
            raise ValueError("Tag registry needs a " + section + " mapping")
        for ref, assigned in mappings.items():
            parts = ref.split("/") if isinstance(ref, str) else []
            if len(parts) != (2 if section == "episodes" else 1) or not all(_valid_slug(part) for part in parts):
                raise ValueError("Invalid tag reference in " + section + ": " + str(ref))
            if not isinstance(assigned, list) or not 1 <= len(assigned) <= 4:
                raise ValueError("Assign 1-4 tags to " + ref)
            if any(not isinstance(slug, str) for slug in assigned):
                raise ValueError("Tag assignments must be slug strings: " + ref)
            if len(assigned) != len(set(assigned)):
                raise ValueError("Duplicate tag assignment: " + ref)
            unknown = set(assigned) - slugs
            if unknown:
                raise ValueError("Unknown tag for " + ref + ": " + ", ".join(sorted(unknown)))


@lru_cache(maxsize=4)
def load_tags(base: Path = ROOT):
    """Load and validate the registry's shape; callers treat the cache as read-only."""
    path = Path(base) / "content" / "tags.json"
    registry = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    _validate_registry(registry)
    return registry


def _registry(registry):
    return load_tags() if registry is None else registry


def _assigned(section, ref, registry):
    registry = _registry(registry)
    definitions = {tag["slug"]: tag for tag in registry["definitions"]}
    return [definitions[slug] for slug in registry[section].get(ref, [])]


def tags_for_article(article, registry=None):
    return _assigned("articles", article.get("slug", ""), registry)


def tags_for_episode(series, episode, registry=None):
    return _assigned("episodes", series.get("slug", "") + "/" + episode.get("slug", ""), registry)


def tags_for_series(series, registry=None):
    """Union of the published episodes, preserving their editorial tag order."""
    result, seen = [], set()
    for episode in series.get("episodes", []):
        for tag in tags_for_episode(series, episode, registry):
            if tag["slug"] not in seen:
                result.append(tag)
                seen.add(tag["slug"])
    return result


def tag_url(tag):
    slug = tag.get("slug") if isinstance(tag, dict) else tag
    if not _valid_slug(slug):
        raise ValueError("Invalid tag URL slug")
    return "/tags/" + slug + "/"


def render_tag_links(tags, *, label="タグ", compact=False):
    if not tags:
        return ""
    classes = "topic-tags" + (" topic-tags--compact" if compact else "")
    links = "".join(
        '<a class="topic-tag" rel="tag" href="' + html.escape(tag_url(tag), quote=True)
        + '">#' + html.escape(str(tag["label"]), quote=True) + '</a>' for tag in tags
    )
    return '<nav class="' + classes + '" aria-label="' + html.escape(str(label), quote=True) + '">' + links + '</nav>'


def topic_hashtags(tags, limit=2):
    """Safe X hashtags in editorial order, with at most two per shared article."""
    if type(limit) is not int:
        raise ValueError("Hashtag limit must be an integer")
    maximum = min(2, max(0, limit))
    if not maximum:
        return []
    result, seen = [], set()
    for tag in tags:
        label = _hashtag_label(tag["label"])
        if label.casefold() not in seen:
            result.append("#" + label)
            seen.add(label.casefold())
        if len(result) == maximum:
            break
    return result


def article_hashtags(article, limit=2, registry=None):
    return topic_hashtags(tags_for_article(article, registry), limit=limit)


def validate_tag_assignments(articles, serials, registry=None):
    """Reject orphaned, missing, duplicated or unused topic metadata at build time."""
    registry = _registry(registry)
    _validate_registry(registry)
    article_refs = [article["slug"] for article in articles]
    episode_refs = [series["slug"] + "/" + episode["slug"]
                    for series in serials for episode in series.get("episodes", [])]
    for section, refs in (("articles", article_refs), ("episodes", episode_refs)):
        if len(refs) != len(set(refs)):
            raise ValueError("Duplicate published reference in " + section)
        registered = set(registry[section])
        unknown = registered - set(refs)
        if unknown:
            raise ValueError("Unknown " + section + " tag reference: " + ", ".join(sorted(unknown)))
        missing = set(refs) - registered
        if missing:
            raise ValueError("Missing tags for " + section + ": " + ", ".join(sorted(missing)))
    used = {slug for section in ("articles", "episodes") for assigned in registry[section].values() for slug in assigned}
    unused = {tag["slug"] for tag in registry["definitions"]} - used
    if unused:
        raise ValueError("Unused tag definition: " + ", ".join(sorted(unused)))


def _publication_key(item):
    if item["kind"] == "news":
        article = item["article"]
        value = article.get("published_at") or article["source"]["published_at"][:10]
        identity = "/articles/" + article["slug"]
    else:
        value = item["episode"]["published_at"]
        identity = "/serials/" + item["series"]["slug"] + "/" + item["episode"]["slug"]
    published = datetime.fromisoformat(value)
    if published.tzinfo is None:
        published = published.replace(tzinfo=JST)
    # Day-only entries use a day boundary only for sorting. Their source value is
    # never rewritten, so callers can display the day without an invented time.
    return published.astimezone(timezone.utc), identity


def tag_collections(articles, serials, registry=None):
    """Cross-format collections ordered by actual publication, newest first."""
    registry = _registry(registry)
    groups = {tag["slug"]: {"tag": tag, "items": [], "count": 0}
              for tag in registry["definitions"]}
    for article in articles:
        for tag in tags_for_article(article, registry):
            groups[tag["slug"]]["items"].append({"kind": "news", "article": article})
    for series in serials:
        for episode in series.get("episodes", []):
            for tag in tags_for_episode(series, episode, registry):
                groups[tag["slug"]]["items"].append({"kind": series["kind"], "series": series, "episode": episode})
    result = []
    for group in groups.values():
        if group["items"]:
            group["items"].sort(key=_publication_key, reverse=True)
            group["count"] = len(group["items"])
            result.append(group)
    return result
