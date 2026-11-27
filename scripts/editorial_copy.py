"""Present legacy satire copy without redundant in-story fiction qualifiers.

Published article data is never mutated: media fingerprints and existing visual
review approvals must remain tied to the exact original source text. Future
articles should be written cleanly according to docs/EDITORIAL_LOOP.md.
"""
import re

_SOURCE_DISCLAIMER_SENTENCE = re.compile(
    r"(?:創作|架空)(?:であり|である|で、|。|$)|本紙(?:独自)?の設定|架空の出来事で"
    r"|事実はない|報じたものではない|調査結果ではない"
    r"|(?:実在|2031年|架空).*(?:ではない|報じていない|発表されていない|無関係|着想|の話|実在しない)。?$"
)
_SOURCE_EDIT_NOTE = re.compile(
    r"日付代表値|改稿時に.*出典日付を訂正"
    r"|架空.*(?:帰属させない|ことにはしない)|実在.*将来の行為を述べない"
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
    # Source notes can name real works and future plans. Story-copy cleanup
    # would change their meaning (e.g.「共同創作の展示」).
    kept = [part for part in sentences if part.strip()
            and not _SOURCE_EDIT_NOTE.search(part) and not _SOURCE_DISCLAIMER_SENTENCE.search(part)]
    return re.sub(r"\s+", " ", "".join(kept)).strip()
