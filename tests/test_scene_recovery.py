"""Offline regression tests: storage gates and real local-git publication races."""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import local_scene_photos as photos, local_scene_sync as sync
from scripts import generated_photos as generated

def article(slug="one-story"):
    return {"slug":slug,"title":"夕飯ガチャ", "summary":"家族が献立を断る",
            "paragraphs":["A", "B"],"category":"暮らし","published_at":"2026-11-03T09:00:00+09:00"}

def jpeg():
    return (b"\xff\xd8\xff\xc0\x00\x11\x08"
            + (675).to_bytes(2,"big") + (1200).to_bytes(2,"big")
            + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
            + b"\x00" * 17000 + b"\xff\xd9")

MODEL = "sd_xl_base_1.0.safetensors"

class SceneStorageTests(unittest.TestCase):
    def test_blank_model_output_is_rejected_and_real_png_is_bounded(self):
        from PIL import Image
        empty = io.BytesIO()
        Image.new("RGB", (1024,576), (0,0,0)).save(empty, format="PNG")
        with self.assertRaisesRegex(ValueError, "blank"):
            photos.png_to_jpeg(empty.getvalue())
        source = Image.effect_noise((1024,576), 50).convert("RGB")
        png = io.BytesIO()
        source.save(png, format="PNG")
        data = photos.png_to_jpeg(png.getvalue())
        self.assertLessEqual(len(data), 700*1024)
        with Image.open(io.BytesIO(data)) as result:
            self.assertEqual(result.size, (1200,675))

    def test_all_entrypoints_enforce_four_and_twelve(self):
        for main in (photos.main, sync.main):
            for args in (["--dry-run","--limit","0"],["--dry-run","--limit","5"],
                         ["--dry-run","--min-free-gb","11"]):
                with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        main(args)

    def test_archive_limit_preserves_existing_photo_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            photos.write_generated(article(), jpeg(), MODEL, root=root)
            manifest = root / "assets/generated/manifest.json"
            before = manifest.read_bytes()
            with patch.object(photos,"MAX_ARCHIVE_BYTES",len(jpeg()) + 100):
                with self.assertRaisesRegex(RuntimeError, "1.5 GiB"):
                    photos.write_generated(article("two"), jpeg(), MODEL, root=root)
            self.assertEqual(manifest.read_bytes(), before)
            self.assertFalse((root / "assets/generated/two.jpg").exists())

    def test_writer_enforces_per_image_limit_not_only_encoder(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                photos.write_generated(article(), jpeg() + b"x" * (700 * 1024), MODEL, root=Path(tmp))

    def test_corrupt_manifest_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "assets/generated/manifest.json"
            path.parent.mkdir(parents=True)
            path.write_text("{invalid")
            with self.assertRaises(ValueError):
                photos.write_generated(article(), jpeg(), MODEL, root=root)
            self.assertEqual(path.read_text(), "{invalid")

    def test_symlink_destination_is_never_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "assets/generated"
            folder.mkdir(parents=True)
            keep = root / "important.jpg"
            keep.write_bytes(b"keep")
            (folder / "one-story.jpg.tmp").symlink_to(keep)
            with self.assertRaisesRegex(RuntimeError,"symlink"):
                photos.write_generated(article(), jpeg(), MODEL, root=root)
            self.assertEqual(keep.read_bytes(), b"keep")

    def test_body_and_scene_direction_invalidate_old_images(self):
        original = article()
        for changed in ({**original,"paragraphs":["changed","ending"]},
                        {**original,"scene_prompt":"A completely different staged scene"}):
            self.assertNotEqual(generated.article_fingerprint(original), generated.article_fingerprint(changed))

    def test_generation_checks_disk_while_waiting(self):
        replies = [{"prompt_id":"job"}]
        with patch.object(photos,"request_json",side_effect=replies), \
             patch.object(photos,"check_storage",side_effect=RuntimeError("disk full")):
            with self.assertRaisesRegex(RuntimeError,"disk full"):
                photos.create_png("http://127.0.0.1:8188", article(), MODEL, 30)

    def test_local_opener_ignores_external_proxy(self):
        import urllib.request
        proxies = [h for h in photos.LOCAL_OPENER.handlers if isinstance(h, urllib.request.ProxyHandler)]
        self.assertTrue(all(not h.proxies for h in proxies))

    def test_prompt_puts_article_action_first_and_varies_camera(self):
        a = {**article(), "scene_prompt":"A solemn actor carries an empty chair through a quiet hallway."}
        self.assertTrue(photos.scene_prompt(a).startswith(a["scene_prompt"]))
        directions = {photos.scene_prompt({**a,"slug":"story-"+str(i)}).split("16:9")[0] for i in range(16)}
        self.assertGreater(len(directions), 1)


class SceneGitIntegrationTests(unittest.TestCase):
    def test_cpu_photo_transfer_preserves_verified_origin_and_model(self):
        model = "SimianLuo/LCM_Dreamshaper_v7"
        photos.write_generated(article(), jpeg(), model, root=self.repo, origin="github-cpu-lcm")
        self.outbox.mkdir(parents=True)
        self.assertEqual(sync.transfer_scenes(self.repo, self.outbox, [article()], missing_only=True),
                         [article()["slug"]])
        self.assertTrue(generated.usable_generated_scene(article(), root=self.outbox))
        record = generated.generated_manifest(self.outbox)[article()["slug"]]
        self.assertEqual((record["origin"], record["model"]), ("github-cpu-lcm", model))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.remote = self.base / "remote.git"
        self.repo = self.base / "user-repo"
        self.app = self.base / "app"
        self.outbox = self.app / "pending"
        self.env = patch.dict(os.environ, {
            "PATH":"/Library/Developer/CommandLineTools/usr/bin:"+os.environ["PATH"],
            "GIT_AUTHOR_NAME":"Scene test","GIT_AUTHOR_EMAIL":"scene@test.invalid",
            "GIT_COMMITTER_NAME":"Scene test","GIT_COMMITTER_EMAIL":"scene@test.invalid",
        })
        self.env.start()
        self.run_git("init","--bare",str(self.remote))
        self.run_git("clone",str(self.remote),str(self.repo))
        self.run_git("checkout","-b","main",cwd=self.repo)
        (self.repo/"content").mkdir()
        (self.repo/"content/articles.json").write_text(json.dumps([article()]))
        (self.repo/"assets/generated").mkdir(parents=True)
        (self.repo/"assets/generated/manifest.json").write_text('{"version":1,"images":{}}')
        (self.repo/"keep.txt").write_text("user file")
        self.run_git("add",".",cwd=self.repo)
        self.run_git("commit","-m","initial",cwd=self.repo)
        self.run_git("push","origin","main",cwd=self.repo)
        self.initial = self.run_git("rev-parse","HEAD",cwd=self.repo).stdout.strip()
        self.patches = [patch.object(sync,"ROOT",self.repo),patch.object(sync,"APP_DIR",self.app),
                        patch.object(sync,"OUTBOX",self.outbox),patch.object(sync,"REMOTES",{str(self.remote)}),
                        patch.object(sync,"check_storage")]
        for p in self.patches: p.start()
        self.args = SimpleNamespace(limit=4, min_free_gb=12, timeout=30, dry_run=False, publish=True,
                                    server="http://127.0.0.1:8188",checkpoint=MODEL,
                                    output_dir="",acknowledge_model_license=True)

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.env.stop()
        self.temp.cleanup()

    def run_git(self,*args,cwd=None):
        return subprocess.run(["git",*args],cwd=cwd or self.base,check=True,
                              capture_output=True,text=True)

    def checkout_remote(self,name):
        checkout = self.base / name
        self.run_git("clone","-b","main",str(self.remote),str(checkout))
        return checkout

    def generate_one(self,args,checkout):
        if not generated.usable_generated_scene(article(),root=checkout):
            photos.write_generated(article(),jpeg(),MODEL,root=checkout)
        return 0

    def test_partial_generation_publishes_success_preserving_user_changes(self):
        (self.repo/"keep.txt").write_text("unsaved user edit")
        def partial(args,checkout):
            self.generate_one(args,checkout)
            return 1
        with patch.object(sync,"generate",side_effect=partial):
            self.assertEqual(sync.sync(self.args),1)
        published = self.checkout_remote("verify")
        self.assertTrue(generated.usable_generated_scene(article(),root=published))
        self.assertEqual((self.repo/"keep.txt").read_text(),"unsaved user edit")
        self.assertEqual(self.run_git("rev-parse","HEAD",cwd=self.repo).stdout.strip(),self.initial)
        self.assertFalse((self.outbox/"assets/generated/one-story.jpg").exists())

    def test_failed_push_retries_next_run_without_regenerating(self):
        real_git = sync.git
        def reject(*args,**kwargs):
            if args[0]=="push":
                return subprocess.CompletedProcess(args,1,"","simulated offline")
            return real_git(*args,**kwargs)
        with patch.object(sync,"generate",side_effect=self.generate_one), patch.object(sync,"git",side_effect=reject):
            self.assertEqual(sync.sync(self.args),1)
        self.assertTrue(generated.usable_generated_scene(article(),root=self.outbox))
        def no_regenerate(args,checkout):
            self.assertTrue(generated.usable_generated_scene(article(),root=checkout))
            return 0
        with patch.object(sync,"generate",side_effect=no_regenerate):
            self.assertEqual(sync.sync(self.args),0)
        self.assertTrue(generated.usable_generated_scene(article(),root=self.checkout_remote("verify")))

    def test_concurrent_article_and_manifest_changes_are_preserved(self):
        competitor = self.checkout_remote("publisher")
        real_git = sync.git
        raced = False
        second = article("another-story")
        def race(*args,**kwargs):
            nonlocal raced
            if args[0]=="push" and not raced:
                raced = True
                (competitor/"content/articles.json").write_text(json.dumps([article(),second]))
                photos.write_generated(second,jpeg(),MODEL,root=competitor)
                self.run_git("add",".",cwd=competitor)
                self.run_git("commit","-m","concurrent article and photo",cwd=competitor)
                self.run_git("push","origin","main",cwd=competitor)
            return real_git(*args,**kwargs)
        with patch.object(sync,"generate",side_effect=self.generate_one), patch.object(sync,"git",side_effect=race):
            self.assertEqual(sync.sync(self.args),0)
        published = self.checkout_remote("verify")
        self.assertEqual(len(photos.load_articles(published)),2)
        for a in (article(),second):
            self.assertTrue(generated.usable_generated_scene(a,root=published))
        self.assertEqual(self.run_git("rev-parse","HEAD",cwd=self.repo).stdout.strip(),self.initial)

    def test_changed_story_during_generation_does_not_publish_stale_image(self):
        competitor = self.checkout_remote("publisher")
        edited = {**article(),"paragraphs":["new content","new punchline"]}
        def generate_then_edit(args,checkout):
            self.generate_one(args,checkout)
            (competitor/"content/articles.json").write_text(json.dumps([edited]))
            self.run_git("add",".",cwd=competitor)
            self.run_git("commit","-m","edit story",cwd=competitor)
            self.run_git("push","origin","main",cwd=competitor)
            return 0
        with patch.object(sync,"generate",side_effect=generate_then_edit):
            self.assertEqual(sync.sync(self.args),0)
        published = self.checkout_remote("verify")
        self.assertFalse(generated.usable_generated_scene(edited,root=published))
        self.assertFalse((published/"assets/generated/one-story.jpg").exists())
        self.assertTrue(generated.usable_generated_scene(article(),root=self.outbox))

    def test_interrupted_outbox_save_retains_and_recovers_private_checkout(self):
        transfer = sync.transfer_scenes
        def fail_save(source,target,articles,**kwargs):
            if source.name=="checkout" and generated.usable_generated_scene(article(),root=source):
                raise OSError("simulated full disk during outbox save")
            return transfer(source,target,articles,**kwargs)
        with patch.object(sync,"generate",side_effect=self.generate_one), patch.object(sync,"transfer_scenes",side_effect=fail_save):
            with self.assertRaises(OSError):
                sync.sync(self.args)
        leftovers = list(self.app.glob("sync-*/checkout"))
        self.assertEqual(len(leftovers),1)
        self.assertTrue(generated.usable_generated_scene(article(),root=leftovers[0]))
        with patch.object(sync,"generate",side_effect=self.generate_one):
            self.assertEqual(sync.sync(self.args),0)
        self.assertEqual(list(self.app.glob("sync-*")),[])
        self.assertTrue(generated.usable_generated_scene(article(),root=self.checkout_remote("verify")))

    def test_spoofed_origin_is_rejected(self):
        self.run_git("remote","set-url","origin","https://evil.invalid/RollNuts/mihassei-news",cwd=self.repo)
        with self.assertRaisesRegex(RuntimeError,"exactly"):
            sync.safe_to_publish()

    def test_combined_jpeg_budget_counts_original_outbox_and_worktree(self):
        photos.write_generated(article(),jpeg(),MODEL,root=self.repo)
        photos.write_generated(article(),jpeg(),MODEL,root=self.outbox)
        temporary = self.app/"sync-example/checkout"
        photos.write_generated(article(),jpeg(),MODEL,root=temporary)
        self.assertEqual(sync.managed_jpeg_bytes(),3*len(jpeg()))
        with patch.object(sync,"MAX_ARCHIVE_BYTES",3*len(jpeg())):
            with self.assertRaisesRegex(RuntimeError,"combined"):
                sync.require_image_budget(1)

if __name__ == "__main__":
    unittest.main()
