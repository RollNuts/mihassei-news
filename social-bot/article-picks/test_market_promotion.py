"""Offline market promotion, source/image binding and shared-delivery regressions."""
import copy
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import test_dispatch as fixture
import test_audit_delivery as audit_fixture
import article_thread as threads
import market_promotions as market
import publish

NOW = datetime.fromisoformat("2026-11-16T09:05:00+09:00")


def episode(slug="profit-check-03", published_at="2026-11-15"):
    return {"slug": slug, "number": 3,
            "title": "増収でも利益が伸びるとは限らない。会社予想と実績を分けて読む",
            "summary": "設備需要が増えても利益が同じように増えるとは限らない。公開済みの会社予想と実績を分け、将来の判定条件を固定する。",
            "published_at": published_at,
            "sections": [{"heading": "利益の条件", "paragraphs": ["原文はこのまま保持する。"]}],
            "forecast": {"reviewed_on": "2027-05-31", "statement": "元の判定条件"}}


class MarketFixture:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.original = episode()
        self.save_source([self.original])

    def save_source(self, episodes, *, kind="analysis"):
        path = self.root / "content/serials.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps([
            {"slug": "case-files", "kind": "fiction", "episodes": []},
            {"slug": "market-lab", "kind": kind, "episodes": episodes},
        ], ensure_ascii=False), encoding="utf-8")

    def items(self):
        return market.load_market_articles(self.root)

    def candidates(self, *, state=None, blocked=None, now=NOW, days=7):
        return market.market_candidates(self.items(), state or {}, blocked or set(), now, days)


