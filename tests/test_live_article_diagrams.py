"""Read-only publication checks catch missing, stale, or misplaced optional SVGs."""
import contextlib
import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import check_live
from scripts.article_diagrams import render_article_diagram, reviewed_article_diagram
from scripts.generated_photos import article_fingerprint


class LiveArticleDiagramsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
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
        self.payload = (
            '<svg xmlns="http://www.w3.org/2000/svg" role="img" viewBox="0 0 720 700" '
            f'data-article-sha256="{article_fingerprint(self.article)}">'
            '<title>倉庫 &amp; 看板</title>'
            '<desc>倉庫だけが移り、&quot;看板&quot;は元の位置に残る。</desc>'
            '<rect x="10" y="10" width="200" height="100" fill="#fff"/>'
            '<text x="20" y="60">浮き倉庫</text></svg>'
        ).encode("utf-8")
        self.path.write_bytes(self.payload)
        self.diagram = reviewed_article_diagram(self.article, root=self.root)
        self.figure = render_article_diagram(self.diagram)
        self.markup = (
            '<figure class="article-hero-figure"><img src="/generated/unchanged-photo.jpg"></figure>'
            '<div class="article-body"><p>倉庫が隣の桟橋へ移った。</p>'
            + self.figure + '<p>看板は元の場所に残った。</p></div>'
        )
        self.fetch = Mock(return_value=(self.payload, "image/svg+xml; charset=utf-8"))

    def verify(self, markup=None, article=None):
        return check_live.verify_article_diagram(
            self.article if article is None else article,
            self.markup if markup is None else markup,
            root=self.root, fetch=self.fetch,
        )

    def test_current_figure_fetches_exactly_one_validated_url_and_decodes_alt_once(self):
        self.assertTrue(self.verify())
        self.fetch.assert_called_once_with(self.diagram["url"])

    def test_default_fetch_uses_the_existing_read_only_get(self):
        with patch.object(check_live, "get", self.fetch):
            self.assertTrue(check_live.verify_article_diagram(self.article, self.markup, root=self.root))
        self.fetch.assert_called_once_with(self.diagram["url"])

    def test_article_without_optional_diagram_needs_no_placeholder_or_extra_get(self):
        self.path.unlink()
        self.assertFalse(self.verify(self.markup.replace(self.figure, "")))
        self.fetch.assert_not_called()

    def test_removed_diagram_cannot_remain_public(self):
        self.path.unlink()
        with self.assertRaisesRegex(RuntimeError, "unexpected or stale"):
            self.verify()
        self.fetch.assert_not_called()

    def test_stale_diagram_is_only_ok_when_absent_from_the_article(self):
        changed = dict(self.article, paragraphs=["倉庫は陸上に固定されていた。"])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(self.verify(self.markup.replace(self.figure, ""), article=changed))
            with self.assertRaisesRegex(RuntimeError, "unexpected or stale"):
                self.verify(article=changed)
        self.fetch.assert_not_called()

    def test_invalid_local_diagram_cannot_bypass_the_same_public_omission_rule(self):
        self.path.write_bytes(b"not SVG")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(self.verify(self.markup.replace(self.figure, "")))
            with self.assertRaisesRegex(RuntimeError, "unexpected or stale"):
                self.verify()
        self.fetch.assert_not_called()

    def test_unapproved_diagram_image_without_figure_is_detected(self):
        self.path.unlink()
        for src in ("/article-diagrams/other.svg", "https://veripsa.com/article-diagrams/other.svg",
                    "../../article-diagrams/other.svg", "/%61rticle-diagrams/other.svg"):
            with self.subTest(src=src), self.assertRaisesRegex(RuntimeError, "unexpected or stale"):
                self.verify('<div class="article-body"><p>本文</p><img src="' + src + '"></div>')
        self.fetch.assert_not_called()

    def test_a_comment_is_not_a_public_diagram(self):
        self.path.unlink()
        self.assertFalse(self.verify(self.markup.replace(self.figure, "<!--" + self.figure + "-->")))
        self.fetch.assert_not_called()

    def test_current_diagram_missing_from_public_article_fails(self):
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            self.verify(self.markup.replace(self.figure, ""))
        self.fetch.assert_not_called()

    def test_wrong_article_or_old_cache_key_fails_before_any_image_get(self):
        for src in ("/article-diagrams/other.svg?v=123", "/article-diagrams/warehouse-moves.svg",
                    "/article-diagrams/warehouse-moves.svg?v=old", "https://elsewhere.test/other.svg"):
            with self.subTest(src=src), self.assertRaises(RuntimeError):
                self.verify(self.markup.replace(self.diagram["url"], src))
        self.fetch.assert_not_called()

    def test_size_alt_and_caption_must_match_the_current_review(self):
        for old, new in (
            ('width="720"', 'width="1200"'), ('height="700"', 'height="675"'),
            ('&quot;看板&quot;', '看板'), ('倉庫 &amp; 看板', '別の記事の解説'),
            ('loading="lazy"', 'loading="eager"'), ('decoding="async"', 'decoding="sync"'),
        ):
            with self.subTest(old=old), self.assertRaises(RuntimeError):
                self.verify(self.markup.replace(old, new))
        self.fetch.assert_not_called()

    def test_responsive_override_and_duplicate_images_are_rejected(self):
        for figure in (
            self.figure.replace('<img ', '<img srcset="/article-diagrams/other.svg 2x" ', 1),
            self.figure.replace('<figcaption>', '<img src="/article-diagrams/other.svg"><figcaption>'),
            self.figure.replace('<figcaption>', '<picture><img src="/article-diagrams/other.svg"></picture><figcaption>'),
        ):
            with self.subTest(figure=figure), self.assertRaises(RuntimeError):
                self.verify(self.markup.replace(self.figure, figure))
        self.fetch.assert_not_called()

    def test_exactly_one_diagram_belongs_directly_after_the_first_paragraph(self):
        no_figure = self.markup.replace(self.figure, "")
        for markup in (
            self.markup.replace(self.figure, self.figure * 2),
            self.figure + no_figure,
            no_figure.replace('<div class="article-body">', '<div class="article-body">' + self.figure),
            no_figure.replace('</div>', self.figure + '</div>'),
            self.markup.replace(self.figure, '<section>' + self.figure + '</section>'),
            self.markup + '<img src="/article-diagrams/other.svg">',
        ):
            with self.subTest(markup=markup), self.assertRaises(RuntimeError):
                self.verify(markup)
        self.fetch.assert_not_called()

    def test_unreachable_svg_fails_even_when_html_is_current(self):
        for failure in (urllib.error.HTTPError(self.diagram["url"], 404, "Not Found", {}, None),
                        urllib.error.URLError("offline"), TimeoutError("slow")):
            self.fetch.side_effect = failure
            with self.subTest(failure=failure), self.assertRaisesRegex(RuntimeError, "unreachable"):
                self.verify()

    def test_svg_cannot_be_an_html_error_page_or_incorrect_mime(self):
        for payload, mime in ((b"<html>Not Found</html>", "text/html"),
                              (self.payload, "application/octet-stream"),
                              (self.payload, "image/svg+xml-fake")):
            self.fetch.return_value = payload, mime
            with self.subTest(mime=mime), self.assertRaisesRegex(RuntimeError, "Content-Type"):
                self.verify()

    def test_stale_cdn_bytes_and_truncated_download_fail(self):
        for payload in (self.payload.replace(b"#fff", b"#000"), self.payload[:-8],
                        self.payload + b" "):
            self.fetch.return_value = payload, "image/svg+xml"
            with self.subTest(payload=payload), self.assertRaisesRegex(RuntimeError, "differs from the reviewed SVG"):
                self.verify()

    def test_real_story_renderer_satisfies_live_contract_without_changing_photo(self):
        from scripts import build
        hero = '<img src="/generated/unchanged-photo.jpg" alt="倉庫の写真">'
        with (patch.object(build, "BASE", self.root),
              patch.object(build, "layout", side_effect=lambda title, desc, path, main, **kw: main),
              patch.object(build, "article_hero_media", return_value=hero),
              patch.object(build, "photo_credit", return_value="再現イメージ（合成）"),
              patch.object(build, "clip_caption", return_value="")):
            markup = build.story(self.article, [self.article], issues={}, serials=[])
        self.assertIn(hero, markup)
        self.assertTrue(self.verify(markup))
        self.fetch.assert_called_once_with(self.diagram["url"])


if __name__ == "__main__":
    unittest.main()
