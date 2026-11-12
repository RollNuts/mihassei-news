"""A small optional diagram must never remove an article's approved photo."""
import contextlib
import copy
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.article_diagrams import (
    MAX_SVG_BYTES, publish_article_diagrams, render_article_diagram,
    reviewed_article_diagram,
)
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

    def test_invalid_root_missing_description_and_malformed_svg_are_omitted(self):
        variants = [
            self.svg().replace('role="img"', ''),
            self.svg().replace('<desc>', '<desc-unused>').replace('</desc>', '</desc-unused>'),
            self.svg().replace('viewBox="0 0 720 700"', 'viewBox="0 0 0 700"'),
            self.svg().replace('width="720"', 'width="600"'),
            self.svg().replace('xmlns="http://www.w3.org/2000/svg"', ''),
            self.svg()[:-6],
            '<!DOCTYPE svg [<!ENTITY ex "external">]>' + self.svg(),
            '<?xml-stylesheet href="https://example.com/diagram.css"?>' + self.svg(),
            self.svg().replace('role="img"', 'role="img" xml:base="https://example.com/"'),
        ]
        for source in variants:
            with self.subTest(source=source[:95]), contextlib.redirect_stderr(io.StringIO()):
                self.write_svg(source)
                self.assertIsNone(reviewed_article_diagram(self.article, root=self.root))

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


if __name__ == "__main__":
    unittest.main()
