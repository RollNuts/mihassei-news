"""Offline regression tests for dated editions, source dates, and link integrity."""
import unittest
from scripts.build import daily_issues, issue_url, issue_index, issue_page, story, home


class DailyIssueTests(unittest.TestCase):
    def sample(self, day, index):
        return {
            "slug": f"fiction-{day.replace('-', '')}-{index}",
            "title": f"記事{day}-{index}",
            "summary": "すべて創作の短いニュースです。",
            "category": "社会",
            "fiction_company": "架空研究所",
            "art": "notice",
            "flash": "速報",
            "fiction_at": "2031-11-02T09:00:00+09:00",
            "published_at": day + f"T{10 + index:02}:30:00+09:00",
            "paragraphs": ["完全な架空ニュースです。", "あくまで創作です。"],
            "source": {
                "url": "https://example.com/fiction",
                "publisher": "参考ニュース",
                "topic": "現実のニュースから着想しました。",
                "published_at": "2026-11-02T09:00:00+09:00"
            },
        }

    def fixture(self):
        return (
            [self.sample("2026-11-02", i) for i in range(4)]
            + [self.sample("2026-11-01", i) for i in range(3)]
            + [self.sample("2026-10-31", i) for i in range(2)]
        )

    def test_real_dates_and_minimum_three_articles(self):
        groups=daily_issues(self.fixture())
        self.assertEqual(list(groups), ["2026-11-02", "2026-11-01"])
        self.assertEqual([len(x) for x in groups.values()], [4,3])
        self.assertEqual(groups["2026-11-02"][0]["slug"],"fiction-20261102-3")
        self.assertNotIn("2026-10-31",groups)
        self.assertEqual(issue_url("2026-11-02"), "/issues/2026-11-02/")
        with self.assertRaises(ValueError):
            issue_url("../evil")
        with self.assertRaises(ValueError):
            issue_url("2026-02-30")

    def test_date_page_is_a_full_unique_human_readable_issue(self):
        groups=daily_issues(self.fixture())
        newer=issue_page("2026-11-02",groups["2026-11-02"],groups)
        older=issue_page("2026-11-01",groups["2026-11-01"],groups)
        self.assertIn('<link rel="canonical" href="https://veripsa.com/issues/2026-11-02/">',newer)
        self.assertIn('<time datetime="2026-11-02">2026年11月2日発行号</time>',newer)
        self.assertIn("現実の2026年11月2日に掲載した、4本の創作ニュース",newer)
        self.assertIn("2031",newer)
        self.assertIn("/issues/2026-11-01/",newer)
        self.assertIn("/issues/2026-11-02/",older)
        for a in groups["2026-11-02"]:
            self.assertIn('href="/articles/'+a["slug"]+'/"',newer)
        self.assertNotIn('href="/articles/fiction-20261101-0/"',newer)
        self.assertNotIn('href="/articles/fiction-20261031-0/"',newer)

    def test_index_and_article_return_to_edition(self):
        entries=self.fixture()
        groups=daily_issues(entries)
        listing=issue_index(groups)
        self.assertIn('href="/issues/2026-11-02/"',listing)
        self.assertIn('href="/issues/2026-11-01/"',listing)
        self.assertNotIn('/issues/2026-10-31/',listing)
        self.assertIn("3本以上",listing)
        full=story(entries[0],entries,groups)
        thin=story(entries[-1],entries,groups)
        self.assertIn('class="article-issue-link" href="/issues/2026-11-02/"',full)
        self.assertNotIn('class="article-issue-link"',thin)

    def test_homepage_promotes_date_editions_not_fake_future_dates(self):
        entries=self.fixture()
        markup=home(entries)
        self.assertIn('class="daily-issues-section"',markup)
        self.assertIn('href="/issues/"',markup)
        self.assertIn('href="/issues/2026-11-02/"',markup)
        self.assertIn("発行号を読む",markup)
        self.assertNotIn('/issues/2031-',markup)


if __name__ == "__main__":
    unittest.main()
