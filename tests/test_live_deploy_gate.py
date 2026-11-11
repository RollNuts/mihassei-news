"""The production workflow must verify actual published bytes, not an optional app check."""
import unittest
import copy
import html
import io
import json
import re
import textwrap
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]

class DeploymentWorkflowTests(unittest.TestCase):
    def test_live_artwork_checks_follow_actual_text_free_article_design(self):
        checker=(ROOT/"scripts/check_live.py").read_text(encoding="utf-8")
        self.assertIn('b"data-visual-topic="',checker)
        self.assertIn('b"<text" in cover',checker)
        self.assertIn('class="share-control"',checker)
        self.assertIn('if "創作イラスト" not in page:',checker)
        self.assertNotIn('class="reader-follow"',checker)

    def test_live_checks_follow_pv_labels_and_keep_browser_units_separate(self):
        """Release checks follow measured PV without relabeling legacy readers."""
        checker=(ROOT/"scripts/check_live.py").read_text(encoding="utf-8")
        builder=(ROOT/"scripts/build.py").read_text(encoding="utf-8")
        for token in ("今日のPV","昨日のPV","data-pv-widget",
                      "data-site-pv-yesterday","data-site-pv-week",
                      "data-pv-tracking-note"):
            self.assertIn(token,checker)
            self.assertIn(token,builder)
        self.assertIn("Daily JST audience counters missing or invalid",checker)
        self.assertIn("Previous JST audience counters missing or invalid",checker)
        self.assertIn("Daily per-story browser count missing or invalid",checker)
        self.assertNotIn('"観測されたブラウザ" not in body',checker)

    def test_rss_live_checker_uses_all_current_categories(self):
        from scripts.build import CATEGORIES
        from xml.etree import ElementTree as ET
        checker=(ROOT/"scripts/check_live.py").read_text(encoding="utf-8")
        self.assertIn('set(CATEGORIES[1:])', checker)
        from scripts.build import CATEGORY_PATHS
        self.assertIn('category_paths = CATEGORY_PATHS', checker)
        self.assertEqual(CATEGORY_PATHS["事件"], "incidents")
        self.assertIn("事件", CATEGORIES)
        feed=ET.fromstring("<rss><channel><item><category>事件</category></item></channel></rss>")
        self.assertIn(feed.findtext("./channel/item/category"), set(CATEGORIES[1:]))

    def test_live_checker_inspects_new_publications_not_old_source_news(self):
        """A newly published story with an older source still needs smoke checks."""
        from scripts.check_live import latest_articles
        from scripts.build import editorial_lead
        older={"slug":"older","published_at":"2026-11-02T18:30:00+09:00",
               "source":{"published_at":"2026-11-03T17:00:00+09:00"}}
        fresh={"slug":"fresh","published_at":"2026-11-03T18:00:00+09:00",
               "source":{"published_at":"2026-11-01T07:00:00+09:00"}}
        items=[older,fresh]
        self.assertEqual(latest_articles(items)[0]["slug"],"fresh")
        self.assertEqual(latest_articles(list(reversed(items)))[0]["slug"],"fresh")
        self.assertEqual(latest_articles(items,limit=1)[0]["slug"],"fresh")
        self.assertEqual(len(latest_articles(items,limit=None)),2)
        self.assertEqual(editorial_lead(items)["slug"],
                         latest_articles(items)[0]["slug"])

    def test_verified_motion_player_and_live_checker_agree_on_cache_bust(self):
        from scripts import build
        from unittest.mock import patch
        checker=(ROOT/"scripts/check_live.py").read_text(encoding="utf-8")
        test_article=build.load()[0]
        manifest={"sha256": "b"*64, "created_at": "2026-11-03T01:00:00+00:00"}
        # Exercise the player contract with an already-vetted clip. Editorial
        # changes may correctly invalidate the newest article's old image;
        # image/review rejection is covered separately in test_visual_review.
        with patch.object(build, "article_clip", return_value=manifest):
            player=build.article_hero_media(test_article)
        self.assertIn('preload="metadata"', player)
        self.assertIn('?v=' + "b"*12, player)
        self.assertIn("data-video-fallback", player)
        self.assertIn('recorded_clip["sha256"][:12]', checker)
        self.assertIn('controls playsinline muted loop preload="metadata"', checker)
        self.assertIn("data-video-fallback", checker)
        self.assertIn('hashlib.sha256(movie).hexdigest() != recorded_clip["sha256"]', checker)

    def test_visible_headlines_match_real_article_data_across_all_surfaces(self):
        """A title present only in metadata must not hide a stale visible heading."""
        import html
        from scripts.build import archive, editorial_lead, home, load, story
        from scripts.check_live import require_matching_public_headline

        articles = load()
        lead = editorial_lead(articles)
        snippets = (
            ("home", home(articles)),
            ("archive", archive(articles)),
            ("article", story(lead, articles)),
        )
        for surface, markup in snippets:
            with self.subTest(surface=surface):
                require_matching_public_headline(lead, markup, surface)
                wrong = dict(lead, title="旧版の見出し")
                with self.assertRaisesRegex(RuntimeError, "headline mismatch"):
                    require_matching_public_headline(wrong, markup, surface)

        # A stale visible article H1 must fail even though the correct title
        # is still present in Open Graph tags and related metadata.
        detail = story(lead, articles)
        original = "<h1>" + html.escape(lead["title"], quote=True) + "</h1>"
        self.assertIn(original, detail)
        stale = detail.replace(original, "<h1>古い記事見出し</h1>", 1)
        with self.assertRaisesRegex(RuntimeError, "headline mismatch"):
            require_matching_public_headline(lead, stale, "article")

    def test_live_article_material_uses_reader_visible_copy_not_legacy_boilerplate(self):
        from scripts.build import load, story
        from scripts.check_live import article_reader_needles

        article = next(item for item in load()
                       if item["slug"] == "film-village-all-stars-no-extras")
        markup = html.unescape(story(article, load()))
        needles = article_reader_needles(article)
        for needle in needles:
            self.assertIn(needle, markup)

        joined = "\n".join(needles)
        self.assertNotIn("本紙独自の設定", joined)
        self.assertNotIn("2031年、", joined)
        self.assertNotIn("架空の", joined)
        self.assertIn("創作報道", needles)
        self.assertNotIn('class="story-fiction-context"', markup)

    def test_live_release_waits_for_real_public_commit(self):
        markup=(ROOT/".github/workflows/verify-public-photos.yml").read_text(encoding="utf-8")
        self.assertIn("concurrency:",markup)
        self.assertIn("cancel-in-progress: true",markup)
        self.assertIn('EXPECTED_SHA: ${{ github.sha }}',markup)
        self.assertIn('python3 scripts/wait_for_live_revision.py --allow-docs-only',markup)
        self.assertRegex(markup, r"(?m)^    paths-ignore:\n      - 'docs/\*\*'$")
        self.assertIn('fetch-depth: 0',markup)
        self.assertIn('timeout-minutes: 15',markup)
        self.assertIn("python3 scripts/check_live.py",markup)
        self.assertIn("python3 scripts/check_share_cards.py",markup)
        self.assertNotIn("Workers Builds: mihassei-news",markup)
        self.assertNotIn("GH_TOKEN:",markup)
        self.assertIn("read-only",markup.lower())


