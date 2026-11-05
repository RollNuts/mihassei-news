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
# Repeated boilerplate fictional datelines can push the actual comic scene back.
# The fictional date must stay in the article metadata; this checks prose only.
FORMULA_FUTURE_INTRO = re.compile(r"20\d{2}年[、，]\s*架空の")
MECHANICAL_ENDINGS = re.compile(
    r"担当者|広報|謝罪|却下|不合格|新制度|管理職|手続き|承認|申請|通知"
)
DIRECT_SPEECH_ENDING = re.compile(
    r"(?:"
    r"「[^」]{2,}」"
    r"|「[^」]{2,}」\s*と\s*"
    r"(?:答えた|言った|説明した|案内した|告げた|うなずいた|笑った|"
    r"書き込んだ|つぶやいた|繰り返した|話した|宣言した|認めた)"
    r")[。！？!?]?\s*$"
)
# A short burst of similar premises can bore a first-time visitor even when
# the 20-story average looks diverse. A signal, never an automatic rejection.
PROCEDURAL_PREMISE = re.compile(
    r"確認|認証|登録|手続き|申請|判定|承認|規制|拒否|不採用|新制度"
)
SHORT_RUN = 6
PROCEDURAL_RUN_THRESHOLD = 4
CATEGORY_RUN_THRESHOLD = 5
DIALOGUE_ENDING_THRESHOLD = 4


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


def editorial_tag_counts(articles: list[dict]) -> Counter:
    """Reader-facing topic/satire labels only; categories are a separate axis."""
    return Counter(
        tag
        for article in articles
        for tag in [*(article.get("tags") or []), *(article.get("satire_tags") or [])]
        if isinstance(tag, str) and tag
    )


