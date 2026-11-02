"""Choose relevant *published* follow-up reading without APIs or paid embeddings.

Suggestions are deterministic and transparent: explicit subject patterns and
conservative title-keyword overlap, followed by real publication recency.
This is navigation, NOT a claim that readers liked a story or that it is news.
"""
from __future__ import annotations

from functools import lru_cache
import re
import unicodedata

TOPICS = (
    ("ai", r"(?<![a-z])ai(?![a-z])|人工知能|自動化|ロボット|機械学習"),
    ("work", r"管理職|上司|社員|職場|仕事|会議|人事|労働|工場|就職|働き方|研究所"),
    ("safety", r"迷惑電話|詐欺|電話|情報漏洩|セキュリティ|個人情報|防犯|被害|保護"),
    ("weather", r"天気|天候|低気圧|気圧|気象|台風|大雨|気温|猛暑|予報"),
    ("food", r"土産|観光|地域|地元|産地|食品|食材|名物|食べ|飲食"),
    ("baseball", r"野球|球団|選手|試合|ファン|プロスポーツ|応援|契約延長"),
    ("entertainment", r"ドラマ|脚本|考察|映画|音楽|芸能|番組|視聴者|最終回"),
    ("economy", r"物価|株価|金利|経済|賃金|契約|市場|金融|銀行|投資"),
    ("politics", r"政府|政策|議会|選挙|大臣|首相|行政|条例|法案|新制度"),
)
COMPILED_TOPICS = tuple((key, re.compile(pattern, re.I)) for key, pattern in TOPICS)
COMMON_PHRASES = frozenset(("新制度", "サービス", "会社", "企業", "開始", "発表",
                            "業務", "利用", "導入", "対応", "結果", "研究"))


@lru_cache(maxsize=2048)
def _profile(title: str, summary: str, topic: str):
    text = unicodedata.normalize("NFKC", " ".join((title, summary, topic))).lower()
    subject_tags = frozenset(tag for tag, pattern in COMPILED_TOPICS if pattern.search(text))
    normalized_title = unicodedata.normalize("NFKC", title).lower()
    phrases = frozenset(
        word for word in re.findall(r"[一-龯々]{2,}|[ァ-ヶー]{3,}|[a-z0-9]{3,}", normalized_title)
        if word not in COMMON_PHRASES
    )
    return subject_tags, phrases


def _signals(article: dict):
    return _profile(
        str(article.get("title", "")),
        str(article.get("summary", "")),
        str(article.get("source", {}).get("topic", "")),
    )


def topic_score(article: dict, candidate: dict) -> int:
    """Strong editorial motifs first, then a small same-category preference."""
    topics, phrases = _signals(article)
    other_topics, other_phrases = _signals(candidate)
    shared_topics = topics & other_topics
    shared_phrases = phrases & other_phrases
    return (
        len(shared_topics) * 10
        + min(2, len(shared_phrases)) * 5
        + (2 if article.get("category") == candidate.get("category") else 0)
    )


def suggested_articles(article: dict, articles: list[dict], limit: int = 4) -> list[dict]:
    """Unique, real articles ranked by subject and real publishing recency.

    Does not invent popularity, recirculate the current story, or use the
    five-years-ahead fictional dateline as if it were a real published time.
    """
    if limit <= 0:
        return []
    seen = {article["slug"]}
    candidates = []
    for item in articles:
        slug = item["slug"]
        if slug in seen:
            continue
        seen.add(slug)
        candidates.append(item)

    return sorted(candidates, key=lambda item: (
        topic_score(article, item),
        item.get("published_at") or item["source"]["published_at"],
        item["source"]["published_at"],
        item["slug"],
    ), reverse=True)[:limit]
