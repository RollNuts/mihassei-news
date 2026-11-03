"""No-network unit tests for the optional free public GitHub CPU generator."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import build, github_cpu_scenes as cpu, local_scene_photos
from scripts.generated_photos import article_fingerprint, generated_scene_entry, usable_generated_scene


def article():
    return {
        "slug": "family-dinner-gacha-243-rejections",
        "title": "夕飯ガチャ243回、家族は全部却下",
        "summary": "家族が献立を全て却下",
        "category": "暮らし",
    }


def jpeg_header_fixture():
    # Data-only JPEG header fixture for existing pure-stdlib image integrity
    # parser, not a real or publishable generated picture.
    sof = (b"\xff\xd8\xff\xc0\x00\x11\x08"
           + (675).to_bytes(2, "big") + (1200).to_bytes(2, "big")
           + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00")
    return sof + b"\x00" * 17000 + b"\xff\xd9"


class FreeCpuImageTests(unittest.TestCase):
    def test_cpu_job_cannot_run_outside_bounded_public_main_schedule(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(cpu.permitted_ci())
        valid = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": "RollNuts/mihassei-news",
            "GITHUB_REF": "refs/heads/main",
            "RUNNER_OS": "Linux",
            "GITHUB_EVENT_NAME": "schedule",
            "VERIPSA_ENABLE_FREE_CPU": "1",
        }
        with patch.dict(os.environ, valid, clear=True):
            self.assertTrue(cpu.permitted_ci())
            for key, value in (
                ("GITHUB_REPOSITORY", "another/private-fork"),
                ("GITHUB_REF", "refs/pull/7/merge"),
                ("RUNNER_OS", "macOS"),
                ("GITHUB_EVENT_NAME", "pull_request"),
                ("VERIPSA_ENABLE_FREE_CPU", "0"),
            ):
                with patch.dict(os.environ, {**valid, key: value}, clear=True):
                    self.assertFalse(cpu.permitted_ci())

    def test_dry_run_never_downloads_or_uploads(self):
        output = io.StringIO()
        with patch.object(cpu, "load_articles", return_value=[article()]), \
             patch.object(cpu, "load_pipeline", side_effect=AssertionError("download attempted")), \
             patch.object(cpu, "write_generated", side_effect=AssertionError("write attempted")), \
             contextlib.redirect_stdout(output):
            self.assertEqual(cpu.main(["--limit", "1", "--dry-run"]), 0)
        self.assertIn("family-dinner", output.getvalue())
        self.assertIn("capsule lottery machine", output.getvalue())

    def test_cpu_scene_verified_by_actual_site_builder_and_source_restricted(self):
        a = article()
        data = jpeg_header_fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local_scene_photos.write_generated(
                a, data, cpu.MODEL, origin="github-cpu-lcm", root=root,
            )
            self.assertTrue(usable_generated_scene(a, root=root))
            record = generated_scene_entry(a, root=root)
            self.assertEqual(record["origin"], "github-cpu-lcm")
            self.assertEqual(record["model"], cpu.MODEL)
            self.assertEqual(record["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(record["article_sha256"], article_fingerprint(a))
            with patch.object(build, "BASE", root):
                self.assertEqual(build.editorial_image(a), "/generated/" + a["slug"] + ".jpg")
                self.assertEqual(build.social_image(a), "/generated/" + a["slug"] + ".jpg")
                self.assertIn("再現イメージ（合成）", build.photo_credit(a))
            manifest = root / "assets/generated/manifest.json"
            original = json.loads(manifest.read_text(encoding="utf-8"))
            for origin, model in (
                ("expensive-remote-image-api", cpu.MODEL),
                ("github-cpu-lcm", "unknown/unreviewed"),
            ):
                document = json.loads(json.dumps(original))
                document["images"][a["slug"]]["origin"] = origin
                document["images"][a["slug"]]["model"] = model
                manifest.write_text(json.dumps(document), encoding="utf-8")
                self.assertFalse(usable_generated_scene(a, root=root))
            manifest.write_text(json.dumps(original), encoding="utf-8")
            self.assertFalse(usable_generated_scene({**a, "summary": "修正済み"}, root=root))
            with self.assertRaises(ValueError):
                local_scene_photos.write_generated(
                    a, data, "unauthorized/model", origin="github-cpu-lcm", root=root
                )

    def test_no_remote_inference_pipe_and_stable_scene_seed(self):
        a = article()
        fake_image = SimpleNamespace(save=Mock())
        pipeline = Mock(return_value=SimpleNamespace(images=[fake_image], nsfw_content_detected=[False]))
        pipeline.tokenizer = None
        gen = Mock()
        gen.manual_seed.return_value = gen
        fake_torch = SimpleNamespace(Generator=Mock(return_value=gen), inference_mode=contextlib.nullcontext)
        with patch.object(cpu, "png_to_jpeg", return_value=jpeg_header_fixture()), \
             patch.object(cpu, "normalize_exposure", return_value=fake_image), \
             patch.object(cpu, "check_quality"):

            data = cpu.render_one(pipeline, fake_torch, a)
        self.assertEqual(data, jpeg_header_fixture())
        self.assertEqual(gen.manual_seed.call_args.args[0], local_scene_photos.stable_seed(a))
        kwargs = pipeline.call_args.kwargs
        self.assertEqual(kwargs["num_inference_steps"], 8)
        self.assertEqual((kwargs["width"], kwargs["height"]), (800, 448))
        self.assertIn("capsule lottery machine", kwargs["prompt"])
        self.assertIn("DSLR color photograph", kwargs["prompt"])

    def test_tv_comedy_punchline_in_prompt_not_generic_crowd(self):
        a = {**article(), "title": "有名人700人の特番、名前を呼ぶだけで放送終了"}
        prompt = cpu.cpu_prompt(a)
        self.assertIn("EMPTY CHAIRS", prompt)
        self.assertIn("Bright, colorful", prompt)
        self.assertLess(len(prompt.split()), 65)

    def test_auto_exposure_has_strict_underexposure_and_amplification_caps(self):
        with self.assertRaisesRegex(ValueError, "severely underexposed"):
            cpu.exposure_multiplier(20)
        self.assertAlmostEqual(cpu.exposure_multiplier(62), 125 / 62)
        self.assertEqual(cpu.exposure_multiplier(30), 2.4)
        self.assertEqual(cpu.exposure_multiplier(150), 1.0)

    def test_clipped_prompt_is_rejected_before_model_call(self):
        a = article()
        pipeline = Mock()
        pipeline.tokenizer.encode.return_value = list(range(78))
        generator = Mock()
        generator.manual_seed.return_value = generator
        fake_torch = SimpleNamespace(
            Generator=Mock(return_value=generator),
            inference_mode=contextlib.nullcontext,
        )
        with self.assertRaisesRegex(ValueError, "CLIP budget"):
            cpu.render_one(pipeline, fake_torch, a)
        pipeline.assert_not_called()

    def test_compact_backlog_prompts_keep_the_article_objects(self):
        expected = {
            "sweet-potato-chorus-starts-too-late": ("sweet potatoes", "purple skin", "yellow flesh", "referee", "whistle"),
            "historical-lookalike-contest-dog-wins": ("dog", "ribbon", "microphone"),
            "stolen-doorbell-returned-by-parcel": ("parcel", "bell", "push button", "close-up"),
            "family-dinner-gacha-243-rejections": ("capsule lottery machine", "plates", "crossed arms"),
            "volleyball-team-lunch-plate-merch": ("plate", "tape measure", "volleyball net"),
        }
        for slug, objects in expected.items():
            prompt = cpu.cpu_prompt({"slug": slug})
            for item in objects:
                self.assertIn(item, prompt)
            self.assertIn("DSLR color photograph", prompt)
            self.assertIn("unmarked props", prompt)
            self.assertLess(len(prompt.split()), 55)

    def test_budget_fitting_keeps_complete_primary_action_and_context_that_fits(self):
        core = "An actor holds a giant spoon beside an empty soup bowl."
        long_context = "A distant skyline " + "tall " * 85 + "with many buildings."
        a = {"slug": "new-scene", "scene_prompt": core + " " + long_context + " Warm daylight."}
        tokenizer = SimpleNamespace(encode=lambda text, **kwargs: text.split() + ["BOS", "EOS"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output): prompt = cpu.fit_cpu_prompt(a, tokenizer)
        self.assertIn(core, prompt)
        self.assertNotIn("distant skyline", prompt)
        self.assertIn("Warm daylight.", prompt)
        self.assertLessEqual(len(tokenizer.encode(prompt)), 77)
        self.assertIn("omitted 1 optional context", output.getvalue())
        with self.assertRaisesRegex(ValueError, "Visual core exceeds CLIP budget"):
            cpu.fit_cpu_prompt({"slug": "long-core", "scene_prompt": "large " * 90 + "."}, tokenizer)

    def test_single_held_retry_is_explicit_and_never_overwrites_a_valid_image(self):
        a = article()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaisesRegex(ValueError, "explicit --slug"):
                cpu.select_articles([a], retry_held=True, root=root)
            local_scene_photos.write_generated(a, jpeg_header_fixture(), cpu.MODEL,
                                              root=root, origin="github-cpu-lcm")
            self.assertEqual(cpu.select_articles([a], slug=a["slug"], retry_held=True, root=root), [])
            path = root / "assets/generated/manifest.json"
            payload = json.loads(path.read_text())
            payload["rejected"] = {a["slug"]: {"article_sha256": article_fingerprint(a)}}
            path.write_text(json.dumps(payload))
            self.assertEqual(cpu.select_articles([a], root=root), [])
            self.assertEqual(cpu.select_articles([a], slug=a["slug"], retry_held=True, root=root), [a])

    def test_review_bundle_contains_only_successes_and_preserves_exact_large_seed(self):
        a, b = article(), {**article(), "slug": "other-scene"}
        with tempfile.TemporaryDirectory() as folder:
            root, bundle = Path(folder) / "repo", Path(folder) / "review"
            for item in (a, b):
                local_scene_photos.write_generated(item, jpeg_header_fixture(), cpu.MODEL,
                                                  root=root, origin="github-cpu-lcm")
            (root / "assets/generated/intermediate.png").write_bytes(b"not for export")
            cpu.export_review_bundle([a], bundle, root=root)
            files = {str(p.relative_to(bundle)) for p in bundle.rglob("*") if p.is_file()}
            self.assertEqual(files, {"content/articles.json", "assets/generated/manifest.json",
                                     "assets/generated/" + a["slug"] + ".jpg"})
            payload = json.loads((bundle / "assets/generated/manifest.json").read_text())
            original = json.loads((root / "assets/generated/manifest.json").read_text())
            self.assertEqual(payload["images"], {a["slug"]: original["images"][a["slug"]]})
            seed = payload["images"][a["slug"]]["seed"]
            self.assertIs(type(seed), int)
            self.assertGreater(seed, 2**53)
            self.assertEqual(seed, local_scene_photos.stable_seed(a))
            self.assertEqual(json.loads((bundle / "content/articles.json").read_text()), [a])
            self.assertTrue(usable_generated_scene(a, root=bundle))

    def test_review_export_refuses_foreign_directories_symlinks_and_source_overlap(self):
        a = article()
        with tempfile.TemporaryDirectory() as folder:
            root, foreign = Path(folder) / "repo", Path(folder) / "foreign"
            root.mkdir(); foreign.mkdir()
            (foreign / "keep.txt").write_text("important")
            link = Path(folder) / "link"
            link.symlink_to(foreign, target_is_directory=True)
            for destination in (foreign, link, root / "review"):
                with self.assertRaises(RuntimeError): cpu.export_review_bundle([a], destination, root=root)
            self.assertEqual((foreign / "keep.txt").read_text(), "important")

    def test_partial_success_is_bundled_even_if_later_disk_check_fails(self):
        a, b = article(), {**article(), "slug": "other-scene"}
        with tempfile.TemporaryDirectory() as folder:
            root, bundle = Path(folder) / "repo", Path(folder) / "review"
            root.mkdir()
            with patch.object(cpu, "ROOT", root), \
                 patch.object(cpu, "load_articles", return_value=[a, b]), \
                 patch.object(cpu, "load_pipeline", return_value=(None, None)), \
                 patch.object(cpu, "permitted_ci", return_value=True), \
                 patch.object(cpu, "render_one", return_value=jpeg_header_fixture()), \
                 patch.object(cpu, "ensure_disk", side_effect=[None, RuntimeError("disk full")]), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "disk full"):
                    cpu.main(["--limit", "2", "--review-dir", str(bundle)])
            self.assertEqual(json.loads((bundle / "content/articles.json").read_text()), [a])
            self.assertTrue(usable_generated_scene(a, root=bundle))

    def test_cpu_generator_rejects_model_safety_filter(self):
        fake_torch = SimpleNamespace(
            Generator=lambda device: SimpleNamespace(manual_seed=lambda seed: None),
            inference_mode=contextlib.nullcontext,
        )
        pipeline = Mock(return_value=SimpleNamespace(images=[None], nsfw_content_detected=[True]))
        pipeline.tokenizer = None
        with self.assertRaises(ValueError):
            cpu.render_one(pipeline, fake_torch, article())

    def test_limits_and_empty_batch_are_safe(self):
        with self.assertRaises(SystemExit):
            cpu.main(["--dry-run", "--limit", "4"])
        with patch.object(cpu, "load_articles", return_value=[article()]), \
             patch.object(cpu, "pending_articles", return_value=[]):
            self.assertEqual(cpu.main(["--dry-run"]), 0)


if __name__ == "__main__":
    unittest.main()
