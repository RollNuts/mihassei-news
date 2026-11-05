"""Present legacy satire copy without redundant in-story fiction qualifiers.

Published article data is never mutated: media fingerprints and existing visual
review approvals must remain tied to the exact original source text. Future
articles should be written cleanly according to docs/EDITORIAL_LOOP.md.
"""
import re

_SOURCE_BOILERPLATE_SENTENCE = re.compile(
    r"[^。]*(?:本紙独自の創作|本紙独自の設定)[^。]*(?:。|$)"
)
_STAGE_YEAR = re.compile(r"(^|[。！？」])\\s*2031年、")


def reader_copy(text: str) -> str:
    """Remove only formulaic labels; keep plot, proper names and attribution."""
    if not isinstance(text, str):
        raise TypeError("Article display text must be a string")
    cleaned = text.replace("架空の", "").replace("創作の", "")
    return _STAGE_YEAR.sub(r"\\1", cleaned)


def source_reader_copy(text: str) -> str:
    """Keep the real-world inspiration while dropping per-article fiction boilerplate."""
    cleaned = reader_copy(text)
    cleaned = _SOURCE_BOILERPLATE_SENTENCE.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()
