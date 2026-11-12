"""A small optional diagram must never remove an article's approved photo."""
import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.article_diagrams import (
    MAX_SVG_BYTES, article_diagram_state, publish_article_diagrams, render_article_diagram,
    reviewed_article_diagram,
)
from scripts import visual_review
from scripts.editorial_covers import story_subject
from scripts.generated_photos import article_fingerprint


class ArticleDiagramsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.article = {
            "slug": "warehouse-moves", "title": "倉庫が移動", "summary": "平たい船に載る倉庫。",
            "category": "事件", "paragraphs": ["倉庫が隣の桟橋へ移った。", "看板は元の場所に残った。"],
            "scene_prompt": "A floating warehouse beside a pier.",
            "fiction_at": "2031-11-12T07:30:00+09:00",
            "published_at": "2026-11-12T07:30:00+09:00", "art": "notice", "tags": ["港"],
            "source": {"url": "https://example.com/news", "publisher": "資料", "topic": "港の移動",
                       "published_at": "2026-11-11T10:00:00+09:00"},
        }
        self.path = self.root / "assets/article-diagrams/warehouse-moves.svg"
        self.path.parent.mkdir(parents=True)

    def svg(self, article=None):
        article = article or self.article
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" role="img" viewBox="0 0 720 700" '
            'width="720" height="700" '
            f'data-article-sha256="{article_fingerprint(article)}">'
            '<title>倉庫 &amp; 看板</title><desc>倉庫だけが移り、&quot;看板&quot;は元の位置に残る。</desc>'
            '<rect x="10" y="10" width="200" height="100" fill="#fff"/>'
            '<text x="20" y="60" font-size="36">浮き倉庫</text></svg>'
        )

    def write_svg(self, source=None):
        self.path.write_text(self.svg() if source is None else source, encoding="utf-8")

    def write_articles(self, articles=None):
        path = self.root / "content/articles.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([self.article] if articles is None else articles), encoding="utf-8")

    def report(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = visual_review.main(["--root", str(self.root), *args])
        self.assertEqual(code, 0, stdout.getvalue())
        return json.loads(stdout.getvalue())

    def write_accepted_photo(self):
        """A JPEG-header fixture for the integrity gate, not a real visual review."""
        self.write_articles()
        data = (b"\xff\xd8\xff\xc0\x00\x11\x08" + (675).to_bytes(2, "big")
                + (1200).to_bytes(2, "big")
                + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
                + b"\x00" * 17000 + b"\xff\xd9")
        photo = self.root / "assets/generated" / (self.article["slug"] + ".jpg")
        photo.parent.mkdir(parents=True, exist_ok=True)
        photo.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        generation = {
            "origin": "local-comfyui", "model": "fixture.safetensors", "prompt_version": 2,
            "article_sha256": article_fingerprint(self.article), "sha256": digest, "bytes": len(data),
        }
        manifest = photo.parent / "manifest.json"
        manifest.write_text(json.dumps({"version": 1, "images": {self.article["slug"]: generation},
                                        "rejected": {}}), encoding="utf-8")
        reviews = self.root / "content/visual_reviews.json"
        reviews.write_text(json.dumps({"version": 1, "reviews": {self.article["slug"]: {
            "decision": "accepted", "article_sha256": article_fingerprint(self.article),
            "image_sha256": digest, "reviewed_at": "2026-11-12T01:30:05+00:00",
            "note": "Synthetic test fixture; not an actual visual review.",
        }}}), encoding="utf-8")
        return photo, manifest, reviews

    def old_editorial_scene(self):
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 675" '
            'role="img" aria-label="テスト用の創作イラスト" '
            f'data-visual-topic="{story_subject(self.article)}" '
            f'data-article-sha256="{"0" * 64}">'
            '<title>Synthetic fixture, not an actual reviewed image</title>'
            '<rect width="1200" height="675" fill="#ffffff"/></svg>'
        )

    def test_missing_optional_file_is_silent_and_needs_no_placeholder(self):
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertIsNone(reviewed_article_diagram(self.article, root=self.root))
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(render_article_diagram(None), "")

    def test_valid_drawing_uses_exact_bytes_accessible_text_and_reserved_size(self):
        self.write_svg()
        before = copy.deepcopy(self.article)
        diagram = reviewed_article_diagram(self.article, root=self.root)
        self.assertIsNotNone(diagram)
        expected_sha = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.assertEqual(diagram["svg_sha256"], expected_sha)
        html = render_article_diagram(diagram)
        self.assertIn(f'/article-diagrams/warehouse-moves.svg?v={expected_sha[:12]}', html)
        self.assertIn('width="720" height="700"', html)
        self.assertIn('alt="倉庫だけが移り、&quot;看板&quot;は元の位置に残る。"', html)
        self.assertIn('<figcaption>倉庫 &amp; 看板</figcaption>', html)
        self.assertIn('loading="lazy" decoding="async"', html)
        self.assertNotIn("<svg", html)
        self.assertEqual(self.article, before)

    def test_changed_svg_gets_a_different_cache_key(self):
        self.write_svg()
        first = reviewed_article_diagram(self.article, root=self.root)
        self.write_svg(self.svg().replace("浮き倉庫", "移動した倉庫"))
        second = reviewed_article_diagram(self.article, root=self.root)
        self.assertNotEqual(first["url"], second["url"])

    def test_body_changes_omit_old_diagram_and_report_reason_only_once(self):
        self.write_svg()
        changed = {**self.article, "paragraphs": ["倉庫は岸の上に建っている。", "元の説明と異なる。"]}
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            for _ in range(2):
                self.assertIsNone(reviewed_article_diagram(changed, root=self.root))
        self.assertEqual(stderr.getvalue().count("article fingerprint changed"), 1)

    def test_status_is_json_safe_silent_and_distinguishes_absent_current_stale_and_invalid(self):
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            absent = article_diagram_state(self.article, root=self.root)
            self.write_svg()
            current = article_diagram_state(self.article, root=self.root)
            changed = {**self.article, "paragraphs": ["別の倉庫が陸上へ移動した。"]}
            stale = article_diagram_state(changed, root=self.root)
            self.write_svg(self.svg().replace('</svg>', '<script>alert(1)</script></svg>'))
            invalid = article_diagram_state(changed, root=self.root)
        self.assertEqual(stderr.getvalue(), "")
        states = json.loads(json.dumps([absent, current, stale, invalid]))
        self.assertEqual([state["status"] for state in states], ["absent", "current", "stale", "invalid"])
        self.assertEqual({state["path"] for state in states}, {"assets/article-diagrams/warehouse-moves.svg"})
        self.assertEqual(stale["reason"], "article_fingerprint_changed")
        self.assertEqual(invalid["reason"], "unsupported_svg_element")
        self.assertEqual((current["width"], current["height"]), (720, 700))
        self.assertIn(current["svg_sha256"][:12], current["url"])

    def test_invalid_root_missing_description_and_malformed_svg_are_omitted(self):
        variants = [
            self.svg().replace('role="img"', ''),
            self.svg().replace('<desc>', '<desc-unused>').replace('</desc>', '</desc-unused>'),
            self.svg().replace('viewBox="0 0 720 700"', 'viewBox="0 0 0 700"'),
            self.svg().replace('width="720"', 'width="600"'),
            self.svg().replace('xmlns="http://www.w3.org/2000/svg"', ''),
            self.svg().replace(article_fingerprint(self.article), ''),
            self.svg()[:-6],
            '<!DOCTYPE svg [<!ENTITY ex "external">]>' + self.svg(),
            '<?xml-stylesheet href="https://example.com/diagram.css"?>' + self.svg(),
            self.svg().replace('role="img"', 'role="img" xml:base="https://example.com/"'),
        ]
        for source in variants:
            with self.subTest(source=source[:95]), contextlib.redirect_stderr(io.StringIO()):
                self.write_svg(source)
                self.assertIsNone(reviewed_article_diagram(self.article, root=self.root))
                self.assertEqual(article_diagram_state(self.article, root=self.root)["status"], "invalid")

    def test_active_embedded_and_external_content_is_omitted(self):
        fragments = [
            '<script>alert(1)</script>', '<foreignObject>HTML</foreignObject>',
            '<image href="https://example.com/pixel.png"/>',
            '<style>@import "https://example.com/style.css";</style>',
            '<rect onclick="alert(1)"/>', '<rect style="fill:red"/>',
            '<use href="https://example.com/diagram.svg#shape"/>',
            '<path fill="url(https://example.com/paint.svg#x)"/>',
            '<path fill="u\\72l(https://example.com/paint.svg#x)"/>',
            '<animate attributeName="href" values="https://example.com"/>',
        ]
        for fragment in fragments:
            with self.subTest(fragment=fragment), contextlib.redirect_stderr(io.StringIO()):
                self.write_svg(self.svg().replace('</svg>', fragment + '</svg>'))
                self.assertIsNone(reviewed_article_diagram(self.article, root=self.root))
                self.assertEqual(article_diagram_state(self.article, root=self.root)["status"], "invalid")

    def test_local_references_are_allowed(self):
        source = self.svg().replace('</svg>', '<defs><marker id="arrow"><path d="M0 0L5 5"/></marker></defs>'
                                   '<path marker-end="url(#arrow)" d="M20 20L30 30"/></svg>')
        self.write_svg(source)
        self.assertIsNotNone(reviewed_article_diagram(self.article, root=self.root))

    def test_paths_symlinks_and_large_assets_are_rejected(self):
        self.assertIsNone(reviewed_article_diagram({**self.article, "slug": "../outside"}, root=self.root))
        outside = self.root / "other.svg"
        outside.write_text(self.svg(), encoding="utf-8")
        self.path.symlink_to(outside)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(reviewed_article_diagram(self.article, root=self.root))
        self.path.unlink()
        self.path.write_bytes(b" " * (MAX_SVG_BYTES + 1))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(reviewed_article_diagram(self.article, root=self.root))

    def test_publication_copies_only_current_validated_bytes(self):
        self.write_svg()
        other = {**self.article, "slug": "other-story"}
        (self.path.parent / "other-story.svg").write_text(self.svg(), encoding="utf-8")
        out = self.root / "dist"
        with contextlib.redirect_stderr(io.StringIO()):
            publish_article_diagrams([self.article, other], out, root=self.root)
        public = out / "article-diagrams/warehouse-moves.svg"
        self.assertEqual(public.read_bytes(), self.path.read_bytes())
        self.assertFalse((out / "article-diagrams/other-story.svg").exists())
        self.assertEqual(list(out.rglob("*.svg")), [public])

    def test_story_inserts_one_figure_after_first_paragraph_and_keeps_photo(self):
        from scripts import build
        hero = '<img src="/generated/unchanged-photo.jpg" alt="倉庫の写真">'
        with (patch.object(build, "BASE", self.root),
              patch.object(build, "layout", side_effect=lambda title, desc, path, main, **kw: main),
              patch.object(build, "article_hero_media", return_value=hero),
              patch.object(build, "photo_credit", return_value="再現イメージ（合成）"),
              patch.object(build, "clip_caption", return_value="")):
            before = build.story(self.article, [self.article], issues={}, serials=[])
            self.write_svg()
            after = build.story(self.article, [self.article], issues={}, serials=[])
            figure = render_article_diagram(reviewed_article_diagram(self.article, root=self.root))
            self.assertEqual(after.replace(figure, ""), before)
            self.assertEqual(after.count('class="article-diagram"'), 1)
            self.assertIn(hero, after)
            self.assertLess(after.index(hero), after.index(figure))
            self.assertIn('<p>倉庫が隣の桟橋へ移った。</p>' + figure + '<p>看板は元の場所に残った。</p>', after)

    def test_diagram_report_preserves_accepted_photo_and_flags_only_existing_broken_diagrams(self):
        photo_files = self.write_accepted_photo()
        original = {path: path.read_bytes() for path in photo_files}
        publication = None
        sources = {
            "absent": None,
            "current": self.svg(),
            "stale": self.svg().replace(article_fingerprint(self.article), "0" * 64),
            "invalid": self.svg().replace('</svg>', '<script>alert(1)</script></svg>'),
        }
        for status, source in sources.items():
            with self.subTest(status=status):
                if source is None:
                    self.path.unlink(missing_ok=True)
                else:
                    self.write_svg(source)
                report = self.report()
                row = report["images"][0]
                self.assertEqual(row["status"], "accepted")
                self.assertEqual(row["publication"]["kind"], "generated_jpeg")
                publication = publication or row["publication"]
                self.assertEqual(row["publication"], publication)
                self.assertEqual(row["article_diagram"]["status"], status)
                self.assertEqual(report["article_diagram_counts"], {
                    key: int(key == status) for key in sources
                })
                reasons = [item["reason"] for item in report["attention"]]
                self.assertEqual(reasons, [status + "_article_diagram"] if status in ("stale", "invalid") else [])
                if report["attention"]:
                    self.assertIn("assets/article-diagrams/warehouse-moves.svg", report["attention"][0]["next_action"])
        self.assertEqual({path: path.read_bytes() for path in photo_files}, original)

    def test_broken_diagram_does_not_hide_missing_primary_photo_action(self):
        self.write_articles()
        self.write_svg(self.svg().replace(article_fingerprint(self.article), "0" * 64))
        report = self.report()
        self.assertEqual(report["images"][0]["publication"]["kind"], "generic_fallback")
        self.assertEqual({item["reason"] for item in report["attention"]},
                         {"generic_fallback", "stale_article_diagram"})

    def test_inactive_old_photo_scene_only_needs_repair_when_selected_photo_is_unavailable(self):
        path = self.root / "assets/editorial-scenes/warehouse-moves.svg"
        path.parent.mkdir(parents=True)
        variants = {
            "stale": self.old_editorial_scene(),
            "invalid": self.old_editorial_scene().replace("0" * 64, "invalid"),
        }
        for status, source in variants.items():
            with self.subTest(status=status):
                photo, _manifest, _reviews = self.write_accepted_photo()
                path.write_text(source, encoding="utf-8")
                report = self.report()
                row = report["images"][0]
                self.assertEqual(row["editorial_scene"]["status"], status)
                self.assertEqual(row["article_diagram"]["status"], "absent")
                self.assertEqual(row["publication"]["kind"], "generated_jpeg")
                self.assertEqual(report["attention"], [])
                # An accepted JPEG is irrelevant if the explicit strategy does
                # not select it. The broken illustration still needs attention.
                self.write_articles([{**self.article, "visual_strategy": "illustration"}])
                illustration = self.report()
                self.assertEqual(illustration["images"][0]["publication"]["kind"], "generic_fallback")
                self.assertEqual([item["reason"] for item in illustration["attention"]], [status + "_editorial_scene"])
                self.write_articles()
                photo.unlink()
                missing = self.report()
                self.assertEqual(missing["images"][0]["publication"]["kind"], "generic_fallback")
                self.assertEqual([item["reason"] for item in missing["attention"]], [status + "_editorial_scene"])

    def test_cli_repeatable_slug_filters_rows_counts_and_attention_without_duplicate_work(self):
        other = {**self.article, "slug": "second-story"}
        excluded = {**self.article, "slug": "excluded-story"}
        self.write_articles([self.article, other, excluded])
        (self.path.parent / "second-story.svg").write_text(self.svg(), encoding="utf-8")
        (self.path.parent / "excluded-story.svg").write_text("broken SVG", encoding="utf-8")
        self.assertEqual(len(self.report()["images"]), 3)
        with patch.object(visual_review, "_publication_row", wraps=visual_review._publication_row) as check:
            report = self.report("--slug", other["slug"], "--slug", self.article["slug"],
                                 "--slug", other["slug"])
        self.assertEqual(check.call_count, 2)
        self.assertEqual([row["slug"] for row in report["images"]], [other["slug"], self.article["slug"]])
        self.assertEqual(report["counts"], {"accepted": 0, "pending": 0, "rejected": 0, "missing": 2})
        self.assertEqual(report["publication_counts"]["generic_fallback"], 2)
        self.assertEqual(report["article_diagram_counts"], {"absent": 1, "current": 0, "stale": 1, "invalid": 0})
        self.assertEqual({item["slug"] for item in report["attention"]}, {self.article["slug"], other["slug"]})

    def test_cli_unknown_slug_fails_instead_of_reporting_no_pending_work(self):
        self.write_articles()
        with (patch.object(visual_review, "_publication_row") as check,
              contextlib.redirect_stdout(io.StringIO()) as stdout):
            code = visual_review.main(["--root", str(self.root), "--slug", self.article["slug"],
                                       "--slug", "unknown-story"])
        self.assertEqual(code, 2)
        check.assert_not_called()
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["slugs"], ["unknown-story"])
        self.assertIn("error", result)
        self.assertNotIn("attention", result)


if __name__ == "__main__":
    unittest.main()
