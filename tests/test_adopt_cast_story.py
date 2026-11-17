"""One real Git draft becomes one local proposal; no external Git/API writes."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import adopt_cast_story as adoption

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 17, 2, 3, 4, tzinfo=timezone.utc)


class CastStoryAdoptionTests(unittest.TestCase):
    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True,
                              env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}).stdout.decode().strip()

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve() / "checkout"
        self.root.mkdir()
        (self.root / "content").mkdir()
        for name in ("photo_issues.json", "photo_stories.json", "articles.json"):
            shutil.copyfile(ROOT / "content" / name, self.root / "content" / name)
        # Fixture clock is fixed at NOW; exclude later real releases from this historical checkout.
        # Current production issues remain validated by the live-site and photo-issue gates.
        catalog_path = self.root / "content/photo_issues.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["issues"] = [issue for issue in catalog["issues"]
                             if issue["state"] != "published" or
                             datetime.fromisoformat(issue["published_at"].replace("Z", "+00:00")) <= NOW]
        catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # Read-only fixture bytes are hard-linked; these tests never edit images.
        for name in ("assets", "maintenance"):
            shutil.copytree(ROOT / name, self.root / name, copy_function=os.link)
        self.document = self.root / "content/photo_stories.json"
        self.initial = json.loads(self.document.read_bytes())
        # Candidate/guard tests start before this fixed JST publication day.
        # Later real daily posts must not turn every case into a duplicate retry;
        # duplicate cases below insert their own explicit same-day record.
        fixture_day = NOW.astimezone(adoption.JST).date()
        self.initial["stories"] = [story for story in self.initial["stories"]
                                   if datetime.fromisoformat(story["published_at"].replace("Z", "+00:00"))
                                   .astimezone(adoption.JST).date() < fixture_day]
        self.original = (json.dumps(self.initial, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.document.write_bytes(self.original)
        self.model = "nagino-saya"
        self.source = copy.deepcopy(self.initial["stories"][0]["source"])
        self.draft = {"schema_version": 1, "model_id": self.model, "name": "凪野さや",
                      "created_date": "2026-11-17", "source_chat_url": adoption.MODEL_CHAT_URLS[self.model],
                      "photo_reference": self.source,
                      "post_text": "今日は風が冷たくて。\n\n帰り道で温かいお茶を買いました。",
                      "spoken_line": "ちょっと、ひと休み。", "future_idea": None}
        self.draft_path = self.root / "drafts/cast-voices/nagino-saya.json"
        self.draft_path.parent.mkdir(parents=True)
        self.git("init", "-q", "-b", "cast-source-drafts")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        remote = Path(folder.name) / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True, capture_output=True)
        self.git("remote", "add", "origin", str(remote))
        self.commit = self.commit_draft(self.draft)

    def commit_draft(self, draft, *, other=False):
        path = self.draft_path.with_name("other-person.json") if other else self.draft_path
        path.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.git("add", str(path.relative_to(self.root)))
        self.git("commit", "-q", "--allow-empty", "-m", "Creator fixture")
        self.git("push", "-q", "-u", "origin", "HEAD:cast-source-drafts")
        return self.git("rev-parse", "HEAD")

    def run_adoption(self, **kwargs):
        return adoption.adopt(self.model, self.commit, root=self.root, now=NOW, **kwargs)

    def write_guards(self, effect=None):
        called = False
        def once():
            nonlocal called
            if not called:
                called = True
                if effect is not None:
                    effect()
        guard = patch.object(adoption, "check_cloud_images_allowed", side_effect=once)
        disk = patch.object(adoption.shutil, "disk_usage", return_value=SimpleNamespace(free=20 * 1024**3))
        return guard, disk

    def test_exact_git_draft_is_a_read_only_candidate_with_original_text_and_real_time(self):
        with patch.object(adoption, "check_cloud_images_allowed") as guard:
            result = self.run_adoption()
        guard.assert_not_called()
        self.assertEqual(result["status"], "candidate")
        candidate = result["candidate"]
        self.assertEqual(candidate["text"], self.draft["post_text"])
        self.assertEqual(candidate["published_at"], "2026-11-17T11:03:04+09:00")
        self.assertEqual(candidate["setting"], {"year": 2031, "month": 11})
        self.assertEqual(candidate["source"], self.source)
        self.assertEqual(self.document.read_bytes(), self.original)
        for private in ("spoken_line", "source_chat_url", "future_idea", "raw_utf8"):
            self.assertNotIn(private, candidate)

    def test_explicit_write_appends_once_and_preserves_all_eight_original_records_bytes(self):
        guard, disk = self.write_guards()
        with guard as checked, disk:
            result = self.run_adoption(write=True)
        self.assertEqual(checked.call_count, 2)
        self.assertEqual(result["status"], "staged")
        written = self.document.read_bytes()
        self.assertTrue(written.startswith(self.original[:self.original.rfind(b"]")].rstrip()))
        self.assertEqual(json.loads(written)["stories"][:-1], self.initial["stories"])
        self.assertEqual(json.loads(written)["stories"][-1], result["candidate"])
        self.assertEqual(list(self.document.parent.glob(".photo-stories-*")), [])

    def test_same_model_text_is_skipped_across_new_commit_metadata_and_existing_story_id(self):
        old = copy.deepcopy(self.initial["stories"][0])
        old.update(id="already-used-under-a-different-id", text=self.draft["post_text"])
        self.document.write_text(json.dumps({**self.initial, "stories": [*self.initial["stories"], old]}))
        newer = {**self.draft, "future_idea": "新しい題材だけを追記。"}
        self.commit = self.commit_draft(newer)
        before = self.document.read_bytes()
        with patch.object(adoption, "check_cloud_images_allowed") as guard:
            result = self.run_adoption(write=True)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["existing_story_id"], old["id"])
        guard.assert_not_called()
        self.assertEqual(self.document.read_bytes(), before)

    def test_same_jst_day_retry_skips_even_new_text_and_preserves_the_existing_id(self):
        for stamp in ("2026-11-17T00:30:00+09:00", "2026-11-16T15:30:00+00:00",
                      "2026-11-17T14:21:36+09:00"):
            with self.subTest(stamp=stamp):
                existing = copy.deepcopy(self.initial["stories"][0])
                existing.update(id="already-posted-today", published_at=stamp,
                                text="今日はすでに公開した、別の原文です。")
                self.document.write_text(json.dumps({**self.initial, "stories": [*self.initial["stories"], existing]}))
                before = self.document.read_bytes()
                with patch.object(adoption, "check_cloud_images_allowed") as guard:
                    result = self.run_adoption(write=True)
                self.assertEqual(result["status"], "skipped")
                self.assertEqual(result["existing_story_id"], existing["id"])
                self.assertIn("actual JST publication day", result["reason"])
                guard.assert_not_called()
                self.assertEqual(self.document.read_bytes(), before)

    def test_next_jst_day_creates_a_new_candidate_without_redating_the_old_post(self):
        existing = copy.deepcopy(self.initial["stories"][0])
        existing.update(id="yesterday-in-jst", published_at="2026-11-17T14:59:59+00:00",
                        text="日付が変わる直前に書いた原文です。")
        self.document.write_text(json.dumps({**self.initial, "stories": [*self.initial["stories"], existing]}))
        before = self.document.read_bytes()
        result = adoption.adopt(self.model, self.commit, root=self.root,
                                now=datetime(2026, 11, 17, 15, tzinfo=timezone.utc))
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["candidate"]["published_at"], "2026-11-18T00:00:00+09:00")
        self.assertEqual(result["candidate"]["text"], self.draft["post_text"])
        self.assertEqual(self.document.read_bytes(), before)

    def test_daily_skip_does_not_bypass_the_pinned_creator_identity(self):
        existing = copy.deepcopy(self.initial["stories"][0])
        existing.update(id="already-posted-today", published_at=NOW.isoformat(),
                        text="先に公開してある、本人の近況です。")
        self.document.write_text(json.dumps({**self.initial, "stories": [*self.initial["stories"], existing]}))
        before = self.document.read_bytes()
        self.commit = self.commit_draft({**self.draft, "name": "別の人物"})
        with self.assertRaisesRegex(ValueError, "fixed model"):
            self.run_adoption(write=True)
        self.assertEqual(self.document.read_bytes(), before)

    def test_description_only_photo_is_pending_and_never_substituted_or_written(self):
        self.commit = self.commit_draft({**self.draft, "photo_reference": {"description": "新しい屋内プールの写真。SHAはまだ不明。"}})
        with patch.object(adoption, "check_cloud_images_allowed") as guard:
            result = self.run_adoption(write=True)
        self.assertEqual(result["status"], "pending")
        self.assertIn("no photo was substituted", result["reason"])
        self.assertNotIn("candidate", result)
        guard.assert_not_called()
        self.assertEqual(self.document.read_bytes(), self.original)

    def test_fixed_identity_chat_date_size_and_partial_photo_reference_are_checked(self):
        cases = [("model_id", "shioba-rio"), ("name", "汐羽りお"),
                 ("source_chat_url", adoption.MODEL_CHAT_URLS["shioba-rio"]),
                 ("created_date", "2026-11-18"), ("future_idea", "大" * adoption.MAX_DRAFT_BYTES),
                 ("photo_reference", {"issue_slug": self.source["issue_slug"]})]
        for field, value in cases:
            with self.subTest(field=field):
                self.commit = self.commit_draft({**self.draft, field: value})
                with self.assertRaises(ValueError):
                    self.run_adoption()
        self.assertEqual(self.document.read_bytes(), self.original)

    def test_wrong_model_unpublished_held_and_changed_photo_hash_cannot_be_adopted(self):
        catalog_path = self.root / "content/photo_issues.json"
        original_catalog = catalog_path.read_bytes()
        for failure in ("wrong-model", "unpublished", "held", "hash"):
            with self.subTest(failure=failure):
                catalog_path.write_bytes(original_catalog)
                draft = copy.deepcopy(self.draft)
                catalog = json.loads(original_catalog)
                issue = next(issue for issue in catalog["issues"] if issue["slug"] == self.source["issue_slug"])
                if failure == "wrong-model":
                    foreign = next(post for post in self.initial["stories"] if post["model_id"] != self.model)
                    draft["photo_reference"] = foreign["source"]
                elif failure == "unpublished":
                    issue["state"] = "draft"
                elif failure == "held":
                    issue["generation_holds"] = {"01": {"reason": "fixture hold"}}
                else:
                    draft["photo_reference"]["image_sha256"] = "0" * 64
                catalog_path.write_text(json.dumps(catalog, ensure_ascii=False))
                self.commit = self.commit_draft(draft)
                with self.assertRaises(ValueError):
                    self.run_adoption()
        self.assertEqual(self.document.read_bytes(), self.original)

    def test_selected_draft_changed_during_guard_refuses_atomic_write(self):
        guard, disk = self.write_guards(lambda: self.commit_draft({**self.draft, "post_text": "本人の新しい返答へ更新しました。"}))
        with guard, disk, self.assertRaisesRegex(RuntimeError, "source draft changed"):
            self.run_adoption(write=True)
        self.assertEqual(self.document.read_bytes(), self.original)
        self.assertEqual(list(self.document.parent.glob(".photo-stories-*")), [])

    def test_another_person_new_commit_does_not_block_the_selected_unchanged_blob(self):
        guard, disk = self.write_guards(lambda: self.commit_draft({"other": "person updated"}, other=True))
        with guard, disk:
            result = self.run_adoption(write=True)
        self.assertEqual(result["status"], "staged")
        self.assertEqual(result["source_commit"], self.commit)
        self.assertEqual(json.loads(self.document.read_bytes())["stories"][-1]["text"], self.draft["post_text"])

    def test_changed_existing_input_is_preserved_without_partial_story_append(self):
        newer = self.original + b"\n"
        guard, disk = self.write_guards(lambda: self.document.write_bytes(newer))
        with guard, disk, self.assertRaisesRegex(RuntimeError, "inputs changed"):
            self.run_adoption(write=True)
        self.assertEqual(self.document.read_bytes(), newer)
        self.assertEqual(list(self.document.parent.glob(".photo-stories-*")), [])

    def test_unavailable_cloud_guard_or_low_disk_blocks_write(self):
        with patch.object(adoption, "check_cloud_images_allowed", side_effect=RuntimeError("guard unavailable")), \
             self.assertRaisesRegex(RuntimeError, "guard unavailable"):
            self.run_adoption(write=True)
        with patch.object(adoption, "check_cloud_images_allowed"), \
             patch.object(adoption.shutil, "disk_usage", return_value=SimpleNamespace(free=0)), \
             self.assertRaisesRegex(RuntimeError, "12 GiB"):
            self.run_adoption(write=True)
        self.assertEqual(self.document.read_bytes(), self.original)

    def test_commit_must_be_full_real_and_on_the_source_branch_with_unchanged_selected_file(self):
        for commit in (self.commit[:8], "0" * 40):
            with self.subTest(commit=commit), self.assertRaises((ValueError, RuntimeError)):
                adoption.adopt(self.model, commit, root=self.root, now=NOW)
        self.commit_draft({**self.draft, "post_text": "本人の次の未掲載原稿です。"})
        with self.assertRaisesRegex(ValueError, "draft changed"):
            self.run_adoption()

    def test_held_write_lock_refuses_a_second_writer_without_touching_the_document(self):
        with adoption._write_lock(self.root), \
             patch.object(adoption, "check_cloud_images_allowed") as guard, \
             self.assertRaisesRegex(RuntimeError, "write is in progress"):
            self.run_adoption(write=True)
        guard.assert_not_called()
        self.assertEqual(self.document.read_bytes(), self.original)

    def test_stop_during_source_refresh_blocks_the_final_replace(self):
        original_git = adoption._git
        refreshed = False
        def git(root, *args):
            nonlocal refreshed
            result = original_git(root, *args)
            if args[0] == "fetch":
                refreshed = True
            return result
        def guard():
            if refreshed:
                raise RuntimeError("cloud image workflow stopped during fetch")
        with patch.object(adoption, "_git", side_effect=git), \
             patch.object(adoption, "check_cloud_images_allowed", side_effect=guard) as checked, \
             patch.object(adoption.shutil, "disk_usage", return_value=SimpleNamespace(free=20 * 1024**3)), \
             self.assertRaisesRegex(RuntimeError, "stopped during fetch"):
            self.run_adoption(write=True)
        self.assertEqual(checked.call_count, 2)
        self.assertEqual(self.document.read_bytes(), self.original)
        self.assertEqual(list(self.document.parent.glob(".photo-stories-*")), [])

    def test_existing_jpeg_budget_cannot_be_bypassed_by_story_staging(self):
        guard, disk = self.write_guards()
        # The normal published-photo gate already enforces this budget; also
        # exercise the final write gate after a budget change during refresh.
        original_git = adoption._git
        refreshed = False
        def git(root, *args):
            nonlocal refreshed
            result = original_git(root, *args)
            if args[0] == "fetch":
                refreshed = True
            return result
        actual_bytes = adoption.archive_bytes(self.root)
        with guard, disk, patch.object(adoption, "_git", side_effect=git), \
             patch.object(adoption, "archive_bytes", side_effect=lambda _root: adoption.MAX_ARCHIVE_BYTES + 1 if refreshed else actual_bytes), \
             self.assertRaisesRegex(RuntimeError, "storage protection changed"):
            self.run_adoption(write=True)
        self.assertEqual(self.document.read_bytes(), self.original)

    def test_edit_during_final_cloud_guard_is_preserved_and_temporary_file_removed(self):
        newer = self.original + b"\n"
        checks = 0
        def guard():
            nonlocal checks
            checks += 1
            if checks == 2:
                self.document.write_bytes(newer)
        with patch.object(adoption, "check_cloud_images_allowed", side_effect=guard), \
             patch.object(adoption.shutil, "disk_usage", return_value=SimpleNamespace(free=20 * 1024**3)), \
             self.assertRaisesRegex(RuntimeError, "inputs changed"):
            self.run_adoption(write=True)
        self.assertEqual(checks, 2)
        self.assertEqual(self.document.read_bytes(), newer)
        self.assertEqual(list(self.document.parent.glob(".photo-stories-*")), [])

    def test_rewritten_source_branch_with_identical_blob_is_still_refused(self):
        original_blob = self.git("rev-parse", self.commit + ":drafts/cast-voices/nagino-saya.json")
        def rewrite():
            self.git("checkout", "-q", "--orphan", "rewritten-source")
            self.git("commit", "-q", "-m", "Unrelated source history, identical file")
            self.git("push", "-q", "--force", "origin", "HEAD:cast-source-drafts")
        guard, disk = self.write_guards(rewrite)
        with guard, disk, self.assertRaisesRegex(RuntimeError, "merge-base"):
            self.run_adoption(write=True)
        self.assertEqual(self.git("rev-parse", "HEAD:drafts/cast-voices/nagino-saya.json"), original_blob)
        self.assertEqual(self.document.read_bytes(), self.original)
        self.assertEqual(list(self.document.parent.glob(".photo-stories-*")), [])


if __name__ == "__main__":
    unittest.main()
