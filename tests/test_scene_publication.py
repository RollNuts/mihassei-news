"""Decode real JPEG fixtures through build/OGP/RSS; these are not AI photographs."""
import contextlib
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch
from xml.etree import ElementTree as ET

from PIL import Image
from scripts import build, local_scene_photos as photos
from scripts.check_live import verify_generated_image
from scripts.generated_photos import article_fingerprint, generated_scene_entry, usable_generated_scene, generated_url
from scripts.visual_review import reviewed_generated_scene
from scripts.social_cards import reviewed_scene_png

ROOT = Path(__file__).resolve().parents[1]
MODEL = "sd_xl_base_1.0.safetensors"


class ScenePublicationTests(unittest.TestCase):
    def test_validated_jpegs_reach_pages_ogp_and_rss_and_bad_image_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "site"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", "dist", "__pycache__"))
            article_bytes = (root / "content/articles.json").read_bytes()
            articles = json.loads(article_bytes)
            eligible = [a for a in articles
                        if a.get("visual_strategy", "cpu-photo") == "cpu-photo"
                        and not a.get("followup_of")]
            with patch.object(build, "BASE", root):
                lead = build.editorial_lead(eligible)
            selected = []
            if lead in eligible:
                selected.append(lead)
            selected.extend(a for a in eligible
                            if a["slug"] not in {item["slug"] for item in selected})
            selected = selected[:3]
            self.assertEqual(len(selected), 3)
            self.assertEqual(len({a["slug"] for a in selected}), 3)
            self.assertEqual(selected[0], lead)
            identities = {a["slug"]: (article_fingerprint(a), photos.stable_seed(a)) for a in selected}
            # These three standalone articles cover the hero, accepted JPEGs
            # and a corrupt fallback. Keep both real builds without rebuilding
            # the unrelated archive; full-site builds have separate coverage.
            article_path = root / "content/articles.json"
            article_path.write_text(json.dumps(selected, ensure_ascii=False), encoding="utf-8")
            self.assertEqual({a["slug"]: (article_fingerprint(a), photos.stable_seed(a))
                              for a in json.loads(article_path.read_text())}, identities)
            review_path = root / "content/visual_reviews.json"
            reviews = json.loads(review_path.read_text(encoding="utf-8"))
            for article in selected:
                png = io.BytesIO()
                Image.effect_noise((1024, 576), 50).convert("RGB").save(png, format="PNG")
                data = photos.png_to_jpeg(png.getvalue())
                photos.write_generated(article, data, MODEL, root=root)
                verify_generated_image(article, data, "image/jpeg", root=root)
                self.assertTrue(usable_generated_scene(article, root=root))
                record = generated_scene_entry(article, root=root)
                self.assertEqual((record["article_sha256"], record["seed"]), identities[article["slug"]])
                self.assertIs(type(record["seed"]), int)
                self.assertEqual(record["sha256"], hashlib.sha256(data).hexdigest())
                reviews["reviews"].pop(article["slug"], None)
            review_path.write_text(json.dumps(reviews), encoding="utf-8")
            for article in selected:
                self.assertFalse(reviewed_generated_scene(article, root=root))
            # Valid bytes remain private until the exact article/image pair
            # receives a review. Exercise the actual build in this temp copy.
            with patch.object(build, "BASE", root), patch.object(build, "OUT", root / "dist"), \
                 contextlib.redirect_stdout(io.StringIO()):
                build.build()
            output = root / "dist"
            feed = ET.fromstring((output / "feed.xml").read_bytes())
            entries = {e.findtext("link"): e for e in feed.findall("./channel/item")}
            for article in selected:
                slug = article["slug"]
                jpeg_url = "/generated/" + slug + ".jpg"
                paired = reviewed_scene_png(article, root=root)
                version = hashlib.sha256(paired).hexdigest()[:12] if paired else "20261103-topic2"
                social_url = "/og/articles/" + slug + ".png?v=" + version
                self.assertFalse((output / jpeg_url.lstrip("/")).exists())
                pages = ["archive/index.html", "articles/" + slug + "/index.html"]
                if article == lead:
                    pages.append("index.html")
                for page in pages:
                    markup = (output / page).read_text()
                    self.assertIn('src="/images/' + slug + '.svg"', markup, page)
                    self.assertNotIn(jpeg_url, markup, page)
                page = (output / "articles" / slug / "index.html").read_text()
                self.assertIn('property="og:image" content="' + build.SITE + social_url + '"', page)
                self.assertIn('name="twitter:image" content="' + build.SITE + social_url + '"', page)
                self.assertIn('<figcaption>創作イラスト', page)
                media = entries[build.SITE + "/articles/" + slug + "/"].find("{http://search.yahoo.com/mrss/}content")
                self.assertEqual(media.attrib["url"], build.SITE + social_url)
                self.assertEqual(media.attrib["type"], "image/png")
                with Image.open(output / "og/articles" / (slug + ".png")) as exported:
                    exported.load()
                    self.assertEqual(exported.size, (1200, 675 if paired else 630))
                if paired is not None:
                    self.assertEqual((output / "og/articles" / (slug + ".png")).read_bytes(), paired)
            # Synthetic reviews belong only to these decoded test fixtures;
            # generation has not automatically accepted the new image bytes.
            for article in selected:
                record = generated_scene_entry(article, root=root)
                reviews["reviews"][article["slug"]] = {
                    "decision": "accepted",
                    "article_sha256": article_fingerprint(article),
                    "image_sha256": record["sha256"],
                    "reviewed_at": "2026-11-04T01:30:05+00:00",
                    "note": "Synthetic test fixture; not an actual visual review.",
                }
            review_path.write_text(json.dumps(reviews), encoding="utf-8")
            for article in selected:
                self.assertTrue(reviewed_generated_scene(article, root=root))
            # Simulate a corrupted/stale cached payload with the right dimensions.
            stale = bytearray(data)
            stale[-10] ^= 1
            with self.assertRaisesRegex(RuntimeError, "differs"):
                verify_generated_image(selected[-1], bytes(stale), "image/jpeg", root=root)
            (root / "assets/generated" / (selected[-1]["slug"] + ".jpg")).write_bytes(stale)
            self.assertFalse(usable_generated_scene(selected[-1], root=root))
            self.assertFalse(reviewed_generated_scene(selected[-1], root=root))
            with patch.object(build, "BASE", root), patch.object(build, "OUT", root / "dist"), \
                 contextlib.redirect_stdout(io.StringIO()):
                build.build()
            output = root / "dist"
            feed = ET.fromstring((output / "feed.xml").read_bytes())
            entries = {e.findtext("link"): e for e in feed.findall("./channel/item")}
            for article in selected[:2]:
                slug = article["slug"]
                url = generated_url(article, root=root)
                self.assertEqual(url, "/generated/" + slug + ".jpg?v=" + generated_scene_entry(article, root=root)["sha256"][:12])
                pages = ["archive/index.html", "articles/" + slug + "/index.html"]
                if article == lead:
                    pages.append("index.html")
                for page in pages:
                    self.assertTrue('src="' + url + '"' in (output / page).read_text(), page)
                page = (output / "articles" / slug / "index.html").read_text()
                self.assertIn('property="og:image" content="https://veripsa.com' + url + '"', page)
                self.assertIn('name="twitter:image" content="https://veripsa.com' + url + '"', page)
                self.assertIn('data-fallback="/images/' + slug + '.svg"', page)
                self.assertIn('width="1200" height="675"', page)
                self.assertIn("再現イメージ（合成）", page)
                media = entries[build.SITE + "/articles/" + slug + "/"].find("{http://search.yahoo.com/mrss/}content")
                self.assertEqual(media.attrib["url"], build.SITE + url)
                self.assertEqual(media.attrib["type"], "image/jpeg")
                with Image.open(output / urlsplit(url).path.lstrip("/")) as exported:
                    exported.load()
                    self.assertEqual(exported.size, (1200, 675))
                self.assertFalse(any("?" in path.name for path in (output / "generated").iterdir()))
            broken = selected[-1]["slug"]
            self.assertFalse((output / "generated" / (broken + ".jpg")).exists())
            page = (output / "articles" / broken / "index.html").read_text()
            self.assertIn('src="/images/' + broken + '.svg"',
                          page)
            paired = reviewed_scene_png(selected[-1], root=root)
            version = hashlib.sha256(paired).hexdigest()[:12] if paired else "20261103-topic2"
            social_url = "/og/articles/" + broken + ".png?v=" + version
            self.assertIn('property="og:image" content="' + build.SITE + social_url + '"', page)
            self.assertIn('name="twitter:image" content="' + build.SITE + social_url + '"', page)
            media = entries[build.SITE + "/articles/" + broken + "/"].find("{http://search.yahoo.com/mrss/}content")
            self.assertEqual(media.attrib["url"], build.SITE + social_url)
            self.assertEqual(media.attrib["type"], "image/png")
            fallback = output / "og/articles" / (broken + ".png")
            self.assertTrue(fallback.exists())
            if paired is not None:
                self.assertEqual(fallback.read_bytes(), paired)
            self.assertFalse((output / "generated/manifest.json").exists())
            self.assertEqual((ROOT / "content/articles.json").read_bytes(), article_bytes)


if __name__ == "__main__":
    unittest.main()
