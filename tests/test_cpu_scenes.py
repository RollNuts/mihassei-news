"""No-network unit tests for the optional free public GitHub CPU generator."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import build, cpu_photo_model as photo, github_cpu_scenes as cpu, local_scene_photos
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
                a, data, cpu.MODEL, origin=cpu.ORIGIN, root=root,
            )
            self.assertTrue(usable_generated_scene(a, root=root))
            record = generated_scene_entry(a, root=root)
            self.assertEqual(record["origin"], cpu.ORIGIN)
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
                expected = "/generated/" + a["slug"] + ".jpg?v=" + record["sha256"][:12]
                self.assertEqual(build.editorial_image(a), expected)
                self.assertEqual(build.social_image(a), expected)
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
                    a, data, "unauthorized/model", origin=cpu.ORIGIN, root=root
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
        self.assertEqual(kwargs["num_inference_steps"], 24)
        self.assertEqual(kwargs["guidance_scale"], 7.0)
        self.assertIn("anime", kwargs["negative_prompt"])
        self.assertIn("written text", kwargs["negative_prompt"])
        self.assertEqual((kwargs["width"], kwargs["height"]), (800, 448))
        self.assertIn("capsule lottery machine", kwargs["prompt"])
        self.assertIn("documentary press photograph", kwargs["prompt"])

    def test_photo_model_requires_pinned_inference_and_legacy_records_still_work(self):
        from scripts.cpu_photo_model import LEGACY_MODEL, LCM_INFERENCE
        a = article()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local_scene_photos.write_generated(a, jpeg_header_fixture(), cpu.MODEL,
                                              origin=cpu.ORIGIN, root=root)
            manifest = root / "assets/generated/manifest.json"
            payload = json.loads(manifest.read_text())
            self.assertEqual(payload["images"][a["slug"]]["inference"], cpu.INFERENCE)
            for altered in (None, {**cpu.INFERENCE, "safety_revision": "unreviewed"},
                            {**cpu.INFERENCE, "guidance_scale": 0.0}):
                changed = json.loads(json.dumps(payload))
                changed["images"][a["slug"]]["inference"] = altered
                manifest.write_text(json.dumps(changed))
                self.assertFalse(usable_generated_scene(a, root=root))
            local_scene_photos.write_generated(a, jpeg_header_fixture(), LEGACY_MODEL,
                                              origin="github-cpu-lcm", root=root)
            self.assertTrue(usable_generated_scene(a, root=root))
            local_scene_photos.write_generated(a, jpeg_header_fixture(), cpu.MODEL,
                                              origin="github-cpu-lcm", root=root)
            self.assertEqual(generated_scene_entry(a, root=root)["inference"], LCM_INFERENCE)
            self.assertTrue(usable_generated_scene(a, root=root))

    def test_negative_prompt_is_not_silently_clipped_by_the_effective_cfg_path(self):
        pipeline = Mock()
        # Positive fits, but the negative does not. No model invocation occurs.
        pipeline.tokenizer.encode.side_effect = lambda text, **kwargs: list(range(78 if "waxy skin" in text else 40))
        generator = Mock()
        fake_torch = SimpleNamespace(Generator=Mock(return_value=generator))
        with self.assertRaisesRegex(ValueError, "negative prompt exceeds CLIP"):
            cpu.render_one(pipeline, fake_torch, article())
        pipeline.assert_not_called()

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
            local_scene_photos.write_generated(a, data, cpu.MODEL, root=root, origin=cpu.ORIGIN)
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
        self.assertIn("Candid documentary photograph", prompt)
        self.assertIn("realistic skin and materials", prompt)
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
            self.assertIn("documentary press photograph", prompt)
            self.assertIn("Ordinary real-world materials", prompt)
            self.assertLess(len(prompt.split()), 65)
            if slug == "volleyball-team-lunch-plate-merch":
                for room_context in ("table", "room", "indoor", "dinner", "net"):
                    self.assertNotIn(room_context, prompt.lower())
            if slug == "stolen-doorbell-returned-by-parcel":
                for appliance_context in ("hotel", "reception", "counter", "electric", "push button", "skirt"):
                    self.assertNotIn(appliance_context, prompt.lower())
            if slug == "sweet-potato-chorus-starts-too-late":
                for crowd_context in ("crowd", "referee", "participants", "adults", "rectangles"):
                    self.assertNotIn(crowd_context, prompt.lower())

    def assert_current_direction_used(self, source):
        core, _ = cpu.prompt_parts(source)
        self.assertIn(core, local_scene_photos.scene_description(source))
        current = cpu.cpu_prompt(source)
        # A fresh direction may legitimately use the same wording as an old
        # compact scene. Check that the expired mapping is ignored, not that
        # its words are absent from the current article's own direction.
        with patch.dict(cpu.CPU_SCENES, {source["slug"]: "Obsolete source-bound override sentinel."}):
            self.assertEqual(cpu.cpu_prompt(source), current)

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
                if article_fingerprint(source) == fingerprint:
                    self.assertTrue(cpu.reviewed_source_matches(source, slug))
                    prompt = cpu.cpu_prompt(source)
                    for cue in cues:
                        self.assertIn(cue, prompt)
                    self.assertLess(len(prompt.split()), 65)
                else:
                    self.assertFalse(cpu.reviewed_source_matches(source, slug))
                    self.assert_current_direction_used(source)

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
            ("sleep-advice-livestream-until-two-am",
             "e9f39c0ae20625806fcd70f392a8fc966b91b44e9720e03900c40a3b93fbaa54",
             ("presses a plain concealer stick below one eye", "ring light", "smartphone")),
            ("five-star-reviews-of-returned-humidifiers",
             "146c413d5edb2298e0d923589c18ae800ed4f5390b4c37a1410a2002155ee963",
             ("white tabletop humidifier", "return boxes blocking")),
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

    def test_current_fridge_prompt_retains_refrigerator_notes_and_phone_in_one_core(self):
        slug = "fridge-checkin-five-years-only-opening-times"
        source = next(a for a in local_scene_photos.load_articles() if a["slug"] == slug)
        fingerprint = "3f677d6bc13705adf10d04e453bb23a6634192c118f75976708d28dfee34897a"
        if article_fingerprint(source) == fingerprint:
            self.assertTrue(cpu.reviewed_source_matches(source, slug))
            tokenizer = SimpleNamespace(encode=lambda text, **kwargs: text.split() + ["BOS", "EOS"])
            with contextlib.redirect_stdout(io.StringIO()):
                prompt = cpu.fit_cpu_prompt(source, tokenizer)
            for prop in ("white refrigerator door", "yellow paper notes", "black smartphone", "face-down"):
                self.assertIn(prop, prompt)
            self.assertLessEqual(len(tokenizer.encode(prompt)), cpu.CLIP_TOKEN_LIMIT)
        else:
            self.assertFalse(cpu.reviewed_source_matches(source, slug))
            self.assert_current_direction_used(source)
        for changed in ({**source, "paragraphs": [*source["paragraphs"], "An edited ending."]},
                        {**source, "scene_prompt": "An adult holds a green toolbox beside a garden gate."}):
            self.assertFalse(cpu.reviewed_source_matches(changed, slug))
            self.assert_current_direction_used(changed)

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

    def test_short_photograph_openings_keep_the_actual_subject_and_action(self):
        cases = (
            "Photograph of a brass bell in an open parcel.",
            "Documentary photograph of a man carrying a cardboard parcel.",
            "A man poses for a documentary photograph.",
            "A photograph.",
            "Photorealistic Japanese kitchen lighting illuminates a delivery parcel.",
        )
        context = "An ordinary apartment entrance is behind it."
        tokenizer = SimpleNamespace(encode=lambda text, **kwargs: text.split() + ["BOS", "EOS"])
        for core in cases:
            with self.subTest(core=core):
                source = {"slug": "new-visual-scene", "scene_prompt": core + " " + context}
                self.assertEqual(cpu.prompt_parts(source), (core, [context]))
                with contextlib.redirect_stdout(io.StringIO()):
                    fitted = cpu.fit_cpu_prompt(source, tokenizer)
                self.assertIn(core, fitted)
                self.assertIn(context, fitted)

    def test_unfamiliar_subject_words_are_not_classified_as_style_prefixes(self):
        # Subject preservation must not depend on a catalog of familiar nouns.
        for core in ("Photograph of a nyckelharpa.",
                     "A fennec poses for a photograph."):
            with self.subTest(core=core):
                context = "A plain wall is behind it."
                source = {"slug": "unfamiliar-visual-scene", "scene_prompt": core + " " + context}
                self.assertEqual(cpu.prompt_parts(source), (core, [context]))

    def test_only_standalone_style_prefixes_are_removed(self):
        core = "An adult carries a taped cardboard parcel through an apartment entrance."
        context = "Soft daylight."
        for prefixes in (
                "Photorealistic.",
                "Photorealistic Japanese comedy.",
                "Candid documentary press photograph.",
                "Photorealistic staged Japanese news comedy. Natural lighting.",
                "Photorealistic wide film still from a deadpan Japanese comedy."):
            with self.subTest(prefixes=prefixes):
                source = {"slug": "new-visual-scene",
                          "scene_prompt": prefixes + " " + core + " " + context}
                self.assertEqual(cpu.prompt_parts(source), (core, [context]))
        # A style-only description is not removed if there is no scene after it.
        self.assertEqual(cpu.prompt_parts({"slug": "style-only-scene",
                                          "scene_prompt": "Candid documentary press photograph."}),
                         ("Candid documentary press photograph.", []))

    def test_minimal_style_retains_full_context_that_would_otherwise_be_omitted(self):
        core = "A fisherman carries coiled rope beside a small fishing boat."
        context = (
            "The small coastal fishing port has low unmarked wooden storage sheds, weathered mooring posts, "
            "simple nets, fishing floats and narrow working quays beside calm water, with "
            "no city skyline, tall apartment blocks, pleasure yachts, billboards, brand "
            "logos or readable writing, under flat overcast dawn light with natural wood "
            "and rope textures and unposed people nearby."
        )
        source = {"slug": "new-harbor-scene", "scene_prompt": core + " " + context}
        tokenizer = SimpleNamespace(encode=lambda text, **kwargs: text.split() + ["BOS", "EOS"])
        full = cpu.wrap_prompt(core, [context])
        minimal_full = cpu.wrap_prompt(core, [context], minimal=True)
        self.assertGreater(len(tokenizer.encode(full)), cpu.CLIP_TOKEN_LIMIT)
        self.assertLessEqual(len(tokenizer.encode(minimal_full)), cpu.CLIP_TOKEN_LIMIT)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            fitted = cpu.fit_cpu_prompt(source, tokenizer)
        self.assertEqual(fitted, minimal_full)
        self.assertNotIn("omitted", output.getvalue())

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
                                              root=root, origin=cpu.ORIGIN)
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
                                                  root=root, origin=cpu.ORIGIN)
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
                 patch.object(cpu, "_git_read", side_effect=AssertionError("normal generation performed a backfill network check")) as git_read, \
                 patch.object(cpu, "check_backfill_storage", side_effect=AssertionError("normal generation changed its disk policy")) as storage, \
                 patch.object(cpu, "render_one", return_value=jpeg_header_fixture()) as render, \
                 patch.object(cpu, "ensure_disk", side_effect=[None, RuntimeError("disk full")]), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "disk full"):
                    cpu.main(["--limit", "2", "--review-dir", str(bundle)])
            self.assertEqual(json.loads((bundle / "content/articles.json").read_text()), [a])
            self.assertTrue(usable_generated_scene(a, root=bundle))
            git_read.assert_not_called()
            storage.assert_not_called()
            self.assertEqual(render.call_args.kwargs, {})

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


class CpuBackfillTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root, self.bundle = self.directory / "repo", self.directory / "review"
        self.root.mkdir()
        self.original = {**article(), "visual_strategy": "illustration",
                         "scene_prompt": "An old illustrated dinner scene with a capsule machine.",
                         "paragraphs": ["元の記事本文を維持する。"]}
        self.scene = "A plain red capsule vending machine stands beside three empty dinner plates."
        self.healthy = {"slug": self.original["slug"], "status": "missing",
                        "visual_strategy": "illustration", "editorial_scene": {"status": "current"},
                        "publication": {"kind": "editorial_svg", "url": "/keep.svg"}}
        self.env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "SCENE_PUBLISH": "0",
                    "BACKFILL_QUOTA_ALLOWED": "1"}

    def select(self, articles=None, **options):
        defaults = {"slug": self.original["slug"], "limit": 1, "retry_held": True,
                    "backfill_scene": self.scene, "review_dir": self.bundle, "root": self.root}
        defaults.update(options)
        return cpu.select_articles(articles or [self.original], **defaults)

    def healthy_review(self):
        return patch("scripts.visual_review._publication_row", return_value=self.healthy)

    def same_main(self, *arguments):
        return "a" * 40 if arguments == ("rev-parse", "HEAD") else "a" * 40 + "\trefs/heads/main"

    def write_reference_svg(self):
        from scripts.editorial_covers import story_subject
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 675" '
               'role="img" aria-label="合成テスト用の創作イラスト" '
               f'data-visual-topic="{story_subject(self.original)}" '
               f'data-article-sha256="{article_fingerprint(self.original)}">'
               '<title>Synthetic reference fixture, not an image review</title>'
               '<rect width="1200" height="675" fill="white"/>'
               '<rect x="90" y="90" width="300" height="200" fill="#d02020"/></svg>')
        path = self.root / "assets/editorial-scenes" / (self.original["slug"] + ".svg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(svg, encoding="utf-8")
        return svg

    def fake_reference_renderer(self):
        from PIL import Image
        image = Image.new("RGB", (800, 448), (12, 34, 56))
        output = io.BytesIO()
        image.save(output, format="PNG")
        return SimpleNamespace(__version__="2.7.1", svg2png=Mock(return_value=output.getvalue()))

    def test_reference_preflight_requires_an_original_reviewed_svg_and_all_preview_flags(self):
        with patch.dict(os.environ, self.env), self.healthy_review():
            with self.assertRaisesRegex(ValueError, "requires an explicit --backfill-scene"):
                self.select(backfill_reference=True, backfill_scene="")
            with self.assertRaisesRegex(ValueError, "original article's reviewed SVG"):
                self.select(backfill_reference=True)
            svg = self.write_reference_svg()
            clone, = self.select(backfill_reference=True)
            self.assertEqual(cpu.reviewed_reference_svg(self.original, root=self.root), svg)
            with self.assertRaises(ValueError):
                cpu.reviewed_reference_svg(clone, root=self.root)
            path = self.root / "assets/editorial-scenes" / (self.original["slug"] + ".svg")
            path.write_text(svg.replace("<rect width=", '<image href="https://invalid.test/remote.png"/><rect width=', 1))
            with self.assertRaises(ValueError):
                self.select(backfill_reference=True)

    def test_reference_new_work_and_quota_block_before_renderer_or_model(self):
        self.write_reference_svg()
        for articles, quota, error in (([self.original, {**article(), "slug": "new-pending-story"}], "1", SystemExit),
                                       ([self.original], "0", RuntimeError)):
            with patch.dict(os.environ, {**self.env, "BACKFILL_QUOTA_ALLOWED": quota}), self.healthy_review(), \
                 patch.object(cpu, "ROOT", self.root), patch.object(cpu, "load_articles", return_value=articles), \
                 patch.object(cpu, "permitted_ci", return_value=True), \
                 patch.object(cpu, "render_reference_image", side_effect=AssertionError("guide rasterized")), \
                 patch.object(cpu, "load_pipeline", side_effect=AssertionError("model loaded")), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(error):
                    cpu.main(["--slug", self.original["slug"], "--retry-held", "--limit", "1",
                              "--review-dir", str(self.bundle), "--backfill-scene", self.scene,
                              "--backfill-reference"])
        self.assertFalse(self.bundle.exists())

    def test_reference_raster_records_actual_svg_and_rgb_bytes_without_files(self):
        svg = self.write_reference_svg()
        renderer = self.fake_reference_renderer()
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with patch.dict("sys.modules", {"cairosvg": renderer}):
            image, proof = cpu.render_reference_image(self.original, root=self.root)
        self.assertEqual(image.mode, "RGB")
        self.assertEqual(image.size, (800, 448))
        self.assertEqual(proof["source_article_sha256"], article_fingerprint(self.original))
        self.assertEqual(proof["init_svg_sha256"], hashlib.sha256(svg.encode("utf-8")).hexdigest())
        self.assertEqual(proof["init_rgb_sha256"], hashlib.sha256(image.tobytes()).hexdigest())
        self.assertEqual(set(proof), photo.REFERENCE_METADATA_KEYS)
        renderer.svg2png.assert_called_once_with(bytestring=svg.encode("utf-8"), unsafe=False,
                                                background_color="#ffffff", output_width=800, output_height=448)
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        renderer.__version__ = "unreviewed-version"
        with patch.dict("sys.modules", {"cairosvg": renderer}), self.assertRaisesRegex(RuntimeError, "pinned CairoSVG"):
            cpu.render_reference_image(self.original, root=self.root)

    def test_actual_pinned_reference_renderer_rasterizes_the_reviewed_fixture(self):
        required = (os.getenv("VERIPSA_TEST_REFERENCE_RENDERER") == "1"
                    or os.getenv("BACKFILL_REFERENCE") == "1")
        try:
            import cairosvg
        except (ImportError, OSError):
            if required:
                raise
            self.skipTest("CairoSVG is installed only by CI or the opt-in CPU reference job")
        if cairosvg.__version__ != photo.REFERENCE_RENDERER_VERSION:
            if required:
                self.fail("CI must install the pinned CairoSVG 2.7.1")
            self.skipTest("Actual renderer test requires the CI-pinned CairoSVG 2.7.1")
        self.write_reference_svg()
        image, proof = cpu.render_reference_image(self.original, root=self.root)
        self.assertEqual(image.size, (800, 448))
        self.assertEqual(image.getpixel((100, 100)), (208, 32, 32))
        self.assertEqual(image.getpixel((700, 400)), (255, 255, 255))
        self.assertEqual(proof["init_rgb_sha256"], hashlib.sha256(image.tobytes()).hexdigest())
        self.assertFalse(any(self.root.rglob("*.png")))

    def test_reference_conversion_reuses_all_verified_components_and_safety(self):
        names = ("unet", "vae", "text_encoder", "tokenizer", "scheduler", "safety_checker", "feature_extractor")
        original = SimpleNamespace(**{name: object() for name in names})
        converted = Mock(**{name: getattr(original, name) for name in names})
        factory = Mock()
        factory.from_pipe.return_value = converted
        with patch.dict("sys.modules", {"diffusers": SimpleNamespace(AutoPipelineForImage2Image=factory)}):
            self.assertIs(photo.as_reference_pipeline(original), converted)
            factory.from_pipe.assert_called_once_with(original, requires_safety_checker=True)
            converted.to.assert_called_once_with("cpu")
            converted.enable_attention_slicing.assert_called_once_with("auto")
            converted.safety_checker = None
            with self.assertRaises(RuntimeError):
                photo.as_reference_pipeline(original)

    def test_reference_img2img_call_uses_fixed_guide_without_ignored_dimension_kwargs(self):
        from PIL import Image
        clone = {**self.original, "scene_prompt": self.scene, "visual_strategy": "cpu-photo"}
        guide = Image.new("RGB", (800, 448), "white")
        result_image = SimpleNamespace(save=lambda output, **kwargs: output.write(b"synthetic PNG"))
        pipe = Mock(return_value=SimpleNamespace(images=[result_image], nsfw_content_detected=[False]))
        pipe.tokenizer = None
        generator = Mock()
        generator.manual_seed.return_value = generator
        torch = SimpleNamespace(Generator=Mock(return_value=generator), inference_mode=contextlib.nullcontext)
        callback = Mock()
        with patch.object(cpu, "normalize_exposure", side_effect=lambda image: image), \
             patch.object(cpu, "check_quality"), patch.object(cpu, "png_to_jpeg", return_value=jpeg_header_fixture()):
            cpu.render_one(pipe, torch, clone, reference_image=guide, callback_on_step_end=callback)
        kwargs = pipe.call_args.kwargs
        self.assertNotIn("width", kwargs)
        self.assertNotIn("height", kwargs)
        self.assertIs(kwargs["image"], guide)
        self.assertEqual(kwargs["strength"], 0.65)
        self.assertEqual(kwargs["num_inference_steps"], 24)
        self.assertEqual(kwargs["guidance_scale"], 7.0)
        self.assertIs(kwargs["callback_on_step_end"], callback)
        generator.manual_seed.assert_called_once_with(local_scene_photos.stable_seed(clone))
        pipe.return_value.nsfw_content_detected = [True]
        with self.assertRaisesRegex(ValueError, "safety filter"):
            cpu.render_one(pipe, torch, clone, reference_image=guide)

    def test_reference_writer_requires_exact_proof_and_preserves_existing_bytes_on_bad_metadata(self):
        self.write_reference_svg()
        with patch.dict("sys.modules", {"cairosvg": self.fake_reference_renderer()}):
            _image, proof = cpu.render_reference_image(self.original, root=self.root)
        clone = {**self.original, "scene_prompt": self.scene, "visual_strategy": "cpu-photo"}
        local_scene_photos.write_generated(clone, jpeg_header_fixture(), cpu.MODEL, root=self.root,
                                          origin=cpu.REFERENCE_ORIGIN, reference_metadata=proof)
        record = generated_scene_entry(clone, root=self.root)
        self.assertEqual(record["inference"], cpu.REFERENCE_INFERENCE)
        self.assertEqual(record["seed"], local_scene_photos.stable_seed(clone))
        self.assertIs(type(record["seed"]), int)
        path = self.root / "assets/generated/manifest.json"
        before = path.read_bytes()
        for metadata in (None, {**proof, "renderer_version": "other"},
                         {**proof, "source_article_sha256": article_fingerprint(clone)},
                         {**proof, "unexpected": "field"}):
            with self.assertRaises(ValueError):
                local_scene_photos.write_generated(clone, jpeg_header_fixture(), cpu.MODEL, root=self.root,
                                                  origin=cpu.REFERENCE_ORIGIN, reference_metadata=metadata)
            self.assertEqual(path.read_bytes(), before)
        with self.assertRaises(ValueError):
            local_scene_photos.write_generated(clone, jpeg_header_fixture(), cpu.MODEL, root=self.root,
                                              origin=cpu.ORIGIN, reference_metadata=proof)
        self.assertTrue(usable_generated_scene(clone, root=self.root))
        self.assertFalse(reviewed_generated_scene(clone, root=self.root))

    def test_reference_main_exports_only_new_jpeg_clone_and_actual_input_proof(self):
        self.write_reference_svg()
        (self.root / "content").mkdir()
        (self.root / "content/articles.json").write_text(json.dumps([self.original]))
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        renderer = self.fake_reference_renderer()
        fake_image = SimpleNamespace(save=lambda output, **kwargs: output.write(b"synthetic PNG"))
        pipe = Mock(return_value=SimpleNamespace(images=[fake_image], nsfw_content_detected=[False]))
        pipe.tokenizer = None
        generator = Mock()
        generator.manual_seed.return_value = generator
        torch = SimpleNamespace(Generator=Mock(return_value=generator), inference_mode=contextlib.nullcontext)
        with patch.dict(os.environ, self.env), self.healthy_review(), \
             patch.dict("sys.modules", {"cairosvg": renderer}), \
             patch.object(cpu, "ROOT", self.root), patch.object(cpu, "load_articles", return_value=[self.original]), \
             patch.object(cpu, "permitted_ci", return_value=True), patch.object(cpu, "_git_read", side_effect=self.same_main), \
             patch.object(cpu, "load_pipeline", return_value=(pipe, torch)) as load, \
             patch.object(cpu, "as_reference_pipeline", return_value=pipe) as convert, \
             patch.object(cpu, "normalize_exposure", side_effect=lambda image: image), \
             patch.object(cpu, "check_quality"), patch.object(cpu, "png_to_jpeg", return_value=jpeg_header_fixture()), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cpu.main(["--slug", self.original["slug"], "--retry-held", "--limit", "1",
                                       "--review-dir", str(self.bundle), "--backfill-scene", self.scene,
                                       "--backfill-reference"]), 0)
        load.assert_called_once()
        convert.assert_called_once_with(pipe)
        pipe.assert_called_once()
        clone, = json.loads((self.bundle / "content/articles.json").read_text())
        record = generated_scene_entry(clone, root=self.bundle)
        self.assertEqual(record["origin"], cpu.REFERENCE_ORIGIN)
        self.assertEqual(record["inference"], cpu.REFERENCE_INFERENCE)
        self.assertEqual(record["source_article_sha256"], article_fingerprint(self.original))
        self.assertEqual(record["init_rgb_sha256"], hashlib.sha256(pipe.call_args.kwargs["image"].tobytes()).hexdigest())
        self.assertIs(type(record["seed"]), int)
        self.assertGreater(record["seed"], 2**53)
        generator.manual_seed.assert_called_once_with(record["seed"])
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        self.assertEqual({p.relative_to(self.bundle).as_posix() for p in self.bundle.rglob("*") if p.is_file()},
                         {"content/articles.json", "assets/generated/manifest.json",
                          "assets/generated/" + clone["slug"] + ".jpg"})
        from scripts.publish_cpu_scenes import validate_bundle
        self.assertEqual(validate_bundle(self.bundle), [clone])
        self.assertFalse(reviewed_generated_scene(clone, root=self.bundle))

    def test_backfill_clones_only_visual_direction_and_does_not_requeue_illustrations(self):
        snapshot = json.loads(json.dumps(self.original))
        with patch.dict(os.environ, self.env), self.healthy_review():
            clone, = self.select()
        self.assertEqual(self.original, snapshot)
        self.assertEqual(set(clone), set(self.original))
        self.assertEqual({key for key in clone if clone[key] != self.original[key]},
                         {"scene_prompt", "visual_strategy"})
        self.assertEqual(clone["visual_strategy"], "cpu-photo")
        self.assertIsNot(clone["paragraphs"], self.original["paragraphs"])
        self.assertNotEqual(article_fingerprint(clone), article_fingerprint(self.original))
        self.assertEqual(local_scene_photos.stable_seed(clone), local_scene_photos.stable_seed(self.original))
        self.assertEqual(cpu.select_articles([self.original], root=self.root), [])
        self.assertEqual(cpu.select_articles([self.original], root=self.root,
                                            slug=self.original["slug"], retry_held=True), [])

    def test_backfill_misuse_fails_before_model_or_source_writes(self):
        bad_options = ({"slug": ""}, {"retry_held": False}, {"limit": 2}, {"review_dir": None},
                       {"backfill_scene": "too short"},
                       {"backfill_scene": self.original["scene_prompt"]})
        with patch.dict(os.environ, self.env), self.healthy_review(), \
             patch.object(cpu, "load_pipeline", side_effect=AssertionError("model requested")):
            for options in bad_options:
                with self.subTest(options=options), self.assertRaises(ValueError):
                    self.select(**options)
            for env in ({"GITHUB_EVENT_NAME": "schedule"}, {"SCENE_PUBLISH": "1"},
                        {"SCENE_PUBLISH": ""}):
                with patch.dict(os.environ, env), self.assertRaises(ValueError):
                    self.select()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_normal_new_article_generation_blocks_backfill(self):
        new_article = {**article(), "slug": "a-new-unpictured-story"}
        with patch.dict(os.environ, self.env), self.healthy_review():
            with self.assertRaisesRegex(ValueError, "generation takes priority"):
                self.select([self.original, new_article])

    def test_pending_review_and_generic_fallback_block_backfill(self):
        for row in (
            {**self.healthy, "status": "pending"},
            {**self.healthy, "publication": {"kind": "generic_fallback", "url": "/missing.svg"}},
            {**self.healthy, "editorial_scene": {"status": "stale"}},
        ):
            with patch.dict(os.environ, self.env), \
                 patch("scripts.visual_review._publication_row", return_value=row), \
                 self.assertRaisesRegex(ValueError, "review or coverage work takes priority"):
                self.select()

    def test_existing_valid_photo_cannot_be_overwritten(self):
        local_scene_photos.write_generated(self.original, jpeg_header_fixture(), cpu.MODEL,
                                          root=self.root, origin=cpu.ORIGIN)
        original_bytes = (self.root / "assets/generated/manifest.json").read_bytes()
        with patch.dict(os.environ, self.env), self.healthy_review():
            with self.assertRaisesRegex(ValueError, "existing valid JPEG"):
                self.select()
        self.assertEqual((self.root / "assets/generated/manifest.json").read_bytes(), original_bytes)

    def test_held_photo_is_eligible_but_unheld_normal_story_is_not(self):
        original = {**self.original, "visual_strategy": "cpu-photo"}
        path = self.root / "assets/generated/manifest.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"version": 1, "images": {}, "rejected": {
            original["slug"]: {"article_sha256": article_fingerprint(original)},
        }}))
        with patch.dict(os.environ, self.env), self.healthy_review():
            self.assertEqual(len(self.select([original])), 1)
            path.unlink()
            with self.assertRaisesRegex(ValueError, "illustration story or a current visual hold"):
                self.select([original])

    def test_real_render_path_exports_clone_provenance_without_touching_live_files(self):
        (self.root / "content").mkdir()
        (self.root / "content/articles.json").write_text(json.dumps([self.original]))
        svg = self.root / "assets/editorial-scenes" / (self.original["slug"] + ".svg")
        svg.parent.mkdir(parents=True)
        svg.write_text("published SVG must remain unchanged")
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        generator = Mock()
        generator.manual_seed.return_value = generator
        fake_torch = SimpleNamespace(Generator=Mock(return_value=generator), inference_mode=contextlib.nullcontext)
        fake_image = SimpleNamespace(save=lambda output, **kwargs: output.write(b"synthetic PNG input"))
        pipe = Mock(return_value=SimpleNamespace(images=[fake_image], nsfw_content_detected=[False]))
        pipe.tokenizer = None
        with patch.dict(os.environ, self.env), self.healthy_review(), \
             patch.object(cpu, "ROOT", self.root), \
             patch.object(cpu, "load_articles", return_value=[self.original]), \
             patch.object(cpu, "permitted_ci", return_value=True), \
             patch.object(cpu, "_git_read", side_effect=self.same_main), \
             patch.object(cpu, "load_pipeline", return_value=(pipe, fake_torch)), \
             patch.object(cpu, "normalize_exposure", side_effect=lambda image: image), \
             patch.object(cpu, "check_quality"), \
             patch.object(cpu, "png_to_jpeg", return_value=jpeg_header_fixture()), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cpu.main(["--slug", self.original["slug"], "--retry-held", "--limit", "1",
                                       "--review-dir", str(self.bundle), "--backfill-scene", self.scene]), 0)
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(len(pipe.call_args_list), 1)
        self.assertIn(self.scene, pipe.call_args.kwargs["prompt"])
        clone, = json.loads((self.bundle / "content/articles.json").read_text())
        record = json.loads((self.bundle / "assets/generated/manifest.json").read_text())["images"][clone["slug"]]
        self.assertEqual(record["source_article_sha256"], article_fingerprint(self.original))
        self.assertEqual(record["article_sha256"], article_fingerprint(clone))
        self.assertNotEqual(record["article_sha256"], record["source_article_sha256"])
        self.assertEqual(record["inference"], cpu.INFERENCE)
        self.assertEqual(record["origin"], cpu.ORIGIN)
        self.assertIs(type(record["seed"]), int)
        self.assertGreater(record["seed"], 2**53)
        generator.manual_seed.assert_called_once_with(record["seed"])
        self.assertTrue(usable_generated_scene(clone, root=self.bundle))
        from scripts.publish_cpu_scenes import validate_bundle
        self.assertEqual(validate_bundle(self.bundle), [clone])
        self.assertFalse(any(self.directory.glob(".cpu-backfill-*")))
        self.assertFalse(any(self.bundle.rglob("*.png")))

    def test_missing_daily_quota_permit_aborts_before_staging_or_model(self):
        with patch.dict(os.environ, {**self.env, "BACKFILL_QUOTA_ALLOWED": "0"}), self.healthy_review(), \
             patch.object(cpu, "ROOT", self.root), \
             patch.object(cpu, "load_articles", return_value=[self.original]), \
             patch.object(cpu, "permitted_ci", return_value=True), \
             patch.object(cpu, "load_pipeline", side_effect=AssertionError("model loaded")), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "daily-quota check"):
                cpu.main(["--slug", self.original["slug"], "--retry-held", "--limit", "1",
                          "--review-dir", str(self.bundle), "--backfill-scene", self.scene])
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertFalse(self.bundle.exists())

    def test_daily_quota_counts_queued_and_failed_attempts_in_jst(self):
        now = datetime(2026, 11, 8, 3, tzinfo=timezone.utc)
        runs = [
            {"id": 10, "display_title": "Photo backfill: old", "created_at": "2026-11-07T14:59:00Z"},
            {"id": 11, "display_title": "Photo backfill: first", "created_at": "2026-11-07T15:00:00Z", "conclusion": "failure"},
            {"id": 12, "display_title": "Free CPU staged-news photos", "created_at": "2026-11-08T01:00:00Z"},
            {"id": 13, "display_title": "Photo backfill: queued", "created_at": "2026-11-08T02:00:00Z", "status": "queued"},
            {"id": 14, "display_title": "Photo backfill: third", "created_at": "2026-11-08T02:10:00Z"},
            {"id": 15, "display_title": "Photo backfill: fourth", "created_at": "2026-11-08T02:20:00Z"},
        ]
        self.assertTrue(cpu.backfill_run_allowed(runs, 11, current_run_attempt=1, now=now))
        self.assertTrue(cpu.backfill_run_allowed(runs, 13, current_run_attempt=1, now=now))
        self.assertTrue(cpu.backfill_run_allowed(runs, 14, current_run_attempt=1, now=now))
        self.assertFalse(cpu.backfill_run_allowed(runs, 15, current_run_attempt=1, now=now))
        with self.assertRaises(ValueError):
            cpu.backfill_run_allowed(runs, 12, current_run_attempt=1, now=now)
        for malformed in ({"id": 14, "display_title": "Photo backfill: broken"},
                          {"id": 14, "display_title": "Photo backfill: broken", "created_at": "bad time"}):
            with self.assertRaises(ValueError):
                cpu.backfill_run_allowed([*runs, malformed], 13, current_run_attempt=1, now=now)

    def test_same_run_id_cannot_spend_another_backfill_attempt(self):
        now = datetime(2026, 11, 8, 3, tzinfo=timezone.utc)
        runs = [{"id": 11, "display_title": "Photo backfill: first",
                 "created_at": "2026-11-08T01:00:00Z"}]
        self.assertTrue(cpu.backfill_run_allowed(runs, 11, current_run_attempt=1, now=now))
        # The API inventory may still describe the first attempt; the runner's
        # actual attempt counter prevents a rerun from reusing that approval.
        self.assertFalse(cpu.backfill_run_allowed(runs, 11, current_run_attempt=2, now=now))
        self.assertFalse(cpu.backfill_run_allowed([], 11, current_run_attempt=2, now=now))

    def test_failed_or_cancelled_attempt_does_not_release_daily_quota(self):
        now = datetime(2026, 11, 8, 3, tzinfo=timezone.utc)
        for conclusion in ("failure", "cancelled"):
            runs = [{"id": 11, "display_title": "Photo backfill: first",
                     "created_at": "2026-11-08T01:00:00Z", "conclusion": conclusion},
                    {"id": 12, "display_title": "Photo backfill: later",
                     "created_at": "2026-11-08T02:00:00Z"},
                    {"id": 13, "display_title": "Photo backfill: third",
                     "created_at": "2026-11-08T02:10:00Z"},
                    {"id": 14, "display_title": "Photo backfill: fourth",
                     "created_at": "2026-11-08T02:20:00Z"}]
            with self.subTest(conclusion=conclusion):
                self.assertTrue(cpu.backfill_run_allowed(runs, 12, current_run_attempt=1, now=now))
                self.assertFalse(cpu.backfill_run_allowed(runs, 14, current_run_attempt=1, now=now))
                self.assertFalse(cpu.backfill_run_allowed(runs, 11, current_run_attempt=2, now=now))

    def test_same_story_repeat_is_denied_and_still_consumes_an_attempt(self):
        now = datetime(2026, 11, 8, 3, tzinfo=timezone.utc)
        runs = [{"id": identifier, "display_title": "Photo backfill: " + slug,
                 "created_at": f"2026-11-08T02:0{index}:00Z"}
                for index, (identifier, slug) in enumerate(((11, "one"), (12, "one"),
                                                           (13, "two"), (14, "three")))]
        self.assertFalse(cpu.backfill_run_allowed(runs, 12, current_run_attempt=1, now=now))
        self.assertTrue(cpu.backfill_run_allowed(runs, 13, current_run_attempt=1, now=now))
        self.assertFalse(cpu.backfill_run_allowed(runs, 14, current_run_attempt=1, now=now))

    def test_workflow_attempt_is_required_and_malformed_values_fail_closed(self):
        for attempt in (0, -1, True, False, 1.0, "1", None):
            with self.subTest(attempt=attempt), self.assertRaisesRegex(ValueError, "positive integer workflow attempt"):
                cpu.backfill_run_allowed([], 11, current_run_attempt=attempt)
        with self.assertRaises(TypeError):
            cpu.backfill_run_allowed([], 11)

    def test_daily_quota_does_not_guess_when_inventory_may_omit_earlier_attempts(self):
        runs = [{"id": identifier, "display_title": "Photo backfill: story" if identifier == 100 else "Normal",
                 "created_at": "2026-11-08T02:00:00Z"} for identifier in range(1, 101)]
        with self.assertRaisesRegex(ValueError, "complete JST day"):
            cpu.backfill_run_allowed(runs, 100, current_run_attempt=1,
                                      now=datetime(2026, 11, 8, 3, tzinfo=timezone.utc))

    def test_main_revision_guard_is_read_only_bounded_and_fails_closed(self):
        revision = "a" * 40
        with patch.object(cpu.subprocess, "run", return_value=SimpleNamespace(stdout=revision + "\trefs/heads/main\n")) as run:
            cpu.check_main_unchanged(revision)
        self.assertEqual(run.call_args.args[0][-4:], ["ls-remote", "--exit-code", "origin", "refs/heads/main"])
        self.assertEqual(run.call_args.kwargs["timeout"], 15)
        self.assertTrue(run.call_args.kwargs["capture_output"])
        for failure in (subprocess.TimeoutExpired("git", 15), subprocess.CalledProcessError(1, "git")):
            with patch.object(cpu.subprocess, "run", side_effect=failure), \
                 self.assertRaisesRegex(RuntimeError, "cannot verify latest main"):
                cpu.check_main_unchanged(revision)
        with patch.object(cpu, "_git_read", return_value="b" * 40 + "\trefs/heads/main"), \
             self.assertRaisesRegex(RuntimeError, "main advanced"):
            cpu.check_main_unchanged(revision)

    def test_step_guard_checks_every_four_steps_without_changing_tensors(self):
        callback, tensors = cpu.backfill_step_callback("a" * 40), {"latents": object()}
        with patch.object(cpu, "check_main_unchanged") as check:
            for step in range(3):
                self.assertIs(callback(None, step, 0, tensors), tensors)
            check.assert_not_called()
            self.assertIs(callback(None, 3, 0, tensors), tensors)
            check.assert_called_once_with("a" * 40, review_parent=None)

    def test_main_advance_reads_only_pinned_metadata_and_priority_paths_with_timeouts(self):
        revision, remote = "a" * 40, "b" * 40
        for changed in ("", "content/articles.json\n", "assets/generated/manifest.json\n",
                        "content/visual_reviews.json\n", "scripts/cpu_photo_model.py\n"):
            responses = [SimpleNamespace(stdout=remote + "\trefs/heads/main\n"),
                         SimpleNamespace(stdout=""), SimpleNamespace(stdout=changed)]
            with self.subTest(changed=changed), patch.object(cpu.subprocess, "run", side_effect=responses) as run:
                if changed:
                    with self.assertRaisesRegex(RuntimeError, "current article/image inputs"):
                        cpu.check_main_unchanged(revision)
                else:
                    cpu.check_main_unchanged(revision)
                commands = [call.args[0][3:] for call in run.call_args_list]
                self.assertEqual(commands[1], ["fetch", "--no-tags", "--depth=1", "--filter=blob:none", "origin", remote])
                self.assertEqual(commands[2], ["diff", "--no-renames", "--name-only", revision, remote,
                                                "--", *cpu.BACKFILL_PRIORITY_PATHS])
                for call in run.call_args_list:
                    self.assertEqual(call.kwargs["timeout"], 15)
                    self.assertTrue(call.kwargs["capture_output"])
                    self.assertTrue(call.kwargs["check"])
                self.assertFalse({"checkout", "reset", "push"}.intersection(item for command in commands for item in command))

    def test_main_guard_rejects_malformed_remote_or_fetch_failure(self):
        for remote in ("", "b" * 39 + "\trefs/heads/main", "b" * 40 + "\trefs/heads/other",
                       "b" * 40 + "\trefs/heads/main\nextra"):
            with self.subTest(remote=remote), patch.object(cpu, "_git_read", return_value=remote) as read:
                with self.assertRaisesRegex(RuntimeError, "invalid remote"):
                    cpu.check_main_unchanged("a" * 40)
                self.assertEqual(read.call_count, 1)
        with patch.object(cpu.subprocess, "run", side_effect=[
                SimpleNamespace(stdout="b" * 40 + "\trefs/heads/main"),
                subprocess.TimeoutExpired("git fetch", 15),
        ]), self.assertRaisesRegex(RuntimeError, "cannot verify latest main"):
            cpu.check_main_unchanged("a" * 40)

    def test_actual_git_tree_diff_allows_docs_social_state_and_about_edits_but_not_articles(self):
        repo = self.directory / "metadata-repo"
        repo.mkdir()
        def git(*args):
            return subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture",
                                   "-c", "user.email=fixture@example.invalid", *args],
                                  check=True, capture_output=True, text=True, timeout=15).stdout.strip()
        git("init", "-q")
        origin = self.directory / "metadata-origin.git"
        subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True,
                       capture_output=True, text=True, timeout=15)
        git("branch", "-M", "main"); git("remote", "add", "origin", str(origin))
        (repo / "content").mkdir()
        source = repo / "content/articles.json"
        source.write_text("[]\n")
        git("add", "."); git("commit", "-qm", "fixture source")
        git("push", "-q", "origin", "main")
        baseline = git("rev-parse", "HEAD")
        (repo / "docs").mkdir(); (repo / "docs/notes.md").write_text("internal note\n")
        (repo / "social-bot").mkdir(); (repo / "social-bot/state.json").write_text("{}\n")
        (repo / "scripts").mkdir(); (repo / "scripts/build.py").write_text("# about text only\n")
        git("add", "."); git("commit", "-qm", "unrelated publishing metadata")
        git("push", "-q", "origin", "main")
        remote = git("rev-parse", "HEAD")
        def snapshot():
            return {name: (repo / name).read_bytes() for name in git("ls-files").splitlines()}
        with patch.object(cpu, "ROOT", repo):
            before = snapshot()
            cpu.check_main_unchanged(baseline)
            self.assertEqual(git("rev-parse", "HEAD"), remote)
            self.assertEqual(snapshot(), before)
            for relative in ("content/articles.json", "content/visual_reviews.json",
                             "content/approved_photos.json", "assets/generated/synthetic-fixture.jpg",
                             "scripts/cpu_photo_model.py"):
                baseline = remote
                path = repo / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("small synthetic priority fixture\n")
                git("add", "."); git("commit", "-qm", "changed priority input")
                git("push", "-q", "origin", "main")
                remote, before = git("rev-parse", "HEAD"), snapshot()
                with self.subTest(path=relative), self.assertRaisesRegex(RuntimeError, "current article/image inputs"):
                    cpu.check_main_unchanged(baseline)
                self.assertEqual(git("rev-parse", "HEAD"), remote)
                self.assertEqual(snapshot(), before)

    def test_backfill_archive_budget_includes_live_archive_reserved_bytes_and_one_max_jpeg(self):
        with patch.object(cpu, "check_storage") as check, \
             patch.object(cpu, "archive_bytes", return_value=cpu.MAX_ARCHIVE_BYTES - cpu.MAX_SCENE_BYTES - 5), \
             patch.object(cpu, "reserved_jpeg_bytes", return_value=5):
            cpu.check_backfill_storage(self.root, review_parent=self.directory)
            check.assert_called_once_with([self.root, self.directory], cpu.MIN_FREE_GIB)
            with patch.object(cpu, "reserved_jpeg_bytes", return_value=6), \
                 self.assertRaises(cpu.StorageLimitError):
                cpu.check_backfill_storage(self.root, review_parent=self.directory)

    def test_source_disk_and_combined_archive_limits_reject_before_loading_or_writing(self):
        source = self.root / "content/articles.json"
        source.parent.mkdir()
        source.write_text(json.dumps([self.original]))
        original_bytes = source.read_bytes()
        for free, size in ((cpu.MIN_FREE_GIB * 1024**3 - 1, 0),
                           (100 * 1024**3, cpu.MAX_ARCHIVE_BYTES - cpu.MAX_SCENE_BYTES + 1)):
            with self.subTest(free=free, archive=size), \
                 patch.dict(os.environ, self.env), self.healthy_review(), \
                 patch.object(cpu, "ROOT", self.root), \
                 patch.object(cpu, "load_articles", return_value=[self.original]), \
                 patch.object(cpu.shutil, "disk_usage", return_value=SimpleNamespace(free=free)), \
                 patch.object(cpu, "archive_bytes", return_value=size), \
                 patch.object(cpu, "reserved_jpeg_bytes", return_value=0), \
                 patch.object(cpu, "load_pipeline", side_effect=AssertionError("model loaded")), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(cpu.StorageLimitError):
                    cpu.main(["--slug", self.original["slug"], "--retry-held", "--limit", "1",
                              "--review-dir", str(self.bundle), "--backfill-scene", self.scene])
            self.assertEqual(source.read_bytes(), original_bytes)
            self.assertFalse(self.bundle.exists())
            self.assertFalse((self.root / "assets/generated").exists())
            self.assertFalse(any(self.directory.glob(".cpu-backfill-*")))

    def test_storage_drop_after_load_or_before_write_preserves_sources_and_cleans_stage(self):
        source = self.root / "content/articles.json"
        source.parent.mkdir()
        source.write_text(json.dumps([self.original]))
        original_bytes = source.read_bytes()
        for failure_check in (3, 4):
            checks = [None] * (failure_check - 1) + [cpu.StorageLimitError("disk space dropped")]
            with self.subTest(checkpoint=failure_check), \
                 patch.dict(os.environ, self.env), self.healthy_review(), \
                 patch.object(cpu, "ROOT", self.root), \
                 patch.object(cpu, "load_articles", return_value=[self.original]), \
                 patch.object(cpu, "permitted_ci", return_value=True), \
                 patch.object(cpu, "_git_read", side_effect=self.same_main), \
                 patch.object(cpu, "check_backfill_storage", side_effect=checks), \
                 patch.object(cpu, "load_pipeline", return_value=(None, None)) as load, \
                 patch.object(cpu, "render_one", return_value=jpeg_header_fixture()) as render, \
                 patch.object(cpu, "write_generated", side_effect=AssertionError("source written")), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(RuntimeError):
                    cpu.main(["--slug", self.original["slug"], "--retry-held", "--limit", "1",
                              "--review-dir", str(self.bundle), "--backfill-scene", self.scene])
                load.assert_called_once()
                self.assertEqual(render.call_count, 0 if failure_check == 3 else 1)
            self.assertEqual(source.read_bytes(), original_bytes)
            self.assertFalse(self.bundle.exists())
            self.assertFalse(any(self.directory.glob(".cpu-backfill-*")))

    def test_step_guard_stops_on_low_storage_before_another_network_check(self):
        callback = cpu.backfill_step_callback("a" * 40, review_parent=self.directory)
        with patch.object(cpu, "ROOT", self.root), \
             patch.object(cpu.shutil, "disk_usage", return_value=SimpleNamespace(free=11 * 1024**3)), \
             patch.object(cpu, "_git_read", side_effect=AssertionError("network reached before low-space stop")):
            for step in range(3):
                self.assertEqual(callback(None, step, 0, {}), {})
            with self.assertRaises(cpu.StorageLimitError):
                callback(None, 3, 0, {})

    def test_new_main_interrupts_preview_without_changing_live_sources(self):
        source = self.root / "content/articles.json"
        source.parent.mkdir()
        source.write_text(json.dumps([self.original]))
        original_bytes = source.read_bytes()
        def render(_pipe, _torch, _article, *, callback_on_step_end):
            callback_on_step_end(None, 3, 0, {})
            self.fail("New main must abort before writing a JPEG")
        with patch.dict(os.environ, self.env), self.healthy_review(), \
             patch.object(cpu, "ROOT", self.root), \
             patch.object(cpu, "load_articles", return_value=[self.original]), \
             patch.object(cpu, "permitted_ci", return_value=True), \
             patch.object(cpu, "_git_read", side_effect=self.same_main), \
             patch.object(cpu, "check_main_unchanged", side_effect=[None, None, RuntimeError("main advanced")]), \
             patch.object(cpu, "load_pipeline", return_value=(None, None)), \
             patch.object(cpu, "render_one", side_effect=render), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "No scenes passed"):
                cpu.main(["--slug", self.original["slug"], "--retry-held", "--limit", "1",
                          "--review-dir", str(self.bundle), "--backfill-scene", self.scene])
        self.assertEqual(source.read_bytes(), original_bytes)
        self.assertFalse(self.bundle.exists())
        self.assertFalse((self.root / "assets/generated").exists())
        self.assertFalse(any(self.directory.glob(".cpu-backfill-*")))

    def test_workflow_bounds_backfill_before_runtime_and_excludes_publication(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/free-cpu-scenes.yml").read_text()
        self.assertIn("format('Photo backfill: {0}', inputs.slug)", workflow)
        self.assertIn("BACKFILL_SCENE: ${{ inputs.backfill_scene || '' }}", workflow)
        self.assertIn('args+=(--backfill-scene "$BACKFILL_SCENE")', workflow)
        self.assertIn('args+=(--backfill-reference)', workflow)
        self.assertIn("&& !inputs.backfill_reference", workflow)
        self.assertIn("current_run_attempt=int(os.environ['GITHUB_RUN_ATTEMPT'])", workflow)
        self.assertLess(workflow.index("Plan missing images"), workflow.index("Bound daily photo backfill"))
        self.assertLess(workflow.index("Bound daily photo backfill"), workflow.index("Install CPU-only"))
        self.assertIn("MAX_DAILY_BACKFILLS", workflow)
        self.assertEqual(workflow.count("&& !inputs.retry_held && !inputs.backfill_scene"), 2)
        self.assertEqual(workflow.count("steps.backfill_quota.outputs.allowed == '1'"), 4)


if __name__ == "__main__":
    unittest.main()
