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

try:
    from scripts.editorial_mix import EDITORIAL_PAYOFFS, PAYOFF_LABELS
except ModuleNotFoundError:  # Direct execution: python3 scripts/audit_satire.py
    from editorial_mix import EDITORIAL_PAYOFFS, PAYOFF_LABELS

ROOT = Path(__file__).resolve().parents[1]
RECENT_LIMIT = 20
CLUSTER_LIMIT = 10
SIMILARITY_THRESHOLD = 0.68
SIGNATURE_SIMILARITY_THRESHOLD = 0.45

# These patterns intentionally detect only clearly named mechanical premises.
WORK_AI = re.compile(r"(?<![A-Za-z])AI(?![A-Za-z])|人工知能|ロボット|管理職|上司|稟議|研究所|役員|会議", re.I)
FORMAL_ANNOUNCEMENT = re.compile(r"発表|開始|導入|公表|明らかにした")
# Abstract clickbait can hide the actual comic event. Advisory only.
VAGUE_HEADLINE = re.compile(r"まさかの|衝撃(?:の|が)|ネット騒然|話題に|波紋(?:が|広がる)|新時代へ")
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
# Advisory only: the elapsed-time headline may hide a thin personal anecdote.
FIVE_YEAR_HOOK = re.compile(r"(?:5|五)年間?")
FIVE_YEAR_RUN_THRESHOLD = 3
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


def signature_text(article: dict) -> str:
    """Flatten the explicit satire mechanism metadata for advisory comparison."""
    signature = article.get("editorial_signature")
    if not isinstance(signature, dict):
        return ""
    parts = [
        signature.get(key)
        for key in ("target", "action", "outcome")
    ]
    if not all(isinstance(part, str) and part.strip() for part in parts):
        return ""
    return " ".join(part.strip() for part in parts)


def _tag_counts(articles: list[dict], key: str) -> Counter:
    return Counter(
        tag
        for article in articles
        for tag in (article.get(key) or [])
        if isinstance(tag, str) and tag
    )


def topic_tag_counts(articles: list[dict]) -> Counter:
    """Durable subject labels used for topic pages and discovery."""
    return _tag_counts(articles, "tags")


def satire_tag_counts(articles: list[dict]) -> Counter:
    """Playful editorial lenses may stay article-specific without SEO pressure."""
    return _tag_counts(articles, "satire_tags")


def editorial_tag_counts(articles: list[dict]) -> Counter:
    """Backward-compatible combined reader-facing label count."""
    return topic_tag_counts(articles) + satire_tag_counts(articles)


def reusable_topic_note(articles: list[dict]) -> str:
    topics = topic_tag_counts(articles)
    satire = satire_tag_counts(articles)
    if not topics and not satire:
        return ""
    established = [(tag, count) for tag, count in topics.most_common() if count >= 3][:6]
    emerging = sorted(
        ((tag, count) for tag, count in topics.items() if count == 2),
        key=lambda pair: pair[0],
    )[:6]
    reusable_satire = [
        (tag, count) for tag, count in satire.most_common() if count >= 3
    ][:6]
    parts = []
    if established:
        parts.append("育っているトピック: " + " / ".join(
            f"{tag}({count})" for tag, count in established
        ))
    if emerging:
        parts.append("2本あるトピック: " + " / ".join(tag for tag, _ in emerging))
    if reusable_satire:
        parts.append("繰り返し使える風刺タグ: " + " / ".join(
            f"{tag}({count})" for tag, count in reusable_satire
        ))
    return "。".join(parts)


