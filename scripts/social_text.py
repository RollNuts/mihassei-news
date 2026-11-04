"""Conservative X text budgets and hashtags from the site's published topics.

ASCII counts as one unit and other code points as two. This intentionally
overcounts some Unicode sequences; it never relies on paid long-post access.
Only ASCII links to our own site, supplied by our generators, use the fixed
23-unit link allowance. URL-looking editorial prose is counted conservatively.
"""
from __future__ import annotations

import re


URL_RE = re.compile(r"(?<!\S)https://veripsa\.com/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*(?=\s|$)")
HASHTAG_RE = re.compile(r"\w{1,12}\Z")
X_TEXT_LIMIT = 280


def article_hashtags(article: dict, limit: int = 2) -> list[str]:
    """Reuse up to two safe topic labels in source order, without inventing tags.

    Invalid labels are skipped whole, never changed by removing punctuation or
    normalizing their spelling. The published topic limit is twelve characters.
    """
    if type(limit) is not int:
        raise ValueError("Hashtag limit must be an integer")
    maximum = min(2, max(0, limit))
    tags = article.get("tags", [])
    if not maximum or not isinstance(tags, list):
        return []
    result, seen = [], set()
    for tag in tags:
        if not isinstance(tag, str):
            continue
        if (not HASHTAG_RE.fullmatch(tag)
                or not any(char.isalpha() for char in tag)):
            continue
        identity = tag.casefold()
        if identity in seen:
            continue
        result.append("#" + tag)
        seen.add(identity)
        if len(result) == maximum:
            break
    return result


def _weighted_tokens(text: str):
    """Keep generated URLs atomic so shortening cannot break a link."""
    position = 0
    for match in URL_RE.finditer(text):
        for char in text[position:match.start()]:
            yield char, 1 if ord(char) < 128 else 2
        yield match.group(), 23
        position = match.end()
    for char in text[position:]:
        yield char, 1 if ord(char) < 128 else 2


def x_weighted_length(text: str) -> int:
    return sum(weight for _token, weight in _weighted_tokens(text))


def truncate_x_text(text: str, budget: int) -> str:
    """Fit editorial text, including an ellipsis, within a weighted budget.

    Required hashtags and links should be budgeted separately by the caller.
    """
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 0:
        raise ValueError("X text budget must be a non-negative integer")
    if x_weighted_length(text) <= budget:
        return text
    if budget < 2:
        return ""
    parts, used = [], 0
    for token, weight in _weighted_tokens(text):
        if used + weight > budget - 2:
            break
        parts.append((token, weight))
        used += weight
    # A URL needs whitespace before the ellipsis to retain its 23-unit token.
    # Otherwise stripping its trailing separator would turn it back into raw
    # URL-looking prose, which can be hundreds of units long. If the separator
    # does not fit, remove the whole URL rather than returning a broken link.
    while parts:
        token, weight = parts[-1]
        if token.isspace():
            parts.pop()
            used -= weight
            continue
        suffix = " …" if URL_RE.fullmatch(token) else "…"
        if used + x_weighted_length(suffix) <= budget:
            return "".join(part for part, _weight in parts) + suffix
        parts.pop()
        used -= weight
    return "…"