class PVHealthTriggerTests(unittest.TestCase):
    def setUp(self):
        self.workflow = (ROOT / ".github/workflows/public-engagement-health.yml").read_text(encoding="utf-8")

    def allowed(self, event, conclusion="success", repository="RollNuts/mihassei-news", branch="main"):
        # Evaluate the actual workflow's simple boolean condition rather than
        # maintaining a second implementation of the event filter in this test.
        match = re.search(r"(?m)^    if: >-\n((?:      .*\n)+)", self.workflow)
        self.assertIsNotNone(match)
        expression = " ".join(line.strip() for line in match.group(1).splitlines())
        expression = expression.replace("&&", " and ").replace("||", " or ")
        github = SimpleNamespace(
            event_name=event, repository="RollNuts/mihassei-news",
            event=SimpleNamespace(workflow_run=SimpleNamespace(
                conclusion=conclusion, head_branch=branch,
                head_repository=SimpleNamespace(full_name=repository))))
        return eval(expression, {"__builtins__": {}}, {"github": github})

    def test_pv_follows_own_main_release_success_without_another_revision_wait(self):
        self.assertIn("workflow_run:\n    workflows: [Verify live article delivery and SEO]", self.workflow)
        self.assertIn("types: [completed]\n    branches: [main]", self.workflow)
        self.assertNotIn("  push:", self.workflow)
        self.assertNotIn("build-revision.txt", self.workflow)
        self.assertNotIn("wait_for_live_revision", self.workflow)
        self.assertNotIn("EXPECTED_SHA", self.workflow)
        self.assertNotIn("actions/checkout", self.workflow)
        self.assertTrue(self.allowed("workflow_run"))

    def test_failed_fork_and_other_branch_runs_cannot_report_pv_health_success(self):
        for conclusion in ("failure", "cancelled", "skipped", "timed_out", None):
            with self.subTest(conclusion=conclusion):
                self.assertFalse(self.allowed("workflow_run", conclusion=conclusion))
        self.assertFalse(self.allowed("workflow_run", repository="other/fork"))
        self.assertFalse(self.allowed("workflow_run", branch="feature"))
        self.assertFalse(self.allowed("push"))
        self.assertFalse(self.allowed("pull_request"))

    def test_scheduled_and_manual_health_remain_independent_of_release_events(self):
        for event in ("schedule", "workflow_dispatch"):
            with self.subTest(event=event):
                self.assertTrue(self.allowed(event, conclusion=None, repository=None, branch=None))


class PVRecommendationDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start = datetime.fromisoformat("2026-11-01T00:00:00+09:00")
        cls.articles = [
            {"slug": "story-" + str(index), "title": '記事 "A&B" <' + str(index) + ">",
             "category": "社会", "published_at": (start + timedelta(hours=index)).isoformat(),
             "source": {"published_at": "2026-11-01T00:00:00+09:00"}}
            for index in range(30)
        ]

    @staticmethod
    def with_options(markup, transform):
        def replace(match):
            options = json.loads(html.unescape(match.group(2)))
            value = html.escape(json.dumps(transform(options), ensure_ascii=False), quote=True)
            return match.group(1) + value + match.group(3)
        return re.sub(r'(data-popular-options=")([^"]*)(")', replace, markup, count=1)

    def test_live_checker_accepts_full_archive_and_escaped_article_metadata(self):
        from scripts.build import home, load, pv_recommendations, story
        from scripts.check_live import verify_pv_recommendations

        markup = pv_recommendations(self.articles)
        verify_pv_recommendations(markup, self.articles)
        actual = load()
        verify_pv_recommendations(home(actual), actual)
        verify_pv_recommendations(story(actual[0], actual), actual, actual[0])

    def test_live_checker_rejects_truncation_duplicates_stale_titles_and_fake_rankings(self):
        from scripts.build import pv_recommendations
        from scripts.check_live import verify_pv_recommendations

        markup = pv_recommendations(self.articles)
        variants = {
            "only newest twenty": self.with_options(markup, lambda options: options[:20]),
            "duplicate": self.with_options(markup, lambda options: options + options[:1]),
            "unknown story": self.with_options(markup, lambda options: options + [
                {"slug": "unpublished", "title": "未公開", "category": "社会"}]),
            "stale title": self.with_options(markup, lambda options: [dict(options[0], title="旧題")] + options[1:]),
            "stale category": self.with_options(markup, lambda options: [dict(options[0], category="技術")] + options[1:]),
            "visible without PV": markup.replace(" hidden>", ">", 1),
            "browser ranking": markup.replace("過去7日間のPV順", "ブラウザ数順"),
            "static winner": markup.replace('data-popular-list></ol>', 'data-popular-list><li>人気1位</li></ol>'),
            "duplicate panels": markup + markup,
            "old selection": markup + '<section class="starter-editorial"></section>',
        }
        for label, candidate in variants.items():
            with self.subTest(label=label), self.assertRaises(RuntimeError):
                verify_pv_recommendations(candidate, self.articles)

    def test_article_fallback_advances_sequentially_and_never_claims_popularity(self):
        from scripts.build import pv_recommendations
        from scripts.check_live import verify_pv_recommendations

        current = self.articles[-1]
        fallback = ('<nav class="reader-next" data-popular-fallback data-reader-next-mode="sequential">'
                    '<span class="reader-next-kicker">次の記事</span>'
                    '<span class="reader-next-meta">続けて読む · 社会</span>'
                    '<a class="reader-next-title" href="/articles/story-28/">記事タイトル</a></nav>')
        panel = pv_recommendations(self.articles, current["slug"])
        verify_pv_recommendations(fallback + panel, self.articles, current)
        variants = {
            "self link": (fallback + panel).replace('href="/articles/story-28/"', 'href="/articles/story-29/"'),
            "skipped fallback": (fallback + panel).replace('href="/articles/story-28/"', 'href="/articles/story-0/"'),
            "hidden fallback": (fallback + panel).replace(
                'data-reader-next-mode="sequential">',
                'data-reader-next-mode="sequential" hidden>',
            ),
            "misleading label": (fallback + panel).replace("次の記事", "一番人気"),
            "self candidate": fallback + pv_recommendations(self.articles),
            "missing fallback": panel,
            "old related rail": fallback + panel + '<section class="article-related"></section>',
        }
        for label, candidate in variants.items():
            with self.subTest(label=label), self.assertRaises(RuntimeError):
                verify_pv_recommendations(candidate, self.articles, current)
        # The oldest (or only) published story ends the linear stream at the archive.
        oldest = self.articles[:1]
        archive = fallback.replace(
            'data-reader-next-mode="sequential"',
            'data-reader-next-mode="archive"',
        ).replace("/articles/story-28/", "/archive/")
        verify_pv_recommendations(
            archive + pv_recommendations(oldest, oldest[0]["slug"]),
            oldest, oldest[0],
        )


