"""Conservative X text budgets for our generated copy and explicit links.

ASCII counts as one unit and other code points as two. This intentionally
overcounts some Unicode sequences; it never relies on paid long-post access.
Only ASCII links to our own site, supplied by our generators, use the fixed
23-unit link allowance. URL-looking editorial prose is counted conservatively.
"""
from __future__ import annotations

import re


URL_RE = re.compile(r"(?<!\S)https://veripsa\.com/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*(?=\s|$)")
X_TEXT_LIMIT = 280


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
        parts.append(token)
        used += weight
    return "".join(parts).rstrip() + "…"
