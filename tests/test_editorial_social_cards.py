"""Static scene rasters require intact pixels and proof bound to the current SVG."""
import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from scripts import build, social_cards
from scripts.editorial_covers import story_subject
from scripts.generated_photos import article_fingerprint, generated_url, share_page_url
from scripts.local_scene_photos import write_generated
from scripts.check_share_cards import inspect_markup, png_card_dimensions
from scripts.check_live import verify_scene_social_image
from test_generated_photos import fake_jpeg, fixture, layout_asset_fixture, write_review_fixture


class EditorialSocialCardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root, self.article = Path(temporary.name), fixture()
        self.svg_path = self.root / "assets/editorial-scenes" / (self.article["slug"] + ".svg")
        self.svg_path.parent.mkdir(parents=True)
        self.png_path = self.svg_path.with_suffix(".png")
        self.svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 675" role="img"'
                    ' aria-label="Synthetic test-only 創作イラスト"'
                    f' data-visual-topic="{story_subject(self.article)}"'
                    f' data-article-sha256="{article_fingerprint(self.article)}">'
                    '<title>Synthetic scene, not a production approval</title>'
                    '<rect width="600" height="675" fill="#d12e3c"/>'
                    '<rect x="600" width="600" height="675" fill="#35a55b"/></svg>')
        self.svg_path.write_text(self.svg)

    def png(self, *, proof=True, **changes):
        # Independent test encoder; these bytes are never published.
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
        row = b"\0" + bytes((209, 46, 60)) * 600 + bytes((53, 165, 91)) * 600
        data = chunk(b"IHDR", struct.pack(">IIBBBBB", 1200, 675, 8, 2, 0, 0, 0))
        data += chunk(b"IDAT", zlib.compress(row * 675))
        if proof:
            values = {"article_sha256": article_fingerprint(self.article),
                      "svg_sha256": hashlib.sha256(self.svg.encode()).hexdigest(),
                      "renderer": "rsvg-convert 2.62.1", **changes}
            for key, value in values.items():
                data += chunk(b"tEXt", key.encode() + b"\0" + value.encode())
        return b"\x89PNG\r\n\x1a\n" + data + chunk(b"IEND", b"")

    def renderer(self):
        return patch.object(social_cards.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 0, "rsvg-convert version 2.62.1\n"),
            subprocess.CompletedProcess([], 0, self.png(proof=False))])

    def test_actual_existing_renderer_preserves_scene_pixels_and_proof(self):
        if not shutil.which("rsvg-convert"):
            self.skipTest("Normal builds do not need the optional preparation executable")
        version = subprocess.run(["rsvg-convert", "--version"], capture_output=True,
                                 text=True, check=True, timeout=5).stdout.splitlines()
        if not version or version[0] != "rsvg-convert version 2.62.1":
            self.skipTest("Optional native proof needs the pinned existing renderer")
        from PIL import Image
        self.assertTrue(social_cards.prepare_scene_png(self.article, root=self.root))
        with Image.open(self.png_path) as image:
            self.assertEqual(image.size, (1200, 675))
            self.assertEqual(image.convert("RGB").getpixel((10, 10)), (209, 46, 60))
            self.assertEqual(image.convert("RGB").getpixel((1190, 665)), (53, 165, 91))
        self.assertEqual(social_cards.reviewed_scene_png(self.article, root=self.root), self.png_path.read_bytes())

    def test_preparation_rejects_a_different_renderer_version_without_writing(self):
        with patch.object(social_cards.subprocess, "run", return_value=
                          subprocess.CompletedProcess([], 0, "rsvg-convert version 2.63.0\n")) as renderer:
            with self.assertRaisesRegex(RuntimeError, "2.62.1"):
                social_cards.prepare_scene_png(self.article, root=self.root)
            renderer.assert_called_once()
        self.assertFalse(self.png_path.exists())
        self.assertFalse(self.png_path.with_suffix(".png.tmp").exists())

    def test_normal_render_never_invokes_a_renderer_and_copies_current_png(self):
        data = self.png()
        self.png_path.write_bytes(data)
        output = self.root / "social.png"
        with patch.object(social_cards.subprocess, "run") as renderer, patch.dict("sys.modules", {"cairosvg": None}):
            social_cards.render_social_card(self.article, output, root=self.root)
            renderer.assert_not_called()
        self.assertEqual(output.read_bytes(), data)

    def test_live_verifier_checks_current_png_bytes_and_dimensions(self):
        data = self.png()
        self.png_path.write_bytes(data)
        verify_scene_social_image(self.article, data, "image/png", root=self.root)
        with self.assertRaisesRegex(RuntimeError, "differs"):
            verify_scene_social_image(self.article, data + b"stale", "image/png", root=self.root)
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            verify_scene_social_image(self.article, data, "image/jpeg", root=self.root)
        self.png_path.unlink()
        generic = self.root / "generic.png"
        social_cards.render_social_card(self.article, generic, root=self.root)
        verify_scene_social_image(self.article, generic.read_bytes(), "image/png", root=self.root)
        with self.assertRaisesRegex(RuntimeError, "dimensions"):
            verify_scene_social_image(self.article, data, "image/png", root=self.root)

    def test_missing_tampered_or_unbound_png_keeps_legacy_generic_fallback(self):
        data = self.png()
        bad = (None, self.png(proof=False), self.png(article_sha256="0" * 64),
               self.png(svg_sha256="0" * 64), self.png(renderer="unrecognized"),
               data[:-5], data + b"extra", data[:45] + b"changed" + data[52:])
        for index, candidate in enumerate(bad):
            with self.subTest(index=index):
                self.png_path.unlink(missing_ok=True)
                if candidate is not None:
                    self.png_path.write_bytes(candidate)
                output = self.root / "fallback.png"
                with patch.object(social_cards.subprocess, "run") as renderer:
                    social_cards.render_social_card(self.article, output, root=self.root)
                    renderer.assert_not_called()
                self.assertEqual(struct.unpack(">II", output.read_bytes()[16:24]), (1200, 630))
                self.assertIsNone(social_cards.reviewed_scene_version(self.article, root=self.root))
        self.png_path.write_bytes(data)
        for svg in (self.svg.replace(article_fingerprint(self.article), "0" * 64), "<svg broken",
                    self.svg.replace("</svg>", '<image href="https://example.com/private.png"/></svg>')):
            self.svg_path.write_text(svg)
            self.assertIsNone(social_cards.reviewed_scene_png(self.article, root=self.root))

    def test_preparation_skips_unchanged_and_atomically_preserves_managed_old_png_on_failure(self):
        with self.renderer() as renderer:
            self.assertTrue(social_cards.prepare_scene_png(self.article, root=self.root))
            self.assertIn("--background-color=#f8f6f0", renderer.call_args.args[0])
        original = self.png_path.read_bytes()
        with patch.object(social_cards.subprocess, "run") as renderer:
            self.assertFalse(social_cards.prepare_scene_png(self.article, root=self.root))
            renderer.assert_not_called()
        self.svg = self.svg.replace("#35a55b", "#1040b0")
        self.svg_path.write_text(self.svg)
        self.assertIsNone(social_cards.reviewed_scene_png(self.article, root=self.root))
        with self.renderer(), patch.object(social_cards.os, "replace", side_effect=OSError("test failure")):
            with self.assertRaises(OSError):
                social_cards.prepare_scene_png(self.article, root=self.root)
        self.assertEqual(self.png_path.read_bytes(), original)
        self.assertFalse(self.png_path.with_suffix(".png.tmp").exists())
        with self.renderer():
            self.assertTrue(social_cards.prepare_scene_png(self.article, root=self.root))
        self.assertNotEqual(self.png_path.read_bytes(), original)

    def test_preparation_refuses_unmanaged_output_and_symlinks_without_touching_other_files(self):
        unmanaged = self.png(proof=False)
        self.png_path.write_bytes(unmanaged)
        with patch.object(social_cards.subprocess, "run") as renderer:
            with self.assertRaisesRegex(RuntimeError, "unmanaged"):
                social_cards.prepare_scene_png(self.article, root=self.root)
            renderer.assert_not_called()
        self.assertEqual(self.png_path.read_bytes(), unmanaged)
        self.png_path.unlink()
        unrelated = self.root / "personal.png"
        unrelated.write_bytes(b"Unrelated personal file")
        self.png_path.symlink_to(unrelated)
        self.assertIsNone(social_cards.reviewed_scene_png(self.article, root=self.root))
        with self.assertRaisesRegex(RuntimeError, "unsafe"):
            social_cards.prepare_scene_png(self.article, root=self.root)
        self.assertEqual(unrelated.read_bytes(), b"Unrelated personal file")

    def test_png_hash_versions_share_page_og_and_rss_with_clean_canonical(self):
        self.png_path.write_bytes(self.png())
        digest = hashlib.sha256(self.png_path.read_bytes()).hexdigest()[:12]
        with patch.object(build, "BASE", self.root), patch.object(build, "OUT", self.root / "dist"), \
                layout_asset_fixture(self.root), patch.object(build, "stylesheet_url", return_value="/assets/test.css"):
            build.make_article_social_images([self.article])
            shared = build.social_image(self.article)
            self.assertEqual(shared, "/og/articles/" + self.article["slug"] + ".png?v=" + digest)
            self.assertEqual(png_card_dimensions(build.SITE + shared), (1200, 675))
            path = build.article_url(self.article)
            canonical = build.SITE + path
            self.assertEqual(share_page_url(self.article, canonical, root=self.root), canonical + "?image=" + digest)
            page = build.layout(self.article["title"], self.article["summary"], path, "", kind="story", article=self.article)
            self.assertEqual(inspect_markup(page, path), (build.SITE + shared, "image/png"))
            self.assertIn('<link rel="canonical" href="' + canonical + '">', page)
            self.assertIn('property="og:url" content="' + canonical + '">', page)
            with self.assertRaisesRegex(RuntimeError, "dimensions"):
                inspect_markup(page.replace('property="og:image:height" content="675"', 'property="og:image:height" content="630"'), path)
            feed = build.generate_rss([self.article], "test", "test", build.SITE + "/", "/feed.xml")
            self.assertIn('height="675"', feed)
            self.assertIn(build.SITE + shared, feed)
            self.assertIn('<link>' + canonical + '</link>', feed)
        self.png_path.write_bytes(self.png(svg_sha256="0" * 64))
        self.assertEqual(share_page_url(self.article, canonical, root=self.root), canonical)

    def test_accepted_jpeg_priority_and_held_jpeg_scene_fallback(self):
        self.png_path.write_bytes(self.png())
        digest = hashlib.sha256(self.png_path.read_bytes()).hexdigest()[:12]
        write_generated(self.article, fake_jpeg(), "sd_xl_base_1.0.safetensors", root=self.root)
        review = write_review_fixture(self.root, self.article)
        with patch.object(build, "BASE", self.root), patch.object(build, "OUT", self.root / "dist"), \
                patch.object(build, "render_social_card") as render:
            build.make_article_social_images([self.article])
            render.assert_not_called()
            self.assertEqual(build.social_image(self.article), generated_url(self.article, root=self.root))
        with patch.object(social_cards.subprocess, "run") as renderer:
            self.assertFalse(social_cards.prepare_scene_png(self.article, root=self.root))
            renderer.assert_not_called()
        payload = json.loads(review.read_text())
        payload["reviews"][self.article["slug"]]["decision"] = "rejected"
        review.write_text(json.dumps(payload))
        canonical = build.SITE + build.article_url(self.article)
        self.assertEqual(share_page_url(self.article, canonical, root=self.root), canonical + "?image=" + digest)


if __name__ == "__main__":
    unittest.main()
