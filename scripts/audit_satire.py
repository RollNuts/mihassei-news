#!/usr/bin/env python3
"""Free, read-only warning system for repetitive fictional-news satire.

All findings are heuristics, NOT an AI verdict on whether a joke is funny.
Never block the daily publishing quota; instead suggest a different premise.
No external services, model API, package installs, or reader tracking.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECENT_LIMIT = 20
CLUSTER_LIMIT = 10
SIMILARITY_THRESHOLD = 0.68

# These patterns intentionally detect only clearly named mechanical premises.
WORK_AI = re.compile(r"(?<![A-Za-z])AI(?![A-Za-z])|人工知能|ロボット|管理職|上司|稟議|研究所|役員|会議", re.I)
FORMAL_ANNOUNCEMENT = re.compile(r"発表|開始|導入|公表|明らかにした")
MECHANICAL_ENDINGS = re.compile(
    r"担当者|広報|謝罪|却下|不合格|新制度|管理職|手続き|承認|申請|通知"
)
# A short burst of similar premises can bore a first-time visitor even when
# the 20-story average looks diverse. A signal, never an automatic rejection.
PROCEDURAL_PREMISE = re.compile(
    r"確認|認証|登録|手続き|申請|判定|承認|規制|拒否|不採用|新制度"
)
SHORT_RUN = 6
PROCEDURAL_RUN_THRESHOLD = 4
CATEGORY_RUN_THRESHOLD = 5


def actual_published(article: dict) -> str:
    """Use the real site publication time, not the imaginary 2031 datetime."""
    return article.get("published_at") or article.get("source", {}).get("published_at", "")


def latest(articles: list[dict]) -> list[dict]:
    return sorted(articles, key=lambda a: (
        actual_published(a), a.get("source", {}).get("published_at", ""),
    ), reverse=True)[:RECENT_LIMIT]


def normalized(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text)).casefold()
    return "".join(ch for ch in text if unicodedata.category(ch)[0] in ("L", "N"))


def grams(text: str, size: int = 3) -> set[str]:
    clean = normalized(text)
    return {clean[i:i + size] for i in range(max(0, len(clean) - size + 1))}


def near_duplicate(left: str, right: str) -> float:
    """Jaccard similarity of character trigrams. Not semantic similarity."""
    a, b = grams(left), grams(right)
    if min(len(a), len(b)) < 7:
        return 0.0
    return len(a & b) / len(a | b)


def final_sentence(article: dict) -> str:
    paragraphs = article.get("paragraphs") or []
    if not paragraphs:
        return ""
    parts = [p.strip() for p in re.split(r"[。！？!?]", paragraphs[-1]) if p.strip()]
    return parts[-1] if parts else ""


def analyze(articles: list[dict]) -> list[tuple[str, str, str]]:
    """Warn about repetition but never reject or silently delete an article."""
    recent = latest(articles)
    warnings: list[tuple[str, str, str]] = []
    sources: dict[str, str] = {}
    for a in recent:
        slug = a.get("slug", "?")
        source = a.get("source", {}).get("url", "")
        if source:
            if source in sources:
                warnings.append((
                    "REUSED_SOURCE", slug,
                    f"同じ着想元URLが {sources[source]} と重複。別の切り口・実際の出典かを確認",
                ))
            sources[source] = slug
        title = a.get("title", "")
        if len(title) > 55:
            warnings.append((
                "VERY_LONG_HEADLINE", slug,
                f"見出し{len(title)}字。SNSの小画面でも何がおかしいか一目で分かるか確認",
            ))

    # Highlight strong surface-level repetition only. These are warnings;
    # wording can be similar even when the stories make different points.
    for i, a in enumerate(recent):
        for b in recent[i + 1:]:
            title_similarity = near_duplicate(a.get("title", ""), b.get("title", ""))
            if title_similarity >= SIMILARITY_THRESHOLD:
                warnings.append((
                    "SIMILAR_HEADLINE", a.get("slug", "?"),
                    f"{b.get('slug')} と見出しの3文字組一致が {title_similarity:.0%}。別の題材・具体的なオチを検討",
                ))
            left, right = final_sentence(a), final_sentence(b)
            ending_similarity = near_duplicate(left, right)
            if ending_similarity >= SIMILARITY_THRESHOLD:
                warnings.append((
                    "SIMILAR_PUNCHLINE", a.get("slug", "?"),
                    f"{b.get('slug')} と末文が {ending_similarity:.0%} 似ている。最後の一撃を差別化",
                ))

    newest = recent[:CLUSTER_LIMIT]
    short_run = recent[:SHORT_RUN]
    if len(short_run) == SHORT_RUN:
        # A check relevant to the *next* editorial slot. Do not block this one.
        categories = Counter(a.get("category", "") for a in short_run)
        dominant, count = categories.most_common(1)[0]
        if dominant and count >= CATEGORY_RUN_THRESHOLD:
            warnings.append((
                "RECENT_CATEGORY_RUN", "-",
                f"直近{SHORT_RUN}本で{dominant}が{count}本。次枠は異なるジャンル・読者層の題材を先に探す",
            ))
        procedural = sum(bool(PROCEDURAL_PREMISE.search(
            str(a.get("title", "")) + " " + str(a.get("summary", ""))
        )) for a in short_run)
        if procedural >= PROCEDURAL_RUN_THRESHOLD:
            warnings.append((
                "PROCEDURAL_PREMISE_LOOP", "-",
                f"直近{SHORT_RUN}本で確認・申請・認証などの逆効果が{procedural}本。"
                "人間の選択、文化、言葉遊び、意外な成功例など別の仕組みを試す",
            ))
    if len(newest) >= 6:
        work = [a for a in newest if WORK_AI.search(a.get("title", ""))]
        if len(work) >= max(5, (len(newest) * 3 + 4) // 5):
            warnings.append((
                "WORK_AI_CLUSTER", "-",
                f"直近{len(newest)}本のうち{len(work)}本がAI・仕事・会議の見出し。"
                "ネットミーム・推し活・食・恋愛・スポーツ・生活・炎上の仕組みを先に探索",
            ))
        # Five different articles that begin like a press release are usually
        # less entertaining than scenes, people and concrete mishaps.
        release_leads = [
            a for a in newest
            if FORMAL_ANNOUNCEMENT.search((a.get("paragraphs") or [""])[0][:115])
        ]
        if len(release_leads) >= max(6, (len(newest) * 7 + 9) // 10):
            warnings.append((
                "PRESS_RELEASE_OPENING_LOOP", "-",
                f"直近{len(newest)}本のうち{len(release_leads)}本が発表・開始・導入中心の書き出し。"
                "現場の珍事や会話から始まる記事も混ぜる",
            ))
        # A formulaic punchline is a warning signal, not proof of low quality.
        mechanical = [
            a for a in newest if MECHANICAL_ENDINGS.search(final_sentence(a))
        ]
        if len(mechanical) >= max(6, (len(newest) * 7 + 9) // 10):
            warnings.append((
                "FORMULA_PUNCHLINE_LOOP", "-",
                f"直近{len(newest)}本のうち{len(mechanical)}本が担当者・承認・謝罪等で締めている。"
                "言葉遊び、皮肉な成功、関係の逆転など別の結末も試す",
            ))
    return warnings


def main() -> None:
    articles = json.loads((ROOT / "content/articles.json").read_text(encoding="utf-8"))
    issues = analyze(articles)
    for code, slug, message in issues:
        print(f"::warning title=Satire {code} ({slug})::{message}")
    latest_titles = [a.get("title", "") for a in latest(articles)[:3]]
    print(f"Satire review: {len(articles)} articles; {len(issues)} advisory warning(s).")
    print("Newest real headlines:", " / ".join(latest_titles))
    print("These are heuristic signals, not a verdict on humor. Never block publishing.")


if __name__ == "__main__":
    main()
