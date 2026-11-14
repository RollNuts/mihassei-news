"""Offline image, two-post receipt and ambiguous-submission regression tests."""
import copy
import io
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import test_dispatch as f
import article_thread as t
import publish
import dispatch as p
import test_audit_delivery as af

audit = af.m


class ArticleThreadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.story = f.article("image-story", age_hours=4)
        self.path = "social-bot/assets/article-comic-image-story.png"
        self.image = self.root / self.path
        self.image.parent.mkdir(parents=True)
        self.data = f.ArticleComicAttachmentTests.png()
        self.image.write_bytes(self.data)
        self.git("init", "-q", "--template=", ".")
        self.git("add", self.path)
        self.git("-c", "user.name=Offline", "-c", "user.email=offline@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "Offline image fixture")
        self.review = {"approved_by": "owner", "reviewed_at": "2026-11-04T00:00:00Z",
                       "article_sha256": t.article_fingerprint(self.story), "image_sha256": hashlib.sha256(self.data).hexdigest(),
                       "caption": "便利にしたら、手順が増えました。", "path": self.path}
        self.review["caption_sha256"] = t.digest(self.review["caption"])
        download = patch.object(t, "urlopen", side_effect=lambda *a, **kw: self.response())
        self.download = download.start()
        self.addCleanup(download.stop)

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True,
                              capture_output=True, text=True).stdout.strip()

    def response(self, data=None, mime="image/png"):
        response = io.BytesIO(self.data if data is None else data)
        response.status = 200
        response.headers = {"Content-Type": mime}
        return response

    def prepare(self):
        return t.prepare_article_thread(self.story, self.review, root=self.root)

    def record(self, thread, *, pending=False):
        return {"buffer_id": "" if pending else "one-receipt", "submission_pending": pending,
                "queued_at_utc": (f.NOW - timedelta(hours=3)).isoformat(),
                "submission_mode": "shareNow", "article_thread": thread,
                "thread_sha256": t.thread_digest(thread), "text_sha256": t.digest(thread["parent_text"])}

    def test_required_image_is_downloaded_at_committed_revision_and_exact_bytes(self):
        thread = self.prepare()
        self.assertEqual(thread["reply_text"], "https://veripsa.com/articles/image-story/" + t.TRACKING_QUERY)
        self.assertEqual(thread["image"]["revision"], self.git("rev-parse", "HEAD"))
        self.assertEqual(thread["image"]["sha256"], self.review["image_sha256"])
        self.download.assert_called_once()
        self.assertEqual(self.download.call_args.args[0].full_url, thread["image"]["url"])
        self.assertTrue(thread["parent_text"].startswith(self.story["title"]))
        self.assertIn(self.review["caption"], thread["parent_text"])
        self.assertNotIn("https://", thread["parent_text"])

    def test_image_download_failure_mime_or_byte_mismatch_never_calls_buffer(self):
        for response in (lambda: self.response(b"wrong"), lambda: self.response(mime="text/html"),
                         lambda: (_ for _ in ()).throw(TimeoutError("synthetic timeout"))):
            with self.subTest(response=response), patch.object(t, "urlopen", side_effect=lambda *a, **kw: response()), \
                 patch.object(publish, "HERE", self.root / "social-bot"), patch.object(publish, "gql") as provider:
                with self.assertRaises(t.ArticleImageUnavailable):
                    publish.queue_one("fake", "fixed-channel", {"kind": "article", "id": "article-image-story",
                                      "article": self.story, "comic_approval": self.review,
                                      "text": t.canonical_url("image-story")}, mode="shareNow")
                provider.assert_not_called()

    def test_one_mutation_contains_parent_image_then_direct_url_reply(self):
        thread = self.prepare()
        post = {"kind": "article", "id": "article-image-story", "article": self.story,
                "comic_approval": self.review, "article_thread": thread, "text": thread["parent_text"]}
        with patch.object(publish, "HERE", self.root / "social-bot"), \
             patch.object(publish, "gql", return_value={"createPost": {"post": {"id": "one-receipt", "status": "sending"}}}) as send:
            receipt = publish.queue_one("fake", "fixed-channel", post, mode="shareNow", return_post=True)
        send.assert_called_once()
        query = send.call_args.args[1]
        self.assertEqual(query.count("createPost("), 1)
        self.assertIn("mode: shareNow", query)
        self.assertIn("metadata: {twitter: {thread:", query)
        self.assertEqual(query.count("image: {url:"), 1)
        self.assertEqual(query.count(t.canonical_url("image-story")), 1)
        self.assertIn("text: " + publish.qstring(thread["parent_text"]) + ", assets: [{image:", query)
        self.assertIn("text: " + publish.qstring(t.canonical_url("image-story") + t.TRACKING_QUERY) + ", assets: []", query)
        self.assertNotIn("first_comment", query)
        self.assertEqual(receipt["_article_thread"], thread)
        self.assertEqual(receipt["id"], "one-receipt")

    def test_reply_keeps_real_article_image_version_for_both_photo_and_comic(self):
        # Reuse one actually accepted repository photo as a read-only fixture.
        # All copied assets/manifests and git commits live in this temporary repo.
        articles = json.loads((t.ROOT / "content/articles.json").read_text())
        source = next(a for a in articles if a.get("visual_strategy", "cpu-photo") != "illustration"
                      and t.reviewed_generated_scene(a, root=t.ROOT)
                      and t.generated_scene_entry(a, root=t.ROOT)["article_sha256"] == t.article_fingerprint(a))
        image_path = "assets/generated/" + source["slug"] + ".jpg"
        for relative in (image_path, "assets/generated/manifest.json", "content/visual_reviews.json"):
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((t.ROOT / relative).read_bytes())
        self.git("add", image_path, "assets/generated/manifest.json", "content/visual_reviews.json")
        self.git("-c", "user.name=Offline", "-c", "user.email=offline@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "Accepted photo fixture")
        photo_bytes = (self.root / image_path).read_bytes()
        review = {**self.review, "article_sha256": t.article_fingerprint(source)}
        expected = t.share_page_url(source, t.canonical_url(source["slug"]) + t.TRACKING_QUERY, root=self.root)
        image_version = hashlib.sha256(photo_bytes).hexdigest()[:12]
        self.assertTrue(expected.endswith("&image=" + image_version))
        self.assertTrue(t.reviewed_generated_scene(source, root=self.root))
        def downloaded(request, **kwargs):
            return (self.response(photo_bytes, "image/jpeg") if request.full_url.endswith(".jpg")
                    else self.response())
        with patch.object(t, "urlopen", side_effect=downloaded), \
             patch.object(publish, "HERE", self.root / "social-bot"):
            photo = t.prepare_article_thread(source, root=self.root)
            comic = t.prepare_article_thread(source, review, root=self.root)
            self.assertEqual(photo["reply_text"], expected)
            self.assertEqual(comic["reply_text"], expected)
            self.assertEqual(photo["image"]["sha256"][:12], image_version)
            self.assertNotEqual(comic["image"]["sha256"][:12], image_version)
            for thread in (photo, comic):
                self.assertIsNone(t.CAPTION_LINK.search(thread["parent_text"]))
                self.assertIn(t.TRACKING_QUERY, thread["reply_text"])
                t.validate_thread(thread, source["slug"])
            post = {"kind": "article", "id": "article-" + source["slug"], "article": source,
                    "text": comic["parent_text"], "article_thread": comic,
                    "comic_required": True, "comic_approval": review}
            with patch.object(publish, "gql", return_value={"createPost": {"post": {"id": "tracked-receipt"}}}) as send:
                self.assertEqual(publish.queue_one("fake", "fixed-channel", post, mode="shareNow"), "tracked-receipt")
            self.assertIn("text: " + publish.qstring(expected) + ", assets: []", send.call_args.args[1])
            altered = {**comic, "reply_text": expected[:-12] + "f" * 12}
            with patch.object(publish, "gql") as send, self.assertRaises(t.ArticleImageUnavailable):
                publish.queue_one("fake", "fixed-channel", {**post, "article_thread": altered}, mode="shareNow")
            send.assert_not_called()
        remote = f.remote_thread(comic, id="tracked-receipt")
        self.assertIsNone(t.remote_thread_mismatch(remote, comic))
        state = f.ledger()
        p.recover_from_buffer(state, [remote], f.NOW, {source["slug"]})
        self.assertEqual(state["queued"][source["slug"]]["buffer_id"], "tracked-receipt")
        remote["metadata"]["thread"][1]["text"] = t.canonical_url(source["slug"])
        self.assertEqual(t.remote_thread_mismatch(remote, comic), "thread_text_mismatch")

    def test_reply_url_validation_rejects_other_hosts_paths_and_unexpected_queries(self):
        thread = self.prepare()
        base = t.canonical_url(self.story["slug"])
        valid = base + t.TRACKING_QUERY
        for url in ("https://example.invalid/articles/image-story/" + t.TRACKING_QUERY,
                    "https://veripsa.com.evil.invalid/articles/image-story/" + t.TRACKING_QUERY,
                    valid.replace("https:", "http:"), valid.replace("image-story", "another-story"),
                    valid.replace("/articles/", "/share/"), valid + "&image=short", valid + "#fragment",
                    valid + "&redirect=https://example.invalid", valid.replace("utm_medium=social&", "")):
            with self.subTest(url=url), self.assertRaises(ValueError):
                t.validate_thread({**thread, "reply_text": url}, self.story["slug"])
        # Historical canonical-only thread evidence remains readable; the new
        # producer and shareNow re-preparation always require the tracked URL.
        self.assertFalse(t.is_article_reply_url(base, self.story["slug"]))
        t.validate_thread({**thread, "reply_text": base}, self.story["slug"])

    def test_parent_reuses_title_and_safe_lead_or_summary_with_no_urls_or_tags(self):
        story = {**self.story, "x_share_lead": "会議を短くしたかっただけなのに、最後の挨拶だけ1時間でした。"}
        parent = t.parent_text(story)
        self.assertTrue(parent.startswith(story["title"] + "\n\n"))
        self.assertIn(story["x_share_lead"], parent)
        for lead in ("短い", "詳しくは https://example.invalid を見てください。", "#未発生新聞 便利な記事の紹介です。"):
            parent = t.parent_text({**story, "x_share_lead": lead})
            self.assertIn(story["summary"], parent)
            self.assertNotIn("https://", parent)
            self.assertNotIn("#", parent)
        self.assertLessEqual(t.x_weighted_length(t.parent_text({**story, "title": "長い見出し" * 80})), 280)
        with self.assertRaises(ValueError):
            t.parent_text({**story, "title": "https://example.invalid/ が見出し"})

    def test_parent_rejects_bare_domains_in_title_or_hook_including_japanese_suffix(self):
        for url in ("veripsa.com", "veripsa.com/articles/story/", "veripsa.comで読む", "www.veripsa.com"):
            with self.subTest(url=url):
                self.assertIsNotNone(t.CAPTION_LINK.search(url))
                with self.assertRaises(ValueError):
                    t.parent_text({**self.story, "title": "掲載先は" + url})
                with self.assertRaises(ValueError):
                    t.parent_text(self.story, "この記事を" + url + " から読みます。")
                evidence = f.synthetic_thread(self.story)
                evidence["parent_text"] = self.story["title"] + "\n\n" + url
                with self.assertRaises(ValueError):
                    t.validate_thread(evidence, self.story["slug"])

    def test_new_pending_normal_record_binds_exact_thread_and_never_retries_partials(self):
        thread = self.prepare()
        record = self.record(thread, pending=True)
        state = f.ledger(**{"image-story": copy.deepcopy(record)})
        p.validate_state(state)
        attempted = datetime.fromisoformat(record["queued_at_utc"])
        remote = f.remote_thread(thread, id="recovered-id", createdAt=(attempted + timedelta(seconds=1)).isoformat())
        cases = []
        for change in ("missing_reply", "image_changed", "parent_changed"):
            bad = copy.deepcopy(remote)
            if change == "missing_reply": bad["metadata"]["thread"].pop()
            if change == "image_changed": bad["metadata"]["thread"][0]["assets"][0]["source"] += "?different"
            if change == "parent_changed": bad["text"] += "changed"
            cases.append([bad])
        cases.append([remote, {**remote, "id": "duplicate-id"}])
        for rows in cases:
            pending = f.ledger(**{"image-story": copy.deepcopy(record)})
            p.recover_article_threads(pending, rows, f.NOW)
            self.assertTrue(pending["queued"]["image-story"]["submission_pending"])
            self.assertEqual(p.buffer_delivery_spacing_hold([], pending, f.NOW, 2), "unverified_article_submission")
            self.assertIsNone(p.decide([self.story], {}, pending, rows, f.CONFIG, f.NOW)[0])
        p.recover_article_threads(state, [remote], f.NOW)
        recovered = state["queued"]["image-story"]
        self.assertEqual(recovered["buffer_id"], "recovered-id")
        self.assertFalse(recovered["submission_pending"])
        self.assertEqual(recovered["article_thread"], thread)
        self.assertEqual(recovered["thread_delivery"], "unverified")

    def test_reply_url_recovers_slug_and_provider_assets_without_inventing_image_hash(self):
        thread = self.prepare()
        remote = f.remote_thread(thread, id="recovered-id", createdAt=(f.NOW - timedelta(hours=3)).isoformat())
        state = f.ledger()
        self.assertEqual(p.recover_from_buffer(state, [remote], f.NOW, {"image-story"}), ["image-story"])
        saved = state["queued"]["image-story"]
        self.assertEqual(saved["buffer_id"], "recovered-id")
        self.assertEqual(saved["text_sha256"], t.digest(thread["parent_text"]))
        self.assertEqual(saved["buffer_thread_at_recovery"][0]["assets"][0]["source"], thread["image"]["url"])
        self.assertNotIn("article_thread", saved)
        self.assertIsNone(p.decide([self.story], {}, f.ledger(), [remote], f.CONFIG, f.NOW)[0])

    def test_audit_preserves_parent_status_and_marks_reply_publication_unverified(self):
        thread = self.prepare()
        record = self.record(thread)
        remote = f.remote_thread(thread, id="one-receipt", sentAt=(f.NOW - timedelta(hours=2)).isoformat())
        row = audit.reconcile("image-story", record, remote)
        self.assertEqual(row["buffer_status"], "sent")
        self.assertEqual(row["thread_configuration"], "matched")
        self.assertEqual(row["thread_publication"], "unverified")
        self.assertNotIn("reply_id", row)
        partial = copy.deepcopy(remote)
        partial["metadata"]["thread"].pop()
        row = audit.reconcile("image-story", record, partial)
        self.assertEqual(row["buffer_status"], "sent")
        self.assertEqual(row["thread_publication"], "unverified")
        self.assertEqual(row["provider_status_observed"], "sent")
        self.assertEqual(row["reason"], "thread_items_missing_or_partial")
        report = audit.build(f.ledger(**{"image-story": record}), [partial], f.NOW)
        self.assertEqual(report["observed"]["delivery_problems"], 1)
        pending = self.record(thread, pending=True)
        selected = audit.selected_entries(f.ledger(**{"image-story": pending}), f.NOW + timedelta(days=14))
        self.assertEqual(len(selected), 1)
        self.assertTrue(audit.reconcile("image-story", pending, None)["submission_pending"])

    def test_lost_comic_intent_or_misclassified_normal_receipt_never_resends_thread(self):
        thread = self.prepare()
        remote = f.remote_thread(thread, id="existing-comic", createdAt=(f.NOW - timedelta(hours=3)).isoformat(),
                                 sentAt=(f.NOW - timedelta(hours=3)).isoformat())
        (self.root / "content").mkdir()
        (self.root / "content/articles.json").write_text(json.dumps([self.story]))
        config = {**f.CONFIG, "comic_attachments": {"image-story": self.review}}
        for state in (f.ledger(), f.ledger(**{"image-story": f.recorded("existing-comic")})):
            for partial in (False, True):
                observed = copy.deepcopy(remote)
                if partial:
                    observed["text"] = "改稿前の親本文。"
                    observed["metadata"]["thread"] = observed["metadata"]["thread"][:1]
                with self.subTest(partial=partial, state=state), \
                     patch.object(p, "ROOT", self.root), patch.object(publish, "HERE", self.root / "social-bot"), \
                     patch.object(p, "datetime", wraps=datetime) as clock, \
                     patch.object(p, "read_json", side_effect=lambda path: copy.deepcopy(config if path.name == "config.json" else state)), \
                     patch.object(p, "delivery_retry_time", return_value=None), \
                     patch.object(p, "connected_channel", return_value=("org", f.CONFIG["channel_id"])), \
                     patch.object(p, "list_existing", return_value=[observed]), \
                     patch.object(publish, "remote_asset_available", return_value=True), \
                     patch.object(p, "save_state") as save, patch.object(p, "queue_one") as send, \
                     patch.dict(os.environ, {"BUFFER_API_KEY": "fake", "GITHUB_STEP_SUMMARY": "", "PATH": os.environ.get("PATH", "")}, clear=True):
                    clock.now.return_value = f.NOW
                    self.assertEqual(p.main(), 0)
                    send.assert_not_called()
                    held = save.call_args.args[0]["comic_posts"]["image-story"]
                    self.assertTrue(held["submission_pending"])
                    self.assertEqual(held["source"], "comic_recovery_unverified")
                    self.assertEqual(held["observed_buffer_ids"], ["existing-comic"])
                    self.assertEqual(held["expected_parent_sha256"], t.digest(thread["parent_text"]))
                    self.assertEqual(held["observed_parent_sha256_by_buffer_id"], {"existing-comic": t.digest(observed["text"])})
                    p.validate_state(save.call_args.args[0])

    def test_partial_normal_parent_or_fixed_image_source_survives_lost_intent(self):
        thread = f.synthetic_thread(self.story)
        for changed_parent in (False, True):
            remote = f.remote_thread(thread, id="existing-parent", status="error",
                                     createdAt=(f.NOW - timedelta(hours=3)).isoformat())
            remote["metadata"]["thread"].pop()
            if changed_parent:
                remote["text"] = "改稿前の親本文。"
                remote["metadata"]["thread"][0]["text"] = remote["text"]
            state = f.ledger()
            selected, _, _ = p.decide([self.story], {}, state, [remote], f.CONFIG, f.NOW)
            self.assertIsNone(selected)
            saved = state["queued"]["image-story"]
            self.assertEqual(saved["buffer_id"], "existing-parent")
            self.assertEqual(saved["thread_delivery"], "unverified")
            self.assertNotIn("article_thread", saved)
        spoof = copy.deepcopy(remote)
        spoof["metadata"]["thread"][0]["assets"][0]["source"] += "?spoof"
        state = f.ledger()
        self.assertIsNotNone(p.decide([self.story], {}, state, [spoof], f.CONFIG, f.NOW)[0])
        self.assertEqual(state["queued"], {})

    def test_partial_sent_thread_is_rechecked_after_cache_interval(self):
        thread = self.prepare()
        state = f.ledger(**{"image-story": self.record(thread)})
        remote = f.remote_thread(thread, id="one-receipt", sentAt=(f.NOW - timedelta(hours=2)).isoformat())
        matched = audit.build(state, [remote], f.NOW)
        self.assertIn("image-story", audit.reusable_observations(state, matched, f.NOW + timedelta(hours=7)))
        remote["metadata"]["thread"].pop()
        partial = audit.build(state, [remote], f.NOW)
        self.assertIn("image-story", audit.reusable_observations(state, partial, f.NOW + timedelta(hours=5)))
        self.assertNotIn("image-story", audit.reusable_observations(state, partial, f.NOW + timedelta(hours=7)))

    def test_partial_audit_keeps_safe_provider_error_and_sending_times(self):
        thread = self.prepare()
        record = self.record(thread)
        remote = f.remote_thread(thread, id="one-receipt", status="error", createdAt=f.NOW.isoformat(),
                                 error={"message": "Reply image rejected", "supportUrl": "https://support.buffer.com/example", "rawError": "never-record"})
        remote["metadata"]["thread"].pop()
        row = audit.reconcile("image-story", record, remote)
        self.assertEqual(row["buffer_status"], "error")
        self.assertEqual(row["thread_publication"], "unverified")
        self.assertEqual(row["buffer_error_message"], "Reply image rejected")
        self.assertEqual(row["buffer_error_support_url"], "https://support.buffer.com/example")
        self.assertEqual(row["buffer_created_at_utc"], f.NOW.astimezone(af.timezone.utc).isoformat())
        self.assertNotIn("never-record", json.dumps(row))
        report = audit.build(f.ledger(**{"image-story": record}), [remote], f.NOW)
        self.assertEqual(report["observed"]["delivery_problems"], 1)
        remote.update(status="sending", createdAt=(f.NOW - timedelta(minutes=5)).isoformat(), error=None)
        report = audit.build(f.ledger(**{"image-story": record}), [remote], f.NOW)
        self.assertEqual(report["observed"]["delivery_in_progress"], 1)
        self.assertEqual(report["items"][0]["buffer_status"], "sending")

    def test_unknown_acceptance_saves_normal_intent_before_mutation_and_blocks_resend(self):
        # Use a reviewed comic image through the ordinary lane to isolate the
        # durable state behaviour from the independent source-selection tests.
        (self.root / "content").mkdir()
        (self.root / "content/articles.json").write_text(json.dumps([self.story]))
        prepared = self.prepare()
        persisted = {}
        def save(state):
            persisted.clear()
            persisted.update(copy.deepcopy(state))
        with patch.object(p, "ROOT", self.root), patch.object(p, "datetime", wraps=datetime) as clock, \
             patch.object(p, "read_json", side_effect=lambda path: copy.deepcopy(f.CONFIG if path.name == "config.json" else f.ledger())), \
             patch.object(p, "delivery_retry_time", return_value=None), \
             patch.object(p, "connected_channel", return_value=("org", f.CONFIG["channel_id"])), \
             patch.object(p, "list_existing", return_value=[]), patch.object(p, "save_state", side_effect=save), \
             patch.object(p, "prepare_article_thread", return_value=prepared), \
             patch.object(p, "queue_one", side_effect=RuntimeError("synthetic unknown acceptance")) as send, \
             patch.dict(os.environ, {"BUFFER_API_KEY": "fake", "X_TRIGGER_EVENT": "schedule", "GITHUB_STEP_SUMMARY": ""}, clear=True):
            clock.now.return_value = f.NOW
            with self.assertRaisesRegex(RuntimeError, "unknown acceptance"):
                p.main()
        send.assert_called_once()
        intent = persisted["queued"]["image-story"]
        self.assertTrue(intent["submission_pending"])
        self.assertEqual(intent["buffer_id"], "")
        self.assertEqual(intent["article_thread"], prepared)
        self.assertEqual(p.buffer_delivery_spacing_hold([], persisted, f.NOW + timedelta(days=2), 2), "unverified_article_submission")


if __name__ == "__main__":
    unittest.main()
