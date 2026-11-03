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
        self.assertIn("STAGED SATIRICAL NEWS COMEDY", kwargs["prompt"])
        self.assertIn("Bright, colorful", kwargs["prompt"])

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
