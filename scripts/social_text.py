"""Conservative X text budgets and context-aware hashtags for article sharing.

ASCII counts as one unit and other code points as two. This intentionally
overcounts some Unicode sequences; it never relies on paid long-post access.
Only ASCII links to our own site, supplied by our generators, use the fixed
23-unit link allowance. URL-looking editorial prose is counted conservatively.
"""
from __future__ import annotations

import re

if __package__:
    from .editorial_copy import reader_copy
else:
    from editorial_copy import reader_copy


URL_RE = re.compile(r"(?<!\S)https://veripsa\.com/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*(?=\s|$)")
HASHTAG_RE = re.compile(r"\w{1,12}\Z")
TICKER_RE = re.compile(r"[A-Z][A-Z0-9.]{0,5}\Z")
X_TEXT_LIMIT = 280
SHARE_LEAD_MIN_CHARS = 24
SHARE_LEAD_FORBIDDEN = re.compile(r"https?://|(?:^|\s)#[^\s]+|【(?:続報｜)?未発生新聞】")
SHARE_LEAD_LABEL = re.compile(r"^(?:要約|概要|説明|リンク|記事|見出し)\s*[:：]")


GENERIC_X_LABELS = {
    "AI", "SNS", "アプリ", "イベント", "お金", "ニュース", "メディア",
    "仕事", "働き方", "家族", "技術", "教育", "暮らし", "文化", "旅行",
    "生活", "研究", "社会", "経済",
}
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

    Prefer the exact words editors verified people would search on X:
    x_tickers and explicit x_tags. A raw trend_observation.term is deliberately
    not promoted to a hashtag: "what is trending" and "what readers search for
    to find this article" are different editorial decisions. Only after explicit
    discovery terms do we infer a small set of well-known slang from the public
    title/summary only. Site `tags` and `satire_tags` never feed X discovery.
    Broad site categories such as SNS or アプリ are never used merely to fill the post.
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

    fields = [article.get("title"), article.get("summary")]
    searchable = "\n".join(value for value in fields if isinstance(value, str))

    for needles, label in SLANG_RULES:
        if any(needle in searchable for needle in needles):
            add(label)
            if len(result) >= maximum:
                return result

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


def x_share_lead(article: dict, suffix: str) -> str | None:
    """Use an editor-written reader entry only when it is safe as standalone X copy."""
    raw = article.get("x_share_lead")
    if not isinstance(raw, str):
        return None
    lines = [
        re.sub(r"\s+", " ", reader_copy(line).strip())
        for line in raw.splitlines()
    ]
    cleaned = "\n".join(line for line in lines if line).strip()
    if len(cleaned) < SHARE_LEAD_MIN_CHARS:
        return None
    if SHARE_LEAD_FORBIDDEN.search(cleaned) or SHARE_LEAD_LABEL.match(cleaned):
        return None
    if x_weighted_length(cleaned + suffix) > X_TEXT_LIMIT:
        return None
    return cleaned


def reader_share_text(article: dict, url: str) -> str:
    """A reader-editable draft: one natural entry or headline, then discovery tags.

    Reserve the complete link and brand before fitting copy. Never cut an
    editor-written lead mid-thought or append a clipped summary to a headline.
    """
    tags = ["#未発生新聞"]
    tags.extend(tag for tag in article_hashtags(article) if tag not in tags)
    ending = "\n\n" + " ".join(tags)
    suffix = ending + "\n\n" + url
    lead = x_share_lead(article, suffix)
    if lead is None:
        title = " ".join(reader_copy(article["title"]).split())
        if not title:
            raise ValueError("Empty article title")
        prefix = "【続報】\n" if article.get("followup_of") else ""
        lead = prefix + truncate_x_text(
            title, max(0, X_TEXT_LIMIT - x_weighted_length(prefix + suffix)))
    text = lead + ending
    if x_weighted_length(text + "\n\n" + url) > X_TEXT_LIMIT:
        raise ValueError("Reader X draft exceeds the conservative weighted limit")
    return text