class PVAPIDeploymentTests(unittest.TestCase):
    @staticmethod
    def payload(slug="story-a"):
        return {
            "version": 2,
            "unit": "anonymous-browser", "visitors": 900, "pageViews": 32,
            "pageViewTracking": {"unit": "page-view", "timezone": "Asia/Tokyo",
                                 "startedAtUtc": "2026-11-04T00:00:00.000Z"},
            "sessions": 20,
            "sessionTracking": {"unit": "session", "timezone": "Asia/Tokyo",
                                "startedAtUtc": "2026-11-04T00:00:00.000Z",
                                "inactivityTimeoutMinutes": 30},
            "pageViewSourcesToday": {"x": 10, "search": 5, "external": 2, "unattributed": 15},
            "pageViewSourcesCurrentHour": {"x": 2, "search": 1, "external": 0, "unattributed": 1},
            "pageViewSourceTracking": {
                "unit": "page-view", "timezone": "Asia/Tokyo",
                "startedAtUtc": "2026-11-04T00:00:00.000Z",
                "attribution": "session-acquisition-channel",
            },
            "pageViewKindsToday": {
                "home": 10, "article": 15, "archive": 3, "serial": 1, "other": 1,
            },
            "pageViewKindsCurrentHour": {
                "home": 1, "article": 2, "archive": 1, "serial": 0, "other": 0,
            },
            "pageViewKindTracking": {
                "unit": "page-view", "timezone": "Asia/Tokyo",
                "startedAtUtc": "2026-11-04T00:00:00.000Z",
                "categories": ["home", "article", "archive", "serial", "other"],
            },
            "today": {"dateJst": "2026-11-04", "visitors": 12, "articleReads": 8,
                      "pageViews": 32, "pageViewsCoverage": "in-progress",
                      "sessions": 12, "sessionsCoverage": "in-progress"},
            "yesterday": {"dateJst": "2026-11-03", "visitors": 30, "articleReads": 40,
                          "pageViews": None, "pageViewsCoverage": "unmeasured",
                          "sessions": None, "sessionsCoverage": "unmeasured"},
            "last7Days": {"startDateJst": "2026-10-29", "endDateJst": "2026-11-04",
                          "pageViews": 32, "measuredDays": 1, "complete": False},
            "hourly": {
                "timezone": "Asia/Tokyo",
                "startedAtUtc": "2026-11-04T00:00:00.000Z",
                "today": [
                    {"hourJst": "2026-11-04T" + str(hour).zfill(2) + ":00",
                     "pageViews": 32 if hour == 9 else 0,
                     "pageViewsCoverage": "complete"}
                    for hour in range(24)
                ],
            },
            "topArticles": {
                "currentHour": [{"article": slug, "pageViews": 2}],
                "today": [{"article": slug, "pageViews": 7}],
                "last7Days": [{"article": slug, "pageViews": 7}],
            },
            "articles": {slug: {"likes": 0, "readers": 500, "todayReaders": 200,
                                "pageViews": 7, "todayPageViews": 7, "last7DaysPageViews": 7,
                                "votes": {"plausible": 0, "unlikely": 0}}},
        }

    def test_pv_contract_validates_recent_counts_without_using_legacy_browsers(self):
        from scripts.check_live import verify_pageview_contract
        payload = self.payload()
        before = copy.deepcopy(payload)
        verify_pageview_contract(payload, "story-a")
        self.assertEqual(payload, before)
        payload["pageViewTracking"]["startedAtUtc"] = None
        payload["pageViews"] = None
        payload["today"].update(pageViews=None, pageViewsCoverage="unmeasured")
        payload["last7Days"].update(pageViews=None, measuredDays=0)
        payload["articles"]["story-a"].update(pageViews=None, todayPageViews=None, last7DaysPageViews=None)
        verify_pageview_contract(payload, "story-a")

    def test_pv_contract_rejects_missing_recent_pv_false_counts_and_period_mismatch(self):
        from scripts.check_live import verify_pageview_contract
        payload = self.payload()
        del payload["articles"]["story-a"]["last7DaysPageViews"]
        with self.assertRaisesRegex(RuntimeError, "last7DaysPageViews"):
            verify_pageview_contract(payload, "story-a")
        for value in (None, -1, True, "7", 33):
            payload = self.payload()
            payload["articles"]["story-a"]["last7DaysPageViews"] = value
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                verify_pageview_contract(payload, "story-a")
        for key, value in (("startDateJst", "2026-10-28"), ("endDateJst", "2026-11-03")):
            payload = self.payload()
            payload["last7Days"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, "JST period"):
                verify_pageview_contract(payload, "story-a")
        payload = self.payload()
        payload["pageViewTracking"]["unit"] = "anonymous-browser"
        with self.assertRaisesRegex(RuntimeError, "metadata"):
            verify_pageview_contract(payload, "story-a")

    def run_health_workflow(self, payload):
        workflow = (ROOT / ".github/workflows/public-engagement-health.yml").read_text(encoding="utf-8")
        code = textwrap.dedent(workflow.split("          python3 - <<'PY'\n", 1)[1].rsplit("          PY", 1)[0])
        requests = []
        responses = {
            "/": "未発生新聞".encode(),
            "/ads.txt": b"google.com, pub-6754575901141756, DIRECT, f08c47fec0942fa0",
            "/api/engagement": json.dumps(payload).encode(),
            "/api/comments": json.dumps({"article": "apology-before-incident", "total": 0, "comments": []}).encode(),
            "/articles/apology-before-incident/": b'data-future-poll data-poll-choice="plausible" data-poll-choice="unlikely"',
        }

        class Response:
            status = 200
            headers = {"content-type": "application/json"}

            def __init__(self, data):
                self.data = data

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, limit):
                return self.data[:limit]

        def fetch(request, timeout):
            self.assertEqual(request.get_method(), "GET")
            requests.append(request.full_url)
            return Response(responses[urlsplit(request.full_url).path])

        output = io.StringIO()
        with patch("urllib.request.urlopen", side_effect=fetch), patch("time.sleep"), redirect_stdout(output):
            exec(compile(code, "public-engagement-health.yml", "exec"), {})
        return output.getvalue(), requests

    def test_scheduled_health_workflow_reports_pv_and_only_performs_gets(self):
        output, requests = self.run_health_workflow(self.payload("apology-before-incident"))
        self.assertEqual(len(requests), 5)
        self.assertIn("site=32, article=7, article last7Days=7 PV", output)
        self.assertIn("today=32 / yesterday=None / last7Days=32", output)
        self.assertIn("VERIFIED diagnostics v2", output)
        self.assertIn("'article': 'apology-before-incident'", output)
        self.assertIn("'home': 10", output)
        self.assertIn("'article': 15", output)
        self.assertNotIn("900", output)
        self.assertNotIn("500", output)
        workflow = (ROOT / ".github/workflows/public-engagement-health.yml").read_text(encoding="utf-8")
        self.assertIn("cron: '41 23 * * *'", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("group: public-pv-health-main", workflow)
        self.assertIn("cancel-in-progress: true", workflow)
        self.assertIn("workflow_run:", workflow)
        self.assertIn("workflow_dispatch:", workflow)

    def test_scheduled_health_workflow_rejects_old_browser_only_ranking_contract(self):
        payload = self.payload("apology-before-incident")
        del payload["articles"]["apology-before-incident"]["last7DaysPageViews"]
        with self.assertRaisesRegex(SystemExit, "last7DaysPageViews"):
            self.run_health_workflow(payload)

    def test_scheduled_health_workflow_requires_live_diagnostic_v2_fields(self):
        payload = self.payload("apology-before-incident")
        del payload["hourly"]
        with self.assertRaisesRegex(SystemExit, "hourly PV diagnostics missing"):
            self.run_health_workflow(payload)

    def test_scheduled_health_workflow_requires_page_kind_breakdown(self):
        payload = self.payload("apology-before-incident")
        del payload["pageViewKindTracking"]
        with self.assertRaisesRegex(SystemExit, "page-kind PV metadata missing"):
            self.run_health_workflow(payload)

if __name__=="__main__":
    unittest.main()