def reusable_topic_note(articles: list[dict]) -> str:
    counts = editorial_tag_counts(articles)
    if not counts:
        return ""
    established = [(tag, count) for tag, count in counts.most_common() if count >= 3][:6]
    emerging = sorted(
        ((tag, count) for tag, count in counts.items() if count == 2),
        key=lambda pair: pair[0],
    )[:6]
    parts = []
    if established:
        parts.append("育っているタグ: " + " / ".join(f"{tag}({count})" for tag, count in established))
    if emerging:
        parts.append("2本あるタグ: " + " / ".join(tag for tag, _ in emerging))
    return "。".join(parts)


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

    counts = editorial_tag_counts(articles)
    if len(articles) >= 10 and counts:
        one_off = sum(count == 1 for count in counts.values())
        if one_off >= max(12, (len(counts) + 1) // 2):
            warnings.append((
                "TOPIC_FRAGMENTATION", "-",
                f"記事{len(articles)}本に対してタグ{len(counts)}種類、うち1回だけが{one_off}種類。"
                "新作の主題に合う既存タグがあれば同じ表記を再利用し、近い意味の新タグを増やさない。"
                "無関係なタグをSEO目的で付ける必要はない",
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
        future_intros = sum(
            bool(FORMULA_FUTURE_INTRO.search(str((item.get("paragraphs") or [""])[0])[:140]))
            for item in short_run
        )
        if future_intros >= 4:
            warnings.append((
                "FORMULA_FUTURE_INTRO_LOOP", "-",
                f"直近{SHORT_RUN}本で「2031年、架空の〜」型の書き出しが{future_intros}本。"
                "次枠は冒頭2文で人物・現場・原因を示す。台詞始まりは必須ではない",
            ))
        dialogue_endings = sum(
            bool(DIRECT_SPEECH_ENDING.search(
                str((item.get("paragraphs") or [""])[-1]).strip()
            ))
            for item in short_run
        )
        if dialogue_endings >= DIALOGUE_ENDING_THRESHOLD:
            warnings.append((
                "DIALOGUE_PUNCHLINE_LOOP", "-",
                f"直近{SHORT_RUN}本のうち{dialogue_endings}本が最後のセリフで締まっている。"
                "次枠は人物の行動、画面に残る物、皮肉な結果など、台詞以外の結末も比較する",
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
                "冒頭2文で出来事と原因が分かる書き出しも試す。会話への置換だけで済ませない",
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



def next_slot_brief(articles: list[dict]) -> list[str]:
    """Next-slot editorial notes from observed text patterns, never humor scores.

    No article filtering, network requests, new storage or publication blocking.
    """
    recent = latest(articles)
    if not recent:
        return ["掲載済み記事なし。検証済み出典と創作を分け、人物の具体的な行動から考える。"]
    sample = recent[:SHORT_RUN]
    labels = {code for code, _, _ in analyze(articles)}
    lines = [f"直近{len(sample)}本から次の1本を考える（機械的助言であり、面白さの採点ではない）。"]
    lines.append("最近の見出し: " + " / ".join(str(a.get("title") or "無題")[:42] for a in sample[:3]))
    if "RECENT_CATEGORY_RUN" in labels:
        counts = Counter(str(a.get("category") or "") for a in sample)
        dominant, _ = counts.most_common(1)[0]
        lines.append(f"{dominant}が集中。確認できるニュース・公開投稿・生活や文化の話題から、別ジャンル・別の読者層も探す。")
    if "PROCEDURAL_PREMISE_LOOP" in labels:
        lines.append("確認・申請・認証・拒否の逆効果が続く。誤解、意外な成功、言葉遊び、善意の空回りなど別の笑いを探す。")
    if "FORMULA_FUTURE_INTRO_LOOP" in labels:
        lines.append("「2031年、架空の〜」を連発しない。年代は日付欄に任せ、人物・現場・原因は冒頭2文で示す。社名などの架空設定はその後に必要な分だけ添える。台詞始まりは必須ではない。")
    if "WORK_AI_CLUSTER" in labels:
        lines.append("AI・仕事・会議に偏り気味。食・遊び・家族・スポーツ・趣味など身近な場面を探す。")
    if "PRESS_RELEASE_OPENING_LOOP" in labels:
        lines.append("企業の発表から始まる記事が続く。冒頭2文で誰がどこで何に困るかと、その原因を示す。会話も地の文も理解の早い方を選ぶ。")
    if "FORMULA_PUNCHLINE_LOOP" in labels or "SIMILAR_PUNCHLINE" in labels:
        lines.append("定型謝罪や担当者コメントの締めを避ける。既に示した原因から、その話に固有の行動・一言で閉じる。二度目の逆転は不要。本文の追加の笑いは既存の人物と前提から作る。")
    if "DIALOGUE_PUNCHLINE_LOOP" in labels:
        lines.append("最後の一言で落とす記事が続いている。次は、人物が実際にしてしまう行動、残った物、数字や光景そのものがオチになる案も比べる。")
    if "SIMILAR_HEADLINE" in labels or "REUSED_SOURCE" in labels:
        lines.append("似た見出し・出典の使い回しを点検。別の実在話題と固有のオチを探す。")
    if "TOPIC_FRAGMENTATION" in labels:
        note = reusable_topic_note(articles)
        lines.append(
            "タグが分散気味。新しい記事に本当に合う既存タグがあれば同じ表記を優先する。"
            "3本目を作るために題材や本文を曲げず、新しい主題なら新タグでよい。"
            + ((" " + note + "。") if note else "")
        )
    if len(lines) == 2:
        lines.append("目立つ定型警告はなし。新案は直近の見出しと末文を読み比べ、展開の使い回しを防ぐ。")
    lines.append("掲載前: 異なる仕組みの見出し2案を本文なしで比べ、状況だけでなく何がおかしいかが伝わる案を選ぶ。冒頭2文→全文→末文を読み、本文に見出しを繰り返さない行動・結果・一言の笑いがあるか確かめる。原因→人物の行動→結果がつながらなければ、説明を継ぎ足さず設定を減らす。")
    lines.append("意味が通る、珍しい、会話が自然なだけでは合格にしない。企画そのものが弱いなら同じ枠で候補を選び直す。誇張は大胆でよいが変える前提は一つ。全記事を同じ逆転型にしない。")
    lines.append("出典・公開日時・創作の区別を守り、反応数は捏造しない。警告だけで発行を止めない。")
    return lines


def main() -> None:
    articles = json.loads((ROOT / "content/articles.json").read_text(encoding="utf-8"))
    issues = analyze(articles)
    for code, slug, message in issues:
        print(f"::warning title=Satire {code} ({slug})::{message}")
    latest_titles = [a.get("title", "") for a in latest(articles)[:3]]
    print(f"Satire review: {len(articles)} articles; {len(issues)} advisory warning(s).")
    print("Newest real headlines:", " / ".join(latest_titles))
    for note in next_slot_brief(articles):
        print("NEXT SLOT (advisory):", note)
    print("These are heuristic signals, not a verdict on humor. Never block publishing.")


if __name__ == "__main__":
    main()
