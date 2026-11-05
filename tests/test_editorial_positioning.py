"""The newspaper promises playful five-year fiction, not a prediction service."""
import unittest
from pathlib import Path

from scripts.build import about, editorial_policy, home, layout, load


class EditorialPositioningTests(unittest.TestCase):
    def test_home_explains_fiction_before_reader_interactions(self):
        page = home(load())
        self.assertIn('class="fiction-mark">創作報道', page)
        self.assertIn("まだ起きていないニュースを、真顔で。", page)
        self.assertIn("data-edition-title>最新記事", page)
        self.assertIn("社会の矛盾や日常の理不尽", page)
        self.assertNotIn("この新聞の読み方", page)

    def test_about_identifies_satire_without_claiming_accuracy(self):
        page = about()
        self.assertIn("風刺とユーモアの新聞", page)
        self.assertIn("今のニュースや身近な出来事", page)
        self.assertIn("5年後にふくらませて楽しむ", page)
        self.assertIn("ニュース記事と推理小説はフィクションです", page)
        self.assertIn("現実の公開資料に基づく市場分析", page)

    def test_policy_preserves_separation_from_source_backed_market_analysis(self):
        page = editorial_policy()
        self.assertIn("ニュース記事と「未発生事件簿」はフィクションです", page)
        self.assertIn("投資助言や売買の勧誘ではありません", page)
        self.assertIn("「未来相場観測室」は現実の公開資料に基づく市場分析", page)
        self.assertIn("出典・予測日・検証条件を示します", page)

    def test_home_presents_a_single_disclosure_before_the_lead(self):
        page = home(load())
        self.assertIn('class="fiction-mark">創作報道', page)
        self.assertIn('<span class="small-label red">一面</span>', page)
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
        self.assertIn("着想元は、この記事の出来事を報じたものではありません", page)
        self.assertIn('aria-label="この「もしも」のありそう度を投票"', page)
        self.assertIn('data-poll-choice="plausible"', page)
        self.assertIn('data-poll-choice="unlikely"', page)
        self.assertIn("投稿内容は公開されます。", page)

    def test_public_poll_labels_do_not_claim_to_predict_the_future(self):
        from pathlib import Path
        from scripts.build import privacy, story
        browser = (Path(__file__).resolve().parent.parent /
                   "assets" / "engagement.js").read_text(encoding="utf-8")
        self.assertIn("この『もしも』に", browser)
        self.assertNotIn("実際の予想", browser)
        self.assertNotIn("最初の予想", browser)
        self.assertIn("<h2>投票</h2>", privacy())
        self.assertIn("実人数・世論調査・出来事が起こる確率を示すものではありません", privacy())
        articles = load()
        self.assertIn('aria-label="この「もしも」のありそう度を投票"', story(articles[0], articles))

    def test_reader_share_keeps_article_without_repeated_fiction_labels(self):
        from urllib.parse import parse_qs, urlparse
        from scripts.build import share_url, load
        article = load()[0]
        query = parse_qs(urlparse(share_url(article)).query)
        text = query["text"][0]
        self.assertEqual(text.splitlines()[0], "【未発生新聞】")
        self.assertEqual(text.splitlines()[-1], " ".join("#" + tag for tag in article["tags"][:2]))
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
        self.assertNotIn("紙面の架空時計", page)
        self.assertNotIn("data-future-clock", page)
        self.assertNotIn("5年先の時計", page)
        css = (Path(__file__).resolve().parent.parent / "assets" / "newsroom.css").read_text(encoding="utf-8")
        self.assertNotIn(".strip{position:sticky", css)


if __name__ == "__main__":
    unittest.main()
