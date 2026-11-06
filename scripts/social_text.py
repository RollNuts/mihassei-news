"""Conservative X text budgets and context-aware hashtags for article sharing.

ASCII counts as one unit and other code points as two. This intentionally
overcounts some Unicode sequences; it never relies on paid long-post access.
Only ASCII links to our own site, supplied by our generators, use the fixed
23-unit link allowance. URL-looking editorial prose is counted conservatively.
"""
from __future__ import annotations

import re


URL_RE = re.compile(r"(?<!\S)https://veripsa\.com/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*(?=\s|$)")
HASHTAG_RE = re.compile(r"\w{1,12}\Z")
TICKER_RE = re.compile(r"[A-Z][A-Z0-9.]{0,5}\Z")
X_TEXT_LIMIT = 280


GENERIC_X_LABELS = {
    "AI", "SNS", "アプリ", "イベント", "お金", "ニュース", "メディア",
    "仕事", "働き方", "家族", "技術", "教育", "暮らし", "文化", "旅行",
    "生活", "研究", "社会", "経済",
}
SLANG_MARKER_RE = re.compile(r"(?:あるある|ガチャ|コスパ|タイパ|バズ|推し|沼|炎上|界隈|疲れ|活)")
SLANG_RULES = (
    (("ポイ活", "ポイント"), "ポイ活"),
    (("タイパ", "時短", "倍速"), "タイパ"),
    (("コスパ",), "コスパ"),
    (("推し活", "推し"), "推し活"),
    (("転売ヤー", "転売"), "転売ヤー"),
    (("SNS疲れ", "SNS断ち", "デジタルデトックス"), "SNS疲れ"),
    (("サブスク",), "サブスク"),
    (("生成AI",), "生成AI"),
    (("ガチャ",), "ガチャ"),
    (("炎上",), "炎上"),
    (("バズ",), "バズ"),
)


def article_hashtags(article: dict, limit: int = 2) -> list[str]:
    """Choose up to two human-used X terms, never broad site categories.

    Prefer the exact words people would search on X: x_tickers, explicit
    x_tags, then an observed trend term such as 流行語大賞. Only after those
    do we infer slang from the article. Broad site categories such as SNS or
    アプリ are never used merely to fill the post.
    """
    if type(limit) is not int:
        raise ValueError("Hashtag limit must be an integer")
    maximum = min(2, max(0, limit))
    if not maximum:
        return []

    result, seen = [], set()

    def add(label) -> None:
        if len(result) >= maximum or not isinstance(label, str):
            return
        if (not HASHTAG_RE.fullmatch(label)
                or not any(char.isalpha() for char in label)
                or label in GENERIC_X_LABELS):
            return
        identity = label.casefold()
        if identity in seen:
            return
        result.append("#" + label)
        seen.add(identity)

    tickers = article.get("x_tickers", [])
    if isinstance(tickers, list):
        for ticker in tickers:
            if len(result) >= maximum:
                break
            if not isinstance(ticker, str) or not TICKER_RE.fullmatch(ticker):
                continue
            identity = "$" + ticker.casefold()
            if identity in seen:
                continue
            result.append("$" + ticker)
            seen.add(identity)
    if len(result) >= maximum:
        return result

    explicit = article.get("x_tags", [])
    if isinstance(explicit, list):
        for tag in explicit:
            add(tag)
        if len(result) >= maximum:
            return result

    trend = article.get("trend_observation")
    if isinstance(trend, dict):
        add(trend.get("term"))
        if len(result) >= maximum:
            return result

    fields = [article.get("title"), article.get("summary")]
    for key in ("satire_tags", "tags"):
        values = article.get(key, [])
        if isinstance(values, list):
            fields.extend(values)
    searchable = "\n".join(value for value in fields if isinstance(value, str))

    for needles, label in SLANG_RULES:
        if any(needle in searchable for needle in needles):
            add(label)
            if len(result) >= maximum:
                return result

    satire_tags = article.get("satire_tags", [])
    if isinstance(satire_tags, list):
        for tag in satire_tags:
            if isinstance(tag, str) and SLANG_MARKER_RE.search(tag):
                add(tag)
            if len(result) >= maximum:
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
