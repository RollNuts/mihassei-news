"""Present legacy satire copy without redundant in-story fiction qualifiers.

Published article data is never mutated: media fingerprints and existing visual
review approvals must remain tied to the exact original source text. Future
articles should be written cleanly according to docs/EDITORIAL_LOOP.md.
"""
import re

_SOURCE_DISCLAIMER_SENTENCE = re.compile(
    r"(?:本紙|創作|2031年|実在(?:の|し)|事実はない|報じたものではない|調査結果ではない)"
)
_STAGE_YEAR = re.compile(r"(^|[。！？」])\s*2031年、")


def reader_copy(text: str) -> str:
    """Remove only formulaic labels; keep plot, proper names and attribution."""
    if not isinstance(text, str):
        raise TypeError("Article display text must be a string")
    cleaned = text.replace("架空の", "").replace("創作の", "")
    return _STAGE_YEAR.sub(r"\1", cleaned)


def source_reader_copy(text: str) -> str:
    """Keep real-world inspiration sentences and drop per-article fiction disclaimers."""
    if not isinstance(text, str):
        raise TypeError("Article source display text must be a string")
    sentences = re.findall(r"[^。]*(?:。|$)", text)
    kept = [part for part in sentences
            if part.strip() and not _SOURCE_DISCLAIMER_SENTENCE.search(part)]
    cleaned = reader_copy("".join(kept))
    return re.sub(r"\s+", " ", cleaned).strip()
