"""The newspaper promises playful five-year fiction, not a prediction service."""
import unittest

from scripts.build import about, editorial_policy, home, layout, load


class EditorialPositioningTests(unittest.TestCase):
    def test_home_explains_fiction_before_reader_interactions(self):
        page = home(load())
        self.assertIn("未来を当てるための予測ではありません", page)
        self.assertIn("今の話題をちょっと大げさにした", page)
        self.assertIn("まだ起きていないニュースを、真顔で。", page)
        self.assertIn("最新の架空ニュース", page)
        self.assertIn("「公開」は実際の掲載日時", page)

    def test_about_identifies_satire_without_claiming_accuracy(self):
        page = about()
        self.assertIn("未来を当てようとしているわけではありません", page)
        self.assertIn("でも、ちょっとありそう", page)
        self.assertIn("世の中の変な仕組み", page)
        self.assertIn("推理小説は創作です", page)
        self.assertIn("市場考察", page)

    def test_policy_preserves_separation_from_source_backed_market_analysis(self):
        page = editorial_policy()
        self.assertIn("予知ではなく、未来を使った冗談", page)
        self.assertIn("予測の的中率を競うものでも", page)
        self.assertIn("連載「未来相場観測室」は現実の公開資料", page)
        self.assertIn("記事の創作表示", page)

    def test_metadata_and_brand_stay_unhappened_newspaper(self):
        page = layout("未発生新聞", "テスト", "/", "<p>テスト</p>")
        self.assertIn("未発生新聞｜5年後を勝手に想像する風刺ニュース", page)
        self.assertIn('property="og:site_name" content="未発生新聞"', page)
        self.assertIn('class="fiction-mark">創作報道', page)
        self.assertIn("5年後は、こうなってるかも", page)


if __name__ == "__main__":
    unittest.main()
