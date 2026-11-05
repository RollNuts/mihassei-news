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
from scripts.visual_review import reviewed_generated_scene


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
            self.assertFalse(reviewed_generated_scene(a, root=root))
            with patch.object(build, "BASE", root):
                self.assertEqual(build.editorial_image(a), "/images/" + a["slug"] + ".svg")
                self.assertEqual(build.social_image(a),
                                 "/og/articles/" + a["slug"] + ".png?v=20261103-topic2")
                self.assertEqual(build.photo_credit(a), "創作イラスト")
            # Synthetic review input for this temporary header fixture only;
            # CPU generation itself must never grant visual acceptance.
            reviews = root / "content/visual_reviews.json"
            reviews.parent.mkdir(parents=True)
            reviews.write_text(json.dumps({
                "version": 1,
                "reviews": {a["slug"]: {
                    "decision": "accepted",
                    "article_sha256": article_fingerprint(a),
                    "image_sha256": record["sha256"],
                    "reviewed_at": "2026-11-04T01:30:05+00:00",
                    "note": "Synthetic test fixture; not an actual visual review.",
                }},
            }), encoding="utf-8")
            self.assertTrue(reviewed_generated_scene(a, root=root))
            with patch.object(build, "BASE", root):
                self.assertEqual(build.editorial_image(a), "/generated/" + a["slug"] + ".jpg")
                self.assertEqual(build.social_image(a), "/generated/" + a["slug"] + ".jpg")
                self.assertIn("再現イメージ（合成）", build.photo_credit(a))
                illustration = {**a, "visual_strategy": "illustration"}
                self.assertTrue(reviewed_generated_scene(illustration, root=root))
                self.assertFalse(build.uses_generated_scene(illustration))
                self.assertEqual(build.editorial_image(illustration),
                                 "/images/" + a["slug"] + ".svg")
                self.assertEqual(build.social_image(illustration),
                                 "/og/articles/" + a["slug"] + ".png?v=20261103-topic2")
                self.assertEqual(build.photo_credit(illustration), "創作イラスト")
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
                self.assertFalse(reviewed_generated_scene(a, root=root))
                with patch.object(build, "BASE", root):
                    self.assertEqual(build.editorial_image(a), "/images/" + a["slug"] + ".svg")
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

    def test_bell_redraw_changes_real_inference_seed_and_records_the_same_integer(self):
        a = {**article(), "slug": "stolen-doorbell-returned-by-parcel", "title": "盗まれた呼び鈴が小包で戻る"}
        legacy = int.from_bytes(hashlib.sha256((a["slug"] + "|" + a["title"]).encode()).digest()[:8], "big") % (2**63)
        seed = local_scene_photos.stable_seed(a)
        self.assertNotEqual(seed, legacy)
        self.assertEqual(local_scene_photos.stable_seed(dict(a)), seed)
        self.assertTrue(0 <= seed < 2**63)
        fake_image = SimpleNamespace(save=Mock())
        pipeline = Mock(return_value=SimpleNamespace(images=[fake_image], nsfw_content_detected=[False]))
        pipeline.tokenizer = None
        generator = Mock()
        generator.manual_seed.return_value = generator
        fake_torch = SimpleNamespace(Generator=Mock(return_value=generator), inference_mode=contextlib.nullcontext)
        output = io.StringIO()
        with patch.object(cpu, "png_to_jpeg", return_value=jpeg_header_fixture()), \
             patch.object(cpu, "normalize_exposure", return_value=fake_image), \
             patch.object(cpu, "check_quality"), contextlib.redirect_stdout(output):
            data = cpu.render_one(pipeline, fake_torch, a)
        generator.manual_seed.assert_called_once_with(seed)
        self.assertIs(pipeline.call_args.kwargs["generator"], generator)
        self.assertIn("CPU scene seed: " + str(seed), output.getvalue())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            local_scene_photos.write_generated(a, data, cpu.MODEL, root=root, origin="github-cpu-lcm")
            self.assertEqual(generated_scene_entry(a, root=root)["seed"], seed)
            self.assertTrue(usable_generated_scene(a, root=root))

    def test_other_articles_preserve_the_original_seed_formula(self):
        for a in local_scene_photos.load_articles():
            if a["slug"] == "stolen-doorbell-returned-by-parcel":
                continue
            legacy = int.from_bytes(hashlib.sha256((a["slug"] + "|" + a["title"]).encode()).digest()[:8], "big") % (2**63)
            with self.subTest(slug=a["slug"]):
                self.assertEqual(local_scene_photos.stable_seed(a), legacy)

    def test_tv_comedy_punchline_in_prompt_not_generic_crowd(self):
        a = {"slug": cpu.TV_PILOT_SLUG, "title": "有名人700人の特番、名前を呼ぶだけで放送終了",
             "summary": "紹介が終わり、ゲームは始まらなかった。", "category": "エンタメ",
             "paragraphs": ["架空の司会者は出演者を紹介し続けた。"],
             "scene_prompt": "A host holds a microphone beside an unused television game buzzer."}
        with patch.dict(cpu.CPU_SCENE_SOURCE_HASHES, {a["slug"]: article_fingerprint(a)}):
            prompt = cpu.cpu_prompt(a)
        self.assertIn("EMPTY CHAIRS", prompt)
        self.assertIn("Bright, colorful", prompt)
        self.assertLess(len(prompt.split()), 65)

    def test_tv_override_cannot_follow_title_alone_or_an_edited_source(self):
        scene = "An anonymous actor carries a blue umbrella into an empty station."
        other = {**article(), "title": "有名人700人の特番、名前を呼ぶだけで放送終了", "scene_prompt": scene}
        self.assertNotIn("EMPTY CHAIRS", cpu.cpu_prompt(other))
        self.assertIn(scene, cpu.cpu_prompt(other))
        source = {**other, "slug": cpu.TV_PILOT_SLUG, "paragraphs": ["Original fictional television story."]}
        with patch.dict(cpu.CPU_SCENE_SOURCE_HASHES, {source["slug"]: article_fingerprint(source)}):
            self.assertIn("EMPTY CHAIRS", cpu.cpu_prompt(source))
            for changed in ({**source, "paragraphs": ["A different fictional ending."]},
                            {**source, "scene_prompt": "An actor holds a green toolbox beside a garden gate."}):
                self.assertFalse(cpu.reviewed_source_matches(changed, cpu.TV_PILOT_SLUG))
                self.assertNotIn("EMPTY CHAIRS", cpu.cpu_prompt(changed))
                self.assertIn(changed["scene_prompt"], cpu.cpu_prompt(changed))

    def test_auto_exposure_has_strict_underexposure_and_amplification_caps(self):
        with self.assertRaisesRegex(ValueError, "severely underexposed"):
            cpu.exposure_multiplier(20)
        self.assertAlmostEqual(cpu.exposure_multiplier(62), 125 / 62)
        self.assertEqual(cpu.exposure_multiplier(30), 2.4)
        self.assertEqual(cpu.exposure_multiplier(150), 1.0)

    def test_neutral_high_contrast_scene_passes_but_unreadable_exposure_does_not(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is needed for the real image-statistics gate")
        def fixture(values):
            image = Image.new("L", (20, 20))
            image.putdata(values)
            return image.convert("RGB")
        # A silver bell or white plate need not carry colorful surfaces.
        neutral = fixture([85, 165] * 200)
        with contextlib.redirect_stdout(io.StringIO()):
            cpu.check_quality(neutral)
            for unreadable in (
                fixture([0, 80] * 200),                  # dark but contrasting
                fixture([150] * 100 + [255] * 300),    # bright but contrasting
                fixture([124, 126] * 200),             # ordinary luma, featureless
                fixture([0] * 400),                    # black
            ):
                with self.assertRaisesRegex(ValueError, "dark/washed out/unreadable"):
                    cpu.check_quality(unreadable)
        with self.assertRaisesRegex(ValueError, "severely underexposed"):
            cpu.normalize_exposure(fixture([0] * 400))

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
            "midterm-prediction-notebook-full-of-holes": ("ragged circular hole", "dark desk underneath", "pink eraser", "Overhead close-up"),
            "sweet-potato-chorus-starts-too-late": ("whole Japanese sweet potato", "deep-purple skin", "golden-yellow flesh", "close-up"),
            "historical-lookalike-contest-dog-wins": ("dog", "ribbon", "microphone"),
            "stolen-doorbell-returned-by-parcel": ("handbell", "wooden handle", "cardboard", "Close-up"),
            "family-dinner-gacha-243-rejections": ("gumball vending machine", "plastic capsules", "turning handle", "empty plates"),
            "volleyball-team-lunch-plate-merch": ("round white ceramic plate", "yellow measuring tape", "blue-yellow volleyball"),
            "film-village-all-stars-no-extras": ("ONE solitary", "completely empty historical movie street", "tripod camera", "no other people"),
            "sale-discount-savings-receipt-framed": ("Close-up still life", "black picture frame", "cash-register paper strip", "No people"),
            "office-face-to-face-day-video-call-echo": ("large computer monitor", "three-by-three video-call grid", "empty Japanese open-plan office", "unused desks"),
        }
        for slug, objects in expected.items():
            source = {**article(), "slug": slug, "scene_prompt": "An adult holds a toolbox beside a garden gate."}
            with patch.dict(cpu.CPU_SCENE_SOURCE_HASHES, {slug: article_fingerprint(source)}):
                prompt = cpu.cpu_prompt(source)
            for item in objects:
                self.assertIn(item, prompt)
            self.assertIn("DSLR color photograph", prompt)
            self.assertIn("unmarked props", prompt)
            self.assertLess(len(prompt.split()), 55)
            if slug == "volleyball-team-lunch-plate-merch":
                for room_context in ("table", "room", "indoor", "dinner", "net"):
                    self.assertNotIn(room_context, prompt.lower())
            if slug == "stolen-doorbell-returned-by-parcel":
                for appliance_context in ("hotel", "reception", "counter", "electric", "push button", "skirt"):
                    self.assertNotIn(appliance_context, prompt.lower())
            if slug == "sweet-potato-chorus-starts-too-late":
                for crowd_context in ("crowd", "referee", "participants", "adults", "rectangles"):
                    self.assertNotIn(crowd_context, prompt.lower())

    def test_latest_rejected_scenes_have_current_compact_punchline_prompts(self):
        articles = {a["slug"]: a for a in local_scene_photos.load_articles()}
        expected = {
            "film-village-all-stars-no-extras": (
                "2a0c728d342f075fa811a72a0130015cee19e8c6ff7c83c602523d1127132ab3",
                ("ONE solitary", "completely empty historical movie street", "tripod camera"),
            ),
            "sale-discount-savings-receipt-framed": (
                "6a13b26639368d2de4db02f73a0f26de8f1c3772ee9e39030ed47939fb3d15b5",
                ("black picture frame", "cash-register paper strip", "appliance boxes"),
            ),
            "office-face-to-face-day-video-call-echo": (
                "634c2087a7f3abd01f3e093a12908177a2463697d675beae374df576b1423a37",
                ("three-by-three video-call grid", "empty Japanese open-plan office", "unused desks"),
            ),
        }
        for slug, (fingerprint, cues) in expected.items():
            with self.subTest(slug=slug):
                source = articles[slug]
                self.assertEqual(article_fingerprint(source), fingerprint)
                self.assertTrue(cpu.reviewed_source_matches(source, slug))
                prompt = cpu.cpu_prompt(source)
                for cue in cues:
                    self.assertIn(cue, prompt)
                self.assertLess(len(prompt.split()), 65)

    def test_scene_specific_negative_guidance_is_version_bound(self):
        source = {**article(), "slug": "film-village-all-stars-no-extras",
                  "scene_prompt": "A director crosses an empty movie street."}
        with patch.dict(cpu.CPU_SCENE_SOURCE_HASHES, {source["slug"]: article_fingerprint(source)}):
            negative = cpu.cpu_negative_prompt(source)
            self.assertIn("written text", negative)
            self.assertIn("crowd", negative)
            self.assertIn("background people", negative)
            changed = {**source, "scene_prompt": "A director opens a blue umbrella."}
            self.assertFalse(cpu.reviewed_source_matches(changed, source["slug"]))
            self.assertNotIn("market crowd", cpu.cpu_negative_prompt(changed))
            self.assertIn("written text", cpu.cpu_negative_prompt(changed))

    def test_current_compact_revisions_use_their_directions_and_edits_expire_them(self):
        articles = {a["slug"]: a for a in local_scene_photos.load_articles()}
        for slug, fingerprint, props in (
            ("sweet-potato-chorus-starts-too-late",
             "77213239def3618b2c997b3349ba4f95ba036604ebc312f084379698caef5008",
             ("deep-purple skin", "golden-yellow flesh")),
            ("midterm-prediction-notebook-full-of-holes",
             "605ceb59eb496cf65bf4927489aaa8a9fc2dc157e82c14a7a29ca2bc720f58bc",
             ("ragged circular hole", "pink eraser")),
        ):
            with self.subTest(slug=slug):
                source = articles[slug]
                # Later editorial changes may use their fresh scene normally.
                if article_fingerprint(source) == fingerprint:
                    self.assertTrue(cpu.reviewed_source_matches(source, slug))
                    for prop in props:
                        self.assertIn(prop, cpu.cpu_prompt(source))
                scene = "An anonymous actor holds a green toolbox beside a garden gate."
                changed = {**source, "scene_prompt": scene}
                self.assertFalse(cpu.reviewed_source_matches(changed, slug))
                self.assertIn(scene, cpu.cpu_prompt(changed))
                self.assertNotIn(props[0], cpu.cpu_prompt(changed))

    def test_reviewed_override_expires_when_body_or_current_scene_changes(self):
        source = {**article(), "paragraphs": ["Original fictional scene."],
                  "scene_prompt": "An adult holds a toolbox beside a garden gate."}
        slug = source["slug"]
        with patch.dict(cpu.CPU_SCENE_SOURCE_HASHES, {slug: article_fingerprint(source)}):
            self.assertEqual(cpu.prompt_parts(source), (cpu.CPU_SCENES[slug], []))
            for changed, scene in (
                ({**source, "paragraphs": ["The fictional story now has a different ending."]}, source["scene_prompt"]),
                ({**source, "scene_prompt": "An adult opens a blue umbrella inside a train station."},
                 "An adult opens a blue umbrella inside a train station."),
            ):
                self.assertNotEqual(article_fingerprint(changed), article_fingerprint(source))
                self.assertEqual(local_scene_photos.stable_seed(changed), local_scene_photos.stable_seed(source))
                self.assertEqual(cpu.prompt_parts(changed), (scene, []))
                self.assertIn(scene, cpu.cpu_prompt(changed))
                self.assertNotIn("gumball vending machine", cpu.cpu_prompt(changed))

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

    def test_illustration_strategy_never_enters_cpu_or_local_generation(self):
        a = {**article(), "visual_strategy": "illustration"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(local_scene_photos.pending_articles([a], root=root), [])
            self.assertEqual(local_scene_photos.pending_articles([a], root=root, force=True), [])
            self.assertEqual(cpu.select_articles([a], root=root), [])
            self.assertEqual(
                cpu.select_articles([a], slug=a["slug"], retry_held=True, root=root), []
            )
        with self.assertRaisesRegex(ValueError, "visual_strategy"):
            local_scene_photos.pending_articles(
                [{**article(), "visual_strategy": "unknown"}]
            )
        self.assertEqual(local_scene_photos.visual_strategy(article()), "cpu-photo")

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

    def test_cpu_workflow_reports_publication_health_separately_from_generation(self):
        workflow = (Path(__file__).resolve().parents[1]
                    / ".github/workflows/free-cpu-scenes.yml").read_text(encoding="utf-8")
        self.assertIn("Summarize reader-visible image coverage", workflow)
        self.assertIn("python3 scripts/visual_review.py", workflow)
        self.assertIn("Accepted generated JPEG", workflow)
        self.assertIn("Generic SVG fallback", workflow)
        self.assertIn("Illustration-routed stories", workflow)
        self.assertIn("CPU-photo stories", workflow)
        self.assertIn("JPEG awaiting visual review", workflow)
        self.assertIn("green CPU job may be intentionally idle", workflow)
        self.assertNotIn("All published articles already have valid local scenes.", workflow)

    def test_limits_and_empty_batch_are_safe(self):
        with self.assertRaises(SystemExit):
            cpu.main(["--dry-run", "--limit", "4"])
        with patch.object(cpu, "load_articles", return_value=[article()]), \
             patch.object(cpu, "pending_articles", return_value=[]):
            self.assertEqual(cpu.main(["--dry-run"]), 0)


if __name__ == "__main__":
    unittest.main()
