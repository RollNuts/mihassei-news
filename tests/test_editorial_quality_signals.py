"""Editorial quality warnings assist the next edition; they never block it."""
from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from scripts.audit_editorial import audit, _last_line, _review_recent_content

JST = ZoneInfo("Asia/Tokyo")
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=JST)


def sample(i, *, title=None, category="社会", source_url=None, last=None,
           paragraphs=None, minutes_old=None):
    date = NOW - timedelta(minutes=minutes_old if minutes_old is not None else (i+1) * 14)
    source = date - timedelta(minutes=21)
    independent_finales = (
        "夜の駅前では、帰り道に集まった人たちがひそかに明日の天気を予想していたという。",
        "新しい味の発表会が終わると、店主は試食品よりも空の箱が人気だったことを笑った。",
        "勝負がついたあとも観客の歌は続き、最後は両方のチームが一緒に歌うことになった。",
        "展示室の奥にある小さな水槽には、昨日まで見つからなかった新しい生き物がいた。",
        "市場の店先に並んだ野菜にはそれぞれ名前がつき、買い物客は投票に時間を使った。",
        "旅館の古い帳簿をめくると、昔から同じ部屋を使っていた旅人の名前が残っていた。",
    )
    finale = last if last is not None else independent_finales[i % len(independent_finales)]
    return {
        "slug": f"story-{i}",
        "title": title or f"架空の町で新しい試みが始まり意外な展開になる記事{i}",
        "summary": "架空の世界で起きる、ちょっと不思議な出来事を報じる。",
        "paragraphs": paragraphs or [
            "街の中で人々が古くから使ってきた仕組みを見直し、いままで考えられなかった提案が生まれた。",
            "利用者が試してみると予想とは少し違う変化が起き、暮らしの中で小さな工夫が始まった。",
            "検討会ではそれぞれの立場から意見が出された。" + finale,
        ],
        "category": category,
        "published_at": date.isoformat(),
        "source": {
            "url": source_url or f"https://example.org/news/{i}",
            "published_at": source.isoformat(),
        },
    }


class EditorialAuditSignalsTests(unittest.TestCase):
    def test_standard_distinct_stories_remain_unflagged(self):
        articles = [
            sample(0, title="花火の鑑賞に新しい遊び方", category="暮らし"),
            sample(1, title="地元アイスの食べ方", category="エンタメ"),
            sample(2, title="サッカー応援歌に変化", category="スポーツ"),
            sample(3, title="海の生き物の展示", category="社会"),
            sample(4, title="特産品の集荷", category="経済"),
            sample(5, title="観光シーズンの旅の話", category="暮らし"),
        ]
        original = copy.deepcopy(articles)
        self.assertEqual(audit(articles, NOW), [])
        self.assertEqual(articles, original, "The review must be read-only")

    def test_identical_real_source_prompts_a_warning_not_a_deletion(self):
        articles = [sample(0, source_url="https://example.org/news/one"),
                    sample(1, source_url="https://example.org/news/one")]
        notices = audit(articles, NOW)
        self.assertTrue(any(code == "REUSED_SOURCE" and slug == "story-1"
                            for code, slug, message in notices))
        self.assertEqual(len(articles), 2)

    def test_nearly_identical_last_lines_are_detected(self):
        repeated = "発行した結果、いちばん売れたのは商品そのものではなく、何も書かれていない説明書だったという"
        a = sample(0, last=repeated)
        b = sample(1, last=repeated)
        self.assertEqual(_last_line(a), _last_line(b))
        errors = _review_recent_content([a, b])
        self.assertTrue(any(code == "REPEATED_PUNCHLINE"
                            for code, _, _ in errors), errors)

    def test_bureaucracy_and_ai_runs_are_flagged_in_last_six_only(self):
        articles = [
            sample(i, title=f"AIが認証制度の自動判定を開始{i}", category="経済" if i < 4 else "暮らし")
            for i in range(6)
        ]
        notices = audit(articles, NOW)
        codes = [code for code, _, _ in notices]
        self.assertIn("RECENT_CATEGORY_RUN", codes)
        self.assertIn("SAME_MECHANISM_RUN", codes)
        self.assertTrue(any("確認・認証・手続きの逆効果" in message
                            for code, _, message in notices if code == "SAME_MECHANISM_RUN"))
        self.assertTrue(any("AIが人間の役割を奪う" in message
                            for code, _, message in notices if code == "SAME_MECHANISM_RUN"))

    def test_short_entry_gets_advice_but_never_fails(self):
        thin = sample(0, paragraphs=["見つかった", "困った", "終わった"])
        codes = [code for code, _, _ in audit([thin], NOW)]
        self.assertIn("THIN_STORY", codes)
        self.assertFalse(any(code == "INVALID_DATE" for code in codes))

    def test_invalid_story_metadata_is_safe_to_review(self):
        invalid = sample(0)
        invalid["published_at"] = "not-a-date"
        invalid["paragraphs"] = None
        result = audit([invalid], NOW)
        self.assertTrue(any(code == "INVALID_DATE" for code, _, _ in result))
        # Direct review on awkward but valid article contents never crashes.
        self.assertTrue(_review_recent_content([{"slug": "odd", "paragraphs": None,
                                                 "category":"社会", "published_at": "2026-11-03"}]))

    def test_old_news_does_not_create_false_today_quality_warnings(self):
        stale = sample(0)
        stale["published_at"] = (NOW - timedelta(days=5)).isoformat()
        warnings = audit([stale], NOW)
        self.assertFalse(any(code in ("THIN_STORY", "REPEATED_PUNCHLINE", "REUSED_SOURCE")
                             for code, _, _ in warnings))


if __name__ == "__main__":
    unittest.main()
