"""The newspaper promises playful five-year fiction, not a prediction service."""
import unittest

from scripts.build import about, editorial_policy, home, layout, load


class EditorialPositioningTests(unittest.TestCase):
    def test_home_explains_fiction_before_reader_interactions(self):
        page = home(load())
        self.assertIn('class="fiction-mark">創作報道', page)
        self.assertIn("まだ起きていないニュースを、真顔で。", page)
        self.assertIn("最新の架空ニュース", page)
        self.assertIn("社会の矛盾や日常の理不尽", page)
        self.assertNotIn("この新聞の読み方", page)

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

    def test_home_presents_a_single_disclosure_before_the_lead(self):
        page = home(load())
        self.assertIn('class="fiction-mark">創作報道', page)
        self.assertIn('TOP STORY / 一面', page)
        self.assertNotIn('class="concept-primer"', page)
        self.assertTrue(page.index('class="fiction-mark"') < page.index('<section class="hero-grid">'))

    def test_article_marks_fiction_and_preserves_the_real_source(self):
        from scripts.build import story
        items = load()
        page = story(items[0], items)
        self.assertIn('class="story-fiction-context"', page)
        self.assertNotIn('href="#story-source"', page)
        self.assertIn('<summary>着想元を見る</summary>', page)
        self.assertIn('id="story-source"', page)
        self.assertIn("元記事が報じた事実ではありません", page)
        self.assertIn('aria-label="架空ニュースのありそう度を投票"', page)
        self.assertIn('data-poll-choice="plausible"', page)
        self.assertIn('data-poll-choice="unlikely"', page)
        self.assertIn("感想やツッコミをどうぞ。投稿内容は公開されます。", page)

    def test_public_poll_labels_do_not_claim_to_predict_the_future(self):
        from pathlib import Path
        from scripts.build import about, privacy
        browser = (Path(__file__).resolve().parent.parent /
                   "assets" / "engagement.js").read_text(encoding="utf-8")
        self.assertIn("この『もしも』に", browser)
        self.assertNotIn("実際の予想", browser)
        self.assertNotIn("最初の予想", browser)
        self.assertIn("架空ニュースの二択投票について", privacy())
        self.assertIn("未来の出来事が的中する確率", privacy())
        self.assertIn("もしもの二択投票", about())

    def test_reader_share_is_a_joke_not_a_prophecy(self):
        from urllib.parse import parse_qs, urlparse
        from scripts.build import share_url, load
        article = load()[0]
        query = parse_qs(urlparse(share_url(article)).query)
        text = query["text"][0]
        self.assertIn("5年後のもしも（創作）", text)
        self.assertIn("未発生新聞", text)
        self.assertIn(article["title"], text)
        self.assertIn("utm_campaign=reader_share", query["url"][0])
        self.assertNotIn("的中率", text)

    def test_metadata_and_brand_stay_unhappened_newspaper(self):
        page = layout("未発生新聞", "テスト", "/", "<p>テスト</p>")
        self.assertIn("未発生新聞｜5年後を勝手に想像する風刺ニュース", page)
        self.assertIn('property="og:site_name" content="未発生新聞"', page)
        self.assertIn('class="fiction-mark">創作報道', page)
        self.assertIn("まだ起きていないニュースを、真顔で。", page)
        self.assertIn("紙面の架空時計", page)
        self.assertNotIn("5年先の時計", page)


if __name__ == "__main__":
    unittest.main()
