"""Present legacy satire copy without redundant in-story fiction qualifiers.

Published article data is never mutated: media fingerprints and existing visual
review approvals must remain tied to the exact original source text. Future
articles should be written cleanly according to docs/EDITORIAL_LOOP.md.
"""

def reader_copy(text: str) -> str:
    """Remove only formulaic labels; keep plot, proper names and attribution."""
    if not isinstance(text, str):
        raise TypeError("Article display text must be a string")
    return text.replace("架空の", "").replace("創作の", "")
