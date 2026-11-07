"""Reader drafts keep a complete entry, relevant tags and an intact article link."""
import copy
import unittest
from urllib.parse import parse_qs, urlparse

from scripts.build import share_url
from scripts.share_desk import draft
from scripts.social_text import reader_share_text, x_weighted_length


class ReaderShareTests(unittest.TestCase):
    def setUp(self):
        self.article = {
            "slug": "walking-home", "title": "移動ポイントのため家の前でもう一周",
            "summary": "帰宅するつもりがポイントを集め続けている。",
            "x_share_lead": "家の前まで来たのに、ポイントのためにもう一周。節約って、何を減らすことだったっけ。",
            "x_tags": ["ポイ活"],
        }
        self.url = "https://veripsa.com/articles/walking-home/"

    def test_page_and_manual_draft_use_the_complete_editorial_entry(self):
        before = copy.deepcopy(self.article)
        params = parse_qs(urlparse(share_url(self.article)).query)
        expected = self.article["x_share_lead"] + "\n\n#未発生新聞 #ポイ活"
        self.assertEqual(params["text"], [expected])
        self.assertEqual(draft(self.article)["text"], expected)
        self.assertEqual(params["url"][0].split("?")[0], self.url)
        tracking = parse_qs(urlparse(params["url"][0]).query)
        self.assertEqual(tracking["utm_campaign"], ["reader_share"])
        self.assertEqual(tracking["utm_content"], ["reader_entry_v3"])
        self.assertEqual(self.article, before)

    def test_unusable_lead_falls_back_to_headline_without_summary_fragments(self):
        for lead in (None, {}, "", "短い", "長い紹介文" * 80,
                     "要約：家の前まで来たのに、ポイントのためにもう一周して帰宅できない。",
                     "家の前まで来たのに #ポイ活 のために、もう一周して帰宅できない。",
                     "家の前まで来たのに帰宅できない。 https://example.com を見てください。"):
            with self.subTest(lead=lead):
                self.article["x_share_lead"] = lead
                self.assertEqual(reader_share_text(self.article, self.url),
                                 self.article["title"] + "\n\n#未発生新聞 #ポイ活")

    def test_long_headline_keeps_brand_ticker_and_link_budget(self):
        self.article.update(title="長い見出し🤔" * 100, x_share_lead=None,
                            x_tags=["AIバブル"], x_tickers=["NVDA"])
        url = self.url + "?utm_content=" + "a" * 250
        text = reader_share_text(self.article, url)
        self.assertTrue(text.endswith("…\n\n#未発生新聞 $NVDA #AIバブル"))
        self.assertLessEqual(x_weighted_length(text + "\n\n" + url), 280)

    def test_brand_is_present_once_even_without_discovery_terms(self):
        for tags in ([], ["未発生新聞"], ["SNS", "アプリ", "社会"]):
            article = {"title": "レジの常連客が優秀スタッフに選ばれた", "x_tags": tags}
            text = reader_share_text(article, self.url)
            self.assertEqual(text.splitlines()[-1], "#未発生新聞")
            self.assertEqual(text.count("#未発生新聞"), 1)

    def test_followup_fallback_keeps_context_and_removes_legacy_qualifiers(self):
        article = {"title": "架空の店で再び表彰", "followup_of": "original"}
        self.assertEqual(reader_share_text(article, self.url),
                         "【続報】\n店で再び表彰\n\n#未発生新聞")
