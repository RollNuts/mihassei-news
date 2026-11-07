"""Guard writing instructions, not the quality or eligibility of real articles."""
import copy
import unittest
from pathlib import Path

from scripts.audit_satire import analyze, next_slot_brief

ROOT = Path(__file__).resolve().parents[1]
STALE_DIRECTIVES = ("2回目の逆転", "第2の展開", "見出しよりもう一段ひねる")


class EditorialPromptConsistencyTests(unittest.TestCase):
    def test_short_prompt_does_not_require_an_extra_twist(self):
        guide = (ROOT / "docs/EDITORIAL_LOOP.md").read_text(encoding="utf-8")
        heading = "### 毎回の発行で使う短い執筆プロンプト（最優先）"
        self.assertEqual(guide.count(heading), 1)
        prompt = guide.split(heading, 1)[1].split("\n### ", 1)[0].strip()
        self.assertLessEqual(len(prompt), 450, "Replace the short prompt; do not append a rule history")
        self.assertIn("候補3案", prompt)
        self.assertIn("弱い小ネタ", prompt)
        self.assertIn("冒頭2文", prompt)
        self.assertIn("原因", prompt)
        self.assertIn("二度目の逆転は不要", prompt)
        self.assertIn("架空の", prompt)
        self.assertIn("断りを書かない", prompt)
        self.assertIn("summary", prompt)
        self.assertIn("設定を減らす", prompt)
        self.assertIn("editorial_tone", prompt)
        self.assertIn("reader_interest", prompt)
        self.assertIn("interest_next", prompt)
        self.assertIn("character_next", prompt)
        self.assertIn("editorial_payoff", prompt)
        self.assertIn("payoff_next", prompt)
        self.assertIn("plausible_future", prompt)
        self.assertIn("plausibility_basis", prompt)
        self.assertIn("現実の兆候", prompt)
        self.assertIn("直近2本", prompt)
        self.assertIn("初見テスト", prompt)
        self.assertIn("reader_clarity", prompt)
        self.assertIn("誰が", prompt)
        self.assertIn("何をして", prompt)
        self.assertIn("なぜ変", prompt)
        for directive in STALE_DIRECTIVES:
            self.assertNotIn(directive, guide)

    def test_weak_story_gate_rejects_merely_valid_but_dull_premises(self):
        guide = (ROOT / "docs/EDITORIAL_LOOP.md").read_text(encoding="utf-8")
        heading = "### 「成立しているだけ」を落とす企画ゲート（採用前必須）"
        self.assertEqual(guide.count(heading), 1)
        gate = guide.split(heading, 1)[1].split("\n### ", 1)[0]
        for phrase in (
            "候補3案", "変な人が変なこと", "かわいい", "ほっこり",
            "固有名詞や商品仕様だけの一発小ネタ", "15秒", "見出しの反復ではない具体的な行動・台詞",
            "弱い小ネタを説明・流行語・画像・Xタグで救済して公開しない",
        ):
            self.assertIn(phrase, gate)
        self.assertIn("見送る", gate)

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
        self.assertIn("二度目の逆転は不要", advice)
        self.assertIn("警告だけで発行を止めない", advice)
        self.assertEqual(articles, before)

    def test_empty_input_still_does_not_invent_publications(self):
        advice = next_slot_brief([])
        self.assertEqual(len(advice), 1)
        self.assertIn("掲載済み記事なし", advice[0])


if __name__ == "__main__":
    unittest.main()