def payoff_rotation_note(articles: list[dict]) -> str:
    """Summarize recent ending shapes so the next slot can compare a different one."""
    sample = [
        article for article in latest(articles)[:SHORT_RUN]
        if article.get("editorial_payoff") in EDITORIAL_PAYOFFS
    ]
    if not sample:
        return "着地タイプはまだ記録なし。企画に合う結末を選び、次回から同じ型の連打を見える化する。"

    recent = [article["editorial_payoff"] for article in sample]
    counts = Counter(recent)
    shown = " → ".join(PAYOFF_LABELS.get(payoff, payoff) for payoff in recent[:4])
    last = recent[0]
    alternatives = [payoff for payoff in EDITORIAL_PAYOFFS if payoff != last]
    preferred = min(
        alternatives,
        key=lambda payoff: (counts.get(payoff, 0), EDITORIAL_PAYOFFS.index(payoff)),
    )
    return (
        f"最近の着地: {shown}。次枠は「{PAYOFF_LABELS.get(preferred, preferred)}」型も "
        "候補比較し、本文をその型へ無理に合わせない。"
    )


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
        if VAGUE_HEADLINE.search(title):
            warnings.append((
                "VAGUE_HEADLINE", slug,
                "抽象的な煽り語より、誰が何をして何が逆効果になったかを見出しに出す",
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
            signature_similarity = near_duplicate(signature_text(a), signature_text(b))
            if signature_similarity >= SIGNATURE_SIMILARITY_THRESHOLD:
                warnings.append((
                    "SIMILAR_EDITORIAL_SIGNATURE", a.get("slug", "?"),
                    f"{b.get('slug')} と風刺の的・行動・逆効果が {signature_similarity:.0%} 近い。"
                    "言い換えではなく、別の行動か別の目に見える結果を検討",
                ))

    counts = topic_tag_counts(articles)
    if len(articles) >= 10 and counts:
        one_off = sum(count == 1 for count in counts.values())
        # One-off satire lenses are intentionally expressive; only durable
        # topic labels participate in fragmentation warnings. Allow a healthy
        # long tail before warning so niche subjects are not forced into SEO tags.
        threshold = max(15, (len(counts) * 3 + 4) // 5)
        if one_off >= threshold:
            warnings.append((
                "TOPIC_FRAGMENTATION", "-",
                f"記事{len(articles)}本に対してトピックタグ{len(counts)}種類、"
                f"うち1回だけが{one_off}種類。"
                "新作の主題に合う既存トピックがあれば同じ表記を再利用し、近い意味の新タグを増やさない。"
                "風刺タグは言い味の軸なので、記事固有でも無理に統合しない",
            ))

    newest = recent[:CLUSTER_LIMIT]
    short_run = recent[:SHORT_RUN]
    if len(short_run) == SHORT_RUN:
        # A check relevant to the *next* editorial slot. Do not block this one.
        five_year_count = sum(bool(FIVE_YEAR_HOOK.search(
            str(a.get("title", ""))
        )) for a in short_run)
        if five_year_count >= FIVE_YEAR_RUN_THRESHOLD:
            warnings.append((
                "FIVE_YEAR_HEADLINE_RUN", "-",
                f"直近{SHORT_RUN}本で5年の経過を見出しに使う記事が{five_year_count}本。"
                "期間を長くしただけの個人の失敗談になっていないか確認。"
                "世の中・商売・文化の変化を主役にした別案と比べる",
            ))
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

        tracked_payoffs = [
            item.get("editorial_payoff")
            for item in short_run
            if item.get("editorial_payoff") in EDITORIAL_PAYOFFS
        ]
        if len(tracked_payoffs) >= 3 and len(set(tracked_payoffs[:3])) == 1:
            repeated = tracked_payoffs[0]
            warnings.append((
                "PAYOFF_RUN", "-",
                f"直近の記録済み3本が着地「{PAYOFF_LABELS.get(repeated, repeated)}」で連続。"
                "次枠は別の着地も候補比較し、同じ読後感の連打を避ける",
            ))
        if len(tracked_payoffs) >= 5:
            dominant, count = Counter(tracked_payoffs).most_common(1)[0]
            if count >= 4:
                warnings.append((
                    "PAYOFF_CLUSTER", "-",
                    f"直近の記録済み{len(tracked_payoffs)}本中{count}本が着地"
                    f"「{PAYOFF_LABELS.get(dominant, dominant)}」。"
                    "題材だけでなく結末の形も回転させる",
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
        return ["掲載済み記事なし。検証済み出典と創作を分け、現在の社会・商売・生活の変化から2031年の出来事を考える。"]
    sample = recent[:SHORT_RUN]
    labels = {code for code, _, _ in analyze(articles)}
    lines = [f"直近{len(sample)}本から次の1本を考える（機械的助言であり、面白さの採点ではない）。"]
    lines.append("最近の見出し: " + " / ".join(str(a.get("title") or "無題")[:42] for a in sample[:3]))
    lines.append(payoff_rotation_note(articles))
    if "RECENT_CATEGORY_RUN" in labels:
        counts = Counter(str(a.get("category") or "") for a in sample)
        dominant, _ = counts.most_common(1)[0]
        lines.append(f"{dominant}が集中。確認できるニュース・公開投稿・生活や文化の話題から、別ジャンル・別の読者層も探す。")
    if "PROCEDURAL_PREMISE_LOOP" in labels:
        lines.append("確認・申請・認証・拒否の逆効果が続く。誤解、意外な成功、言葉遊び、善意の空回りなど別の笑いを探す。")
    if "FIVE_YEAR_HEADLINE_RUN" in labels:
        lines.append("5年経過の見出しが続く。個人が耐えたり損したりするだけの小話ではなく、2031年に何が変わったか分かる具体的な出来事を先に考える。期間の長さ自体をオチにしない。")
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
    if "PAYOFF_RUN" in labels or "PAYOFF_CLUSTER" in labels:
        lines.append("飽き対策: 題材を変えるだけでなく、着地タイプも変える候補を1案は混ぜる。同じ型が最適なら無理に捨てず、比較した上で採用する。")
    if "SIMILAR_HEADLINE" in labels or "REUSED_SOURCE" in labels:
        lines.append("似た見出し・出典の使い回しを点検。別の実在話題と固有のオチを探す。")
    if "VAGUE_HEADLINE" in labels:
        lines.append("抽象煽りで引っ張らず、主語・具体的な行動・見える逆効果をタイトルに置く。")
    if "SIMILAR_EDITORIAL_SIGNATURE" in labels:
        lines.append("風刺の的・行動・逆効果が近い記事がある。単語だけ変えず、主役の行動か目に見える結果を別の仕組みにする。")
    if "TOPIC_FRAGMENTATION" in labels:
        note = reusable_topic_note(articles)
        lines.append(
            "トピックタグが分散気味。新しい記事に本当に合う既存トピックがあれば同じ表記を優先する。"
            "3本目を作るために題材や本文を曲げず、新しい主題なら新タグでよい。"
            "風刺タグは記事固有の言い味を残してよい。"
            + ((" " + note + "。") if note else "")
        )
    if len(lines) == 3:
        lines.append("目立つ定型警告はなし。新案は直近の見出しと末文を読み比べ、展開の使い回しを防ぐ。")
    lines.append("掲載前: 候補3案のうち最低1案は、今の仕組み・商売・集団の変化が2031年のニュースになる案。個人が5年間我慢して損をするだけなら作り直す。各案に「今ある変化／周囲に起きる結果／本人の欲・圧力／普通なら取る選択／選ばない理由」を置く。普通の選択を捨てる理由が弱い案は見出しを磨く前に落とす。見出し3案を比べ、誰が何をして何がおかしいか最短で伝わる案を選ぶ。冒頭2文→全文→末文を読み、原因→行動→結果がつながらなければ説明を足さず設定を減らす。")
    lines.append("意味が通る、珍しい、会話が自然なだけでは合格にしない。まず「普通の人ならここで何をする？」を一つ置き、次に「なぜこの人物はそれを選ばない？」を反証する。得・評価・体面・同調圧力・損切りしづらさなどの具体的な摩擦が公開文に見えず、作者がオチのためだけに不自然な行動をさせているなら候補を選び直す。behavior_basis の evidence_quote は公開文の実際の一節にする。誇張は大胆でよいが、人間の動機まで消さない。")
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

