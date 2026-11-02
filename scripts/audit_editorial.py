#!/usr/bin/env python3
"""Read-only editorial freshness audit. Warnings never block daily publication.

This is a consistency signal, not a judgement that an article is funny.
No API calls, extra services, paid resources, or mutation of article data.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")
FRESHNESS_HOURS = 12
WINDOW_HOURS = 48
AI_PATTERN = re.compile(r"(?<![A-Za-z])AI(?![A-Za-z])|ロボット|人工知能|生成ＡＩ|生成AI", re.IGNORECASE)

# Soft editorial signals. These flag likely repetition to review during the
# NEXT publication slot; they never suppress, reject, alter or postpone a post.
REVIEW_LIMIT = 10
MIN_STORY_CHARS = 180
NEAR_REPEAT_RATIO = 0.86
TOPIC_PATTERNS = {
    "AIが人間の役割を奪う": re.compile(r"(?<![A-Za-z])AI(?![A-Za-z])|ロボット|自動配分|自動判断", re.I),
    "確認・認証・手続きの逆効果": re.compile(r"確認|認証|登録|手続き|申請|判定|承認|拒否|制度"),
    "職場・会議": re.compile(r"会議|役員|部長|管理職|上司|職場"),
    "推し活・ファン行動": re.compile(r"推し|コラボ|ファン|アイドル|考察|応援"),
}


def _last_line(article: dict) -> str:
    """Only the final actual sentence, stripped of punctuation and spaces."""
    paras = article.get("paragraphs")
    if not isinstance(paras, list) or not paras or not isinstance(paras[-1], str):
        return ""
    normalized = unicodedata.normalize("NFKC", paras[-1])
    parts = [piece.strip() for piece in re.split(r"[。!?！？]+", normalized) if piece.strip()]
    if not parts:
        return ""
    return re.sub(r"[\s「」『』、，,（）()]+", "", parts[-1])


def _review_recent_content(recent: list[dict]) -> list[tuple[str, str, str]]:
    """Advisory clues for editors, not a subjective publishing gate."""
    latest = sorted(
        recent,
        key=lambda item: item.get("published_at", ""),
        reverse=True
    )[:REVIEW_LIMIT]
    notices: list[tuple[str, str, str]] = []
    used_sources: dict[str, str] = {}
    finales: list[tuple[str, str]] = []
    for article in latest:
        slug = str(article.get("slug", "unknown"))
        paras = article.get("paragraphs")
        prose = "".join(x for x in paras if isinstance(x, str)) if isinstance(paras, list) else ""
        if len(prose.strip()) < MIN_STORY_CHARS:
            notices.append(("THIN_STORY", slug,
                            "本文が短く、架空の変化・具体的な場面・オチを描き切れているか確認"))
        source = article.get("source")
        url = source.get("url", "") if isinstance(source, dict) else ""
        if isinstance(url, str) and url.startswith("https://"):
            if url in used_sources:
                notices.append(("REUSED_SOURCE", slug,
                                f"直近の記事 {used_sources[url]} と同一の元URLです。新しい題材とオチか確認"))
            else:
                used_sources[url] = slug
        final = _last_line(article)
        if len(final) >= 24:
            for previous_slug, previous in finales:
                if abs(len(final) - len(previous)) / max(len(final), len(previous)) > .22:
                    continue
                if SequenceMatcher(None, final, previous, autojunk=False).ratio() >= NEAR_REPEAT_RATIO:
                    notices.append(("REPEATED_PUNCHLINE", slug,
                                    f"締めの一文が {previous_slug} と酷似。別の結末を検討"))
                    break
            finales.append((slug, final))

    # Frequent daily posting makes adjacent repetition more important than
    # an all-time average. Use a small sample and moderate, explicit thresholds.
    newest_six = latest[:6]
    if len(newest_six) == 6:
        categories = Counter(x.get("category", "") for x in newest_six)
        common_category, amount = categories.most_common(1)[0]
        if common_category and amount >= 4:
            notices.append(("RECENT_CATEGORY_RUN", "-",
                            f"直近6本で{common_category}が{amount}本。次枠で別ジャンルを探してみてください"))
        mechanisms = Counter()
        for item in newest_six:
            title_and_dek = str(item.get("title", "")) + " " + str(item.get("summary", ""))
            for name, pattern in TOPIC_PATTERNS.items():
                if pattern.search(title_and_dek):
                    mechanisms[name] += 1
        for name, count in mechanisms.items():
            if count >= 4:
                notices.append(("SAME_MECHANISM_RUN", "-",
                                f"直近6本のうち「{name}」型が{count}本。別の視点・笑いの構造を試してください"))
    return notices


def audit(articles: list[dict], now: datetime) -> list[tuple[str, str, str]]:
    """Return (code, slug, message); never invent freshness when dates are absent."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    cutoff = now.astimezone(JST) - timedelta(hours=WINDOW_HOURS)
    warnings: list[tuple[str, str, str]] = []
    recent: list[dict] = []
    for a in articles:
        slug = a.get("slug", "unknown")
        if not a.get("published_at"):
            warnings.append(("MISSING_PUBLICATION", slug, "本紙の現実掲載時刻が未記録。元ネタ時刻と混同しないでください"))
            continue
        try:
            published = datetime.fromisoformat(a["published_at"])
            source = datetime.fromisoformat(a["source"]["published_at"])
        except (KeyError, TypeError, ValueError):
            warnings.append(("INVALID_DATE", slug, "元ネタまたは本紙の日時を解析できません"))
            continue
        if published.tzinfo is None or source.tzinfo is None:
            warnings.append(("MISSING_TZ", slug, "日時にはタイムゾーンが必要です"))
            continue
        published = published.astimezone(JST)
        source = source.astimezone(JST)
        if published < cutoff or published > now.astimezone(JST) + timedelta(minutes=10):
            if published > now.astimezone(JST) + timedelta(minutes=10):
                warnings.append(("FUTURE_PUBLISHED", slug, "本紙の掲載時刻が現実の未来になっています"))
            continue
        recent.append(a)
        lag_hours = (published - source).total_seconds() / 3600
        if lag_hours < -0.1:
            warnings.append(("SOURCE_IN_FUTURE", slug, "元ネタが本紙掲載時刻より後に発表されています"))
        elif lag_hours > FRESHNESS_HOURS:
            warnings.append(("STALE_SOURCE", slug,
                f"本紙掲載時の元ネタが {lag_hours:.1f} 時間前。推奨上限 {FRESHNESS_HOURS} 時間を超過"))
        if not a["source"].get("url", "").startswith("https://"):
            warnings.append(("INVALID_SOURCE_URL", slug, "元ネタのHTTPSリンクを確認してください"))

    if len(recent) >= 6:
        counts = Counter(a.get("category") for a in recent)
        primary, number = counts.most_common(1)[0]
        if number / len(recent) >= 0.6:
            warnings.append(("CATEGORY_BIAS", "-", f"最近{len(recent)}本のうち{primary}が{number}本。ジャンルの偏りを確認"))
        themed = sum(bool(AI_PATTERN.search(a.get("title", ""))) for a in recent)
        if themed / len(recent) >= 0.6:
            warnings.append(("AI_TOPIC_BIAS", "-", f"最近{len(recent)}本のうちAI・ロボット関連見出しが{themed}本。題材の幅を確認"))
    warnings.extend(_review_recent_content(recent))
    return warnings


def main() -> None:
    articles = json.loads((ROOT / "content/articles.json").read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    warnings = audit(articles, now)
    for code, slug, message in warnings:
        # GitHub annotation; this never fails the publish/validation job.
        print(f"::warning title=Editorial {code} ({slug})::{message}")
    print(f"Editorial audit: {len(articles)} articles, {len(warnings)} warning(s);"
          " advisory only. Never reject or defer a scheduled publication for these heuristics.")


if __name__ == "__main__":
    main()
