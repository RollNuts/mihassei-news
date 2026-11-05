#!/usr/bin/env python3
"""Read-only editorial freshness audit. Warnings never block daily publication.

This is a consistency signal, not a judgement that an article is funny.
No API calls, extra services, paid resources, or mutation of article data.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")
FRESHNESS_HOURS = 12
# Dated observations can remain relevant beyond the breaking-news window.
# Missing or unknown kinds keep the existing news-freshness warning.
FRESHNESS_EXEMPT_KINDS = ("公開投稿", "生活・文化")
WINDOW_HOURS = 48
AI_PATTERN = re.compile(r"(?<![A-Za-z])AI(?![A-Za-z])|ロボット|人工知能|生成ＡＩ|生成AI", re.IGNORECASE)
CRISIS_PATTERN = re.compile(
    r"地震|津波|台風|豪雨|洪水|土砂災害|山火事|戦争|武力衝突|空爆|侵攻|砲撃|"
    r"ミサイル攻撃|テロ|大量死傷|死者|死亡者|負傷者|被災者|避難者|人質"
)
CONTEXT_DEPENDENT_OPENING_PATTERN = re.compile(
    r"^(?:これ(?:を|で|が|は)?|それ(?:を|で|が|は)?|この件|その件|例の|あの件|"
    r"このニュース|この話|こうした(?:動き|流れ|結果|状況)|これを受け|その結果)"
)
MAX_FOLLOWUP_CHAIN_ARTICLES = 3
BACKGROUND_ONLY_FIRST_SENTENCE_CHARS = 55


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
        elif (lag_hours > FRESHNESS_HOURS
              and a["source"].get("kind") not in FRESHNESS_EXEMPT_KINDS):
            warnings.append(("STALE_SOURCE", slug,
                f"本紙掲載時の元ネタが {lag_hours:.1f} 時間前。推奨上限 {FRESHNESS_HOURS} 時間を超過"))
        if not a["source"].get("url", "").startswith("https://"):
            warnings.append(("INVALID_SOURCE_URL", slug, "元ネタのHTTPSリンクを確認してください"))

    by_slug = {a.get("slug"): a for a in articles if isinstance(a.get("slug"), str)}
    for a in recent:
        slug = a.get("slug", "unknown")
        searchable = " ".join(
            str(value or "") for value in (
                a.get("title"), a.get("summary"), a.get("source", {}).get("topic")
            )
        )
        if CRISIS_PATTERN.search(searchable):
            warnings.append((
                "CRISIS_TOPIC_REVIEW",
                slug,
                "災害・戦争・テロ・大量死傷事故などの可能性。急性期の直接風刺・被害者を笑いにする表現・惨事のクリック誘導を避け、EDITORIAL_LOOPの危機ニュース規則を再確認",
            ))

        paragraph_list = a.get("paragraphs")
        first_paragraph = paragraph_list[0].strip() if isinstance(paragraph_list, list) and paragraph_list and isinstance(paragraph_list[0], str) else ""
        for field_name, opening in (("見出し", str(a.get("title") or "").strip()), ("summary", str(a.get("summary") or "").strip()), ("本文冒頭", first_paragraph)):
            if opening and CONTEXT_DEPENDENT_OPENING_PATTERN.search(opening):
                warnings.append((
                    "CONTEXT_DEPENDENT_OPENING",
                    slug,
                    f"{field_name}が前の記事・SNS・元ニュースを見ている前提の書き出しです。対象と出来事から単体で書き始めてください",
                ))

        # A long first sentence followed by another sentence is a useful
        # advisory signal that the story may be spending too long on background
        # or setup before the comic scene. Do not depend on words such as
        # "架空": reader-facing copy intentionally omits those formulaic labels.
        if first_paragraph:
            sentence_parts = [
                part.strip()
                for part in re.split(r"(?<=[。！？!?])", first_paragraph, maxsplit=1)
                if part.strip()
            ]
            first_sentence = sentence_parts[0] if sentence_parts else ""
            if (len(sentence_parts) > 1
                    and len(first_sentence) >= BACKGROUND_ONLY_FIRST_SENTENCE_CHARS):
                warnings.append((
                    "BACKGROUND_LOADED_OPENING",
                    slug,
                    f"本文1文目が{len(first_sentence)}字あり、場面や具体的行動に入るまでが重い可能性があります。"
                    "現実背景や設定説明を伸ばさず、冒頭2文の早い位置で人物・現場・具体的行動を見せてください",
                ))

        depth = 1
        cursor = a
        seen_chain = {slug}
        while cursor.get("followup_of") in by_slug:
            parent_slug = cursor["followup_of"]
            if parent_slug in seen_chain:
                break
            seen_chain.add(parent_slug)
            depth += 1
            cursor = by_slug[parent_slug]
        if depth == MAX_FOLLOWUP_CHAIN_ARTICLES:
            warnings.append((
                "FOLLOWUP_THREAD_CLOSED",
                slug,
                "この話題は初報＋続報2本の上限に到達。次の新展開を実質4本目として延命しないでください",
            ))

    if len(recent) >= 6:
        counts = Counter(a.get("category") for a in recent)
        primary, number = counts.most_common(1)[0]
        if number / len(recent) >= 0.6:
            warnings.append(("CATEGORY_BIAS", "-", f"最近{len(recent)}本のうち{primary}が{number}本。ジャンルの偏りを確認"))
        themed = sum(bool(AI_PATTERN.search(a.get("title", ""))) for a in recent)
        if themed / len(recent) >= 0.6:
            warnings.append(("AI_TOPIC_BIAS", "-", f"最近{len(recent)}本のうちAI・ロボット関連見出しが{themed}本。題材の幅を確認"))
    return warnings


def main() -> None:
    articles = json.loads((ROOT / "content/articles.json").read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    warnings = audit(articles, now)
    for code, slug, message in warnings:
        # GitHub annotation; this never fails the publish/validation job.
        print(f"::warning title=Editorial {code} ({slug})::{message}")
    print(f"Editorial audit: {len(articles)} articles, {len(warnings)} warning(s);"
          " advisory only, publishing not blocked.")


if __name__ == "__main__":
    main()
