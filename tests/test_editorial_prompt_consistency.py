"""Guard writing instructions, not the quality or eligibility of real articles."""
import copy
import unittest
from pathlib import Path

from scripts.audit_satire import analyze, next_slot_brief

ROOT = Path(__file__).resolve().parents[1]
STALE_DIRECTIVES = ("2回目の逆転", "第2の展開", "見出しよりもう一段ひねる")


class EditorialPromptConsistencyTests(unittest.TestCase):
    def test_short_prompt_stays_short_without_repeating_all_metadata_rules(self):
        guide = (ROOT / "docs/EDITORIAL_LOOP.md").read_text(encoding="utf-8")
        heading = "### 毎回の発行で使う短い執筆プロンプト（最優先）"
        self.assertEqual(guide.count(heading), 1)
        section = guide.split(heading, 1)[1].split("\n### ", 1)[0]
        prompt = "\n".join(line[2:] for line in section.splitlines() if line.startswith("> "))
        self.assertTrue(prompt)
        self.assertLessEqual(len(prompt), 300, "Replace the short prompt; do not append rule history")
        self.assertIn("公開前の6点", prompt)
        for directive in STALE_DIRECTIVES:
            self.assertNotIn(directive, guide)

    def test_repetition_advice_keeps_the_cause_and_never_rewrites_articles(self):
        # Deliberately repetitive synthetic fixtures exercise all advice branches.
        articles = [{
            "slug": f"fixture-{i}",
            "title": "AI会議の申請を確認する担当者",
            "category": "技術",
            "published_at": f"2026-11-03T07:{30+i:02d}:00+09:00",
            "source": {"url": "https://example.test/source"},
            "paragraphs": ["2031年、架空の会社が新制度を発表した。", "担当者は申請を確認した。"],
        } for i in range(10)]
        before = copy.deepcopy(articles)
        advice = "\n".join(next_slot_brief(articles))
        warnings = "\n".join(message for _, _, message in analyze(articles))
        for directive in STALE_DIRECTIVES:
            self.assertNotIn(directive, advice + warnings)
        self.assertIn("人物・現場・原因は冒頭2文", advice)
        self.assertIn("台詞始まりは必須ではない", advice)
        self.assertIn("公開前の6点", advice)
        self.assertIn("個人の観察、風刺、架空の政策発表・新製品", advice)
        self.assertIn("具体的な出来事・仕組み・使用場面と結果で選ぶ", advice)
        self.assertIn("暮らし・文化も含め", advice)
        self.assertIn("直近の題材・語り口・結末と異なる読みどころを比べ", advice)
        for directive in ("固有のオチ", "見える逆効果", "行動と笑いで選ぶ", "別の笑いを探す"):
            self.assertNotIn(directive, advice)
        self.assertNotIn("最低1案", advice)
        self.assertIn("二度目の逆転は不要", advice)
        self.assertIn("警告だけで発行を止めない", advice)
        self.assertEqual(articles, before)

    def test_empty_input_still_does_not_invent_publications(self):
        advice = next_slot_brief([])
        self.assertEqual(len(advice), 1)
        self.assertIn("掲載済み記事なし", advice[0])


if __name__ == "__main__":
    unittest.main()
