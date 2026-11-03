"""Small, readable newspaper footer without losing free reader utilities.

The test exercises the Python HTML renderer without a browser or paid API.
"""
import re
import unittest

from scripts.build import layout, load, story


class FooterFocusTests(unittest.TestCase):
    def test_footer_uses_semantic_secondary_navigation(self):
        page = layout("未発生新聞", "説明", "/", "<p>テスト</p>", active="home")
        footer = page.split('<footer class="footer">', 1)[1]
        self.assertIn('<nav class="footer-nav" aria-label="補助リンク">', footer)
        nav = footer.split('<nav class="footer-nav"', 1)[1].split("</nav>", 1)[0]
        links = re.findall(r'href="([^"]+)"', nav)
        self.assertEqual(len(links), 8)
        for path in ("/saved/", "/archive/#category-feeds", "/about/",
                     "/editorial/", "/advertise/", "/privacy/", "/feed.xml"):
            self.assertIn(path, links)
        self.assertTrue(any(link.startswith("https://x.com/intent/tweet?") for link in links))
        # Main reading destinations are already present in the global header.
        for duplicate in ("/", "/archive/", "/serials/", "/issues/"):
            self.assertNotIn(duplicate, links)

    def test_fiction_and_market_disclosures_remain_concise_and_distinct(self):
        page = layout("未発生新聞", "説明", "/", "<p>テスト</p>")
        footer = page.split('<footer class="footer">', 1)[1]
        self.assertIn("創作記事・推理小説はフィクションです。", footer)
        self.assertIn("市場分析は投資助言ではありません。", footer)
        item = load()[0]
        story_page = story(item, [item])
        self.assertIn("<strong>この記事は創作です。</strong>", story_page)
        self.assertIn('id="story-source"', story_page)


if __name__ == "__main__":
    unittest.main()
