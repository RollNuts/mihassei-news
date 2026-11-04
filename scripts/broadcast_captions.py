"""Shared, read-only validation of short captions for the current article."""
from __future__ import annotations

import json
from pathlib import Path

try:
    from .generated_photos import article_fingerprint
except ImportError:
    from generated_photos import article_fingerprint

ROOT = Path(__file__).resolve().parents[1]


def story_cues(article: dict, *, root: Path = ROOT) -> list[dict]:
    """Return all three current editorial cues, or none; never invent captions."""
    path = root / "content/broadcasts.json"
    if path.is_symlink():
        return []
    try:
        source = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if (not isinstance(source, dict) or type(source.get("version")) is not int
            or source["version"] != 1):
        return []
    stories = source.get("stories")
    item = stories.get(article["slug"]) if isinstance(stories, dict) else None
    if not isinstance(item, dict) or item.get("article_sha256") != article_fingerprint(article):
        return []
    cues = item.get("cues")
    if not isinstance(cues, list) or len(cues) != 3:
        return []
    for index, cue in enumerate(cues):
        if (not isinstance(cue, dict) or type(cue.get("at")) is not int
                or cue["at"] != index * 4
                or not isinstance(cue.get("label"), str) or not 1 <= len(cue["label"]) <= 12
                or not cue["label"].strip()
                or not isinstance(cue.get("text"), str) or not 1 <= len(cue["text"]) <= 40
                or not cue["text"].strip()):
            return []
    return cues