class MarketPromotionTests(MarketFixture, unittest.TestCase):
    def test_original_day_copy_and_entire_episode_fingerprint_are_preserved(self):
        item = self.items()[0]
        self.assertEqual(item["published_at"], "2026-11-15")
        self.assertNotIn("T", item["published_at"])
        self.assertEqual(item["slug"], "market-lab-profit-check-03")
        self.assertEqual(item["title"], "【市場分析｜2026-11-15】" + self.original["title"])
        self.assertEqual(item["summary"], self.original["summary"])
        expected = hashlib.sha256(json.dumps(self.original, ensure_ascii=False,
                                            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(item["episode_sha256"], expected)
        self.original["forecast"]["statement"] = "条件が変われば指紋も変わる"
        self.save_source([self.original])
        self.assertNotEqual(self.items()[0]["episode_sha256"], expected)

    def test_only_analysis_serial_is_adapted_and_duplicate_or_wrong_kind_fails(self):
        self.assertEqual(len(self.items()), 1)
        self.save_source([episode()], kind="fiction")
        with self.assertRaises(ValueError):
            self.items()
        self.save_source([episode(), episode()])
        with self.assertRaises(ValueError):
            self.items()

    def test_future_old_and_forecast_date_do_not_become_publication_time(self):
        for day, accepted in (("2026-11-17", False), ("2026-11-08", False),
                              ("2026-11-09", True), ("2026-11-16", True)):
            with self.subTest(day=day):
                self.save_source([episode(published_at=day)])
                self.assertEqual(bool(self.candidates()), accepted)
        self.save_source([episode(published_at="2026-11-16T08:00:00+09:00")])
        with self.assertRaises(ValueError):
            self.items()

    def test_calendar_window_uses_jst_and_latest_episode_first(self):
        self.save_source([episode("older-02", "2026-11-15"),
                          episode("newer-03", "2026-11-16")])
        # UTC is still the previous calendar day, but JST has reached the 16th.
        now = datetime.fromisoformat("2026-11-15T15:05:00+00:00")
        selected = self.candidates(now=now)
        self.assertEqual([item["slug"] for item in selected],
                         ["market-lab-newer-03", "market-lab-older-02"])
        with self.assertRaises(ValueError):
            self.candidates(now=NOW.replace(tzinfo=None))

    def test_permanent_slug_dedupe_covers_queued_public_and_remote_blocked(self):
        key = self.items()[0]["slug"]
        yesterday = (NOW - timedelta(days=1)).isoformat()
        for state, blocked in (({"queued": {key: {"queued_at_utc": yesterday}}}, set()),
                               ({"public_posts": {key: {"posted_at_utc": yesterday}}}, set()),
                               ({}, {key})):
            with self.subTest(state=state, blocked=blocked):
                self.assertEqual(self.candidates(state=state, blocked=blocked), [])

    def test_daily_market_cap_counts_intent_actual_send_and_manual_public_observation(self):
        previous = market.market_key("previous-02")
        yesterday = (NOW - timedelta(days=1)).isoformat()
        for state in (
            {"queued": {previous: {"queued_at_utc": NOW.isoformat()}}},
            {"queued": {previous: {"queued_at_utc": yesterday,
                                     "buffer_sent_at_at_accept": NOW.astimezone(timezone.utc).isoformat()}}},
            {"public_posts": {previous: {"posted_at_utc": NOW.isoformat()}}},
        ):
            with self.subTest(state=state):
                self.assertEqual(self.candidates(state=state), [])
        self.assertTrue(self.candidates(state={"queued": {"ordinary-news": {"queued_at_utc": NOW.isoformat()}}}))
        with self.assertRaises(ValueError):
            self.candidates(state={"queued": {previous: {"queued_at_utc": "unknown"}}})

    def test_market_and_news_canonical_urls_are_distinct_and_safe(self):
        key = market.market_key("profit-check-03")
        self.assertEqual(threads.canonical_url(key),
                         "https://veripsa.com/serials/market-lab/profit-check-03/")
        self.assertEqual(threads.canonical_url("profit-check-03"),
                         "https://veripsa.com/articles/profit-check-03/")
        self.assertTrue(market.is_market_key(key))
        for invalid in (None, "market-lab-", "market-lab-../profit", "other-profit"):
            self.assertFalse(market.is_market_key(invalid))
            with self.assertRaises(ValueError):
                market.market_episode_slug(invalid)
        with self.assertRaises(ValueError):
            market.market_key("../bad")
        self.assertFalse(threads.is_article_reply_url(
            "https://veripsa.com/articles/" + key + "/" + threads.TRACKING_QUERY, key))

    def test_shared_news_archive_is_skipped_but_forged_market_adapter_fails(self):
        news = fixture.article("ordinary-news")
        self.assertEqual(market.market_candidates([news], {}, set(), NOW), [])
        self.assertEqual(market.market_candidates([news, *self.items()], {}, set(), NOW), self.items())
        for item in ({**news, "slug": "market-lab-forged"},
                     {**news, "promotion_kind": "market_analysis"}):
            with self.subTest(item=item), self.assertRaises(ValueError):
                market.market_candidates([item], {}, set(), NOW)

    def test_market_hook_keeps_a_complete_sentence_and_never_clips_currency_units(self):
        item = self.items()[0]
        summary = "SWCCの上期営業利益は179億01百万円。" + "下期の計画について確認が必要な情報が" * 7 + "続きます。"
        body = threads.parent_text({**item, "summary": summary})
        self.assertTrue(body.endswith("SWCCの上期営業利益は179億01百万円。"))
        self.assertNotIn("…", body.split("\n\n", 1)[1])
        body = threads.parent_text({**item, "summary": "条件を省略できない数値の長い説明" * 20 + "。"})
        self.assertTrue(body.endswith("公開資料をもとに、予測の根拠と反証材料を整理しました。"))


class MarketThreadTests(MarketFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.image = self.root / market.MARKET_COVER
        self.image.parent.mkdir(parents=True)
        self.data = fixture.ArticleComicAttachmentTests.png()
        self.image.write_bytes(self.data)
        self.git("init", "-q", "--template=", ".")
        self.git("add", ".")
        self.git("-c", "user.name=Offline", "-c", "user.email=offline@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "Offline market fixture")
        patcher = patch.object(threads, "urlopen", side_effect=lambda *a, **kw: self.response())
        self.download = patcher.start()
        self.addCleanup(patcher.stop)

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True,
                              capture_output=True, text=True).stdout.strip()

    def response(self, data=None):
        result = io.BytesIO(self.data if data is None else data)
        result.status = 200
        result.headers = {"Content-Type": "image/png"}
        return result

    def prepare(self, article=None, comic=None):
        return threads.prepare_article_thread(article or self.items()[0], comic, root=self.root)

    def test_market_thread_binds_original_prose_committed_cover_and_reply_image_version(self):
        item = self.items()[0]
        thread = self.prepare(item)
        sha = hashlib.sha256(self.data).hexdigest()
        self.assertEqual(thread["article_sha256"], item["episode_sha256"])
        self.assertEqual(thread["image"]["path"], market.MARKET_COVER)
        self.assertEqual(thread["image"]["revision"], self.git("rev-parse", "HEAD"))
        self.assertEqual(thread["reply_text"], threads.canonical_url(item["slug"]) +
                         threads.TRACKING_QUERY + "&image=" + sha[:12])
        self.assertTrue(thread["parent_text"].startswith("【市場分析｜2026-11-15】"))
        self.assertNotIn("https://", thread["parent_text"])
        self.assertLessEqual(threads.x_weighted_length(thread["parent_text"]), 280)

    def test_changed_copy_fingerprint_source_or_comic_never_reaches_provider(self):
        original = self.items()[0]
        for field, value in (("title", "別の見出し"), ("summary", "別の説明"),
                             ("episode_sha256", "f" * 64), ("promotion_kind", "fiction")):
            with self.subTest(field=field):
                changed = {**original, field: value}
                with self.assertRaises(threads.ArticleImageUnavailable):
                    self.prepare(changed)
        with self.assertRaises(threads.ArticleImageUnavailable):
            self.prepare(comic={"caption": "代替漫画"})
        self.original["sections"][0]["paragraphs"][0] = "本文を差し替えた別原稿。"
        self.save_source([self.original])
        with self.assertRaises(threads.ArticleImageUnavailable):
            self.prepare(original)

    def test_changed_local_or_downloaded_cover_never_reaches_provider(self):
        self.image.write_bytes(self.data + b"changed")
        with self.assertRaises(threads.ArticleImageUnavailable):
            self.prepare()
        self.image.write_bytes(self.data)
        with patch.object(threads, "urlopen", side_effect=lambda *a, **kw: self.response(b"not-the-image")):
            with self.assertRaises(threads.ArticleImageUnavailable):
                self.prepare()

    def test_market_cover_cannot_be_rebound_to_news_or_other_raster(self):
        thread = self.prepare()
        with self.assertRaises(ValueError):
            threads.validate_thread({**thread, "reply_text": "https://veripsa.com/articles/news/" +
                                      threads.TRACKING_QUERY}, "news")
        changed = copy.deepcopy(thread)
        changed["image"]["path"] = "assets/generated/news.jpg"
        changed["image"]["url"] = threads.RAW_BASE + changed["image"]["revision"] + "/" + changed["image"]["path"]
        with self.assertRaises(ValueError):
            threads.validate_thread(changed, self.items()[0]["slug"])
        changed = {**thread, "reply_text": thread["reply_text"][:-12] + "f" * 12}
        with self.assertRaises(ValueError):
            threads.validate_thread(changed, self.items()[0]["slug"])

    def test_shared_queue_one_sends_image_parent_then_market_link_in_one_request(self):
        item = self.items()[0]
        thread = self.prepare(item)
        post = {"kind": "article", "id": "article-" + item["slug"], "article": item,
                "text": thread["parent_text"], "article_thread": thread}
        with patch.object(publish, "HERE", self.root / "social-bot"), \
             patch.object(publish, "gql", return_value={"createPost": {"post": {
                 "id": "market-receipt", "status": "sending"}}}) as provider:
            receipt = publish.queue_one("offline", "fixed-channel", post,
                                        mode="shareNow", return_post=True)
        provider.assert_called_once()
        query = provider.call_args.args[1]
        self.assertEqual(query.count("createPost("), 1)
        self.assertEqual(query.count("image: {url:"), 1)
        self.assertIn(publish.qstring(thread["reply_text"]) + ", assets: []", query)
        self.assertNotIn("/articles/market-lab-", query)
        self.assertEqual(receipt["_article_thread"], thread)

    def test_shared_delivery_audit_requires_both_market_texts_and_exact_cover(self):
        item = self.items()[0]
        thread = self.prepare(item)
        entry = {"buffer_id": "market-receipt", "queued_at_utc": NOW.isoformat(),
                 "article_thread": thread, "thread_sha256": threads.thread_digest(thread),
                 "text_sha256": threads.digest(thread["parent_text"])}
        remote = {"id": "market-receipt", "status": "sent", "text": thread["parent_text"],
                  "metadata": {"thread": [
                      {"text": thread["parent_text"], "assets": [{
                          "source": thread["image"]["url"], "mimeType": "image/png"}]},
                      {"text": thread["reply_text"], "assets": []},
                  ]}}
        observed = audit_fixture.m.reconcile(item["slug"], entry, remote)
        self.assertEqual(observed["thread_configuration"], "matched")
        self.assertNotEqual(observed.get("x_publication"), "verified")
        remote["metadata"]["thread"][1]["text"] = "https://veripsa.com/articles/other/"
        observed = audit_fixture.m.reconcile(item["slug"], entry, remote)
        self.assertEqual(observed["reason"], "thread_text_mismatch")


if __name__ == "__main__":
    unittest.main()
