"""A successful deploy must retain browser and home-screen icons."""
import io
import re
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from scripts import build, check_live

ROOT = Path(__file__).resolve().parents[1]


class LiveIconTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "assets").mkdir()
        self.responses = {}
        for path, mime in (
            ("/assets/favicon-192.png", "image/png"),
            ("/favicon.ico", "image/vnd.microsoft.icon"),
            ("/assets/apple-touch-icon.png", "image/png"),
        ):
            filename = path.rsplit("/", 1)[-1]
            data = (ROOT / "assets" / filename).read_bytes()
            (self.root / "assets" / filename).write_bytes(data)
            self.responses[path] = (data, mime)
        self.requests = []
        self.markup = build.layout("未発生新聞", "アイコンの配信確認", "/", "<p>本文</p>")

    def fetch(self, path):
        self.requests.append(path)
        if path not in self.responses:
            raise HTTPError("https://veripsa.com" + path, 404, "Not Found", {}, None)
        return self.responses[path]

    def verify(self, markup=None):
        with redirect_stdout(io.StringIO()):
            check_live.verify_site_icons(self.markup if markup is None else markup,
                                         fetch=self.fetch, root=self.root)

    def test_current_layout_and_real_icons_pass_with_only_three_gets(self):
        self.verify()
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(set(self.requests), set(self.responses))
        data, _ = self.responses["/favicon.ico"]
        self.responses["/favicon.ico"] = (data, "image/x-icon; charset=binary")
        self.verify()

    def test_missing_conflicting_or_outside_head_declarations_fail(self):
        for path in self.responses:
            declaration = re.search(r'<link\b[^>]*href="' + re.escape(path) + r'"[^>]*>',
                                    self.markup).group(0)
            variants = {
                "removed": self.markup.replace(declaration, ""),
                "duplicated": self.markup.replace(declaration, declaration * 2),
                "wrong size": self.markup.replace(declaration, re.sub(
                    r'sizes="[^"]+"', 'sizes="1x1"', declaration)),
                "outside head": self.markup.replace(declaration, "").replace(
                    "</body>", declaration + "</body>"),
            }
            for label, markup in variants.items():
                with self.subTest(path=path, case=label), self.assertRaisesRegex(
                        RuntimeError, "declaration"):
                    self.verify(markup)

    def test_404_html_fallback_and_stale_bytes_fail_for_each_icon(self):
        for path in list(self.responses):
            original = self.responses[path]
            del self.responses[path]
            with self.subTest(path=path, case="404"), self.assertRaisesRegex(
                    RuntimeError, "not reachable"):
                self.verify()
            for payload, mime, error in (
                (b"<!doctype html>not found", "text/html", "MIME"),
                (b"<!doctype html>not found", original[1], "format or dimensions"),
                (original[0][:-1] + bytes([original[0][-1] ^ 1]), original[1], "current source"),
            ):
                self.responses[path] = (payload, mime)
                with self.subTest(path=path, case=error), self.assertRaisesRegex(RuntimeError, error):
                    self.verify()
            self.responses[path] = original

    def test_wrong_png_dimensions_and_incomplete_ico_frames_fail(self):
        for path in ("/assets/favicon-192.png", "/assets/apple-touch-icon.png"):
            original = self.responses[path]
            data = original[0][:16] + struct.pack(">II", 32, 32) + original[0][24:]
            self.responses[path] = (data, original[1])
            with self.subTest(path=path), self.assertRaisesRegex(RuntimeError, "dimensions"):
                self.verify()
            self.responses[path] = original

        original = self.responses["/favicon.ico"]
        data = original[0]
        first_frame = struct.unpack_from("<I", data, 18)[0]
        variants = (
            data[:4] + struct.pack("<H", 3) + data[6:],
            data[:first_frame + 4] + struct.pack("<i", 65) + data[first_frame + 8:],
            data[:18] + struct.pack("<I", len(data) + 1) + data[22:],
        )
        for candidate in variants:
            self.responses["/favicon.ico"] = (candidate, original[1])
            with self.assertRaisesRegex(RuntimeError, "format or dimensions"):
                self.verify()

    def test_full_release_checker_invokes_the_icon_probe(self):
        with patch.object(check_live, "verify_public_revision", return_value="a" * 40) as revision, \
             patch.object(check_live, "get", return_value=(self.markup.encode(), "text/html")), \
             patch.object(check_live, "verify_site_icons", side_effect=RuntimeError("icon probe")) as probe:
            with self.assertRaisesRegex(RuntimeError, "icon probe"):
                check_live.check()
            revision.assert_called_once_with()
            probe.assert_called_once_with(self.markup)


class IconRobotsTests(unittest.TestCase):
    MANAGED_ROBOTS = """# Cloudflare managed content
User-agent: *
Content-Signal: search=yes,ai-train=no,use=reference
Allow: /
User-agent: Google-Extended
Disallow: /
User-agent: GoogleOther
Disallow: /
User-agent: Google-Agent
Disallow: /
# Repository content
User-agent: *
Allow: /
Sitemap: https://veripsa.com/sitemap.xml
"""

    def verify(self, text):
        # Reuse the caller's robots body. No additional HTTP request is needed.
        with patch.object(check_live, "get", side_effect=AssertionError("unexpected GET")), \
             redirect_stdout(io.StringIO()):
            check_live.verify_icon_crawl_rules(text.encode())

    def test_provider_ai_blocks_do_not_block_search_and_image_bots(self):
        self.verify(self.MANAGED_ROBOTS)
        self.verify("User-agent: *\nAllow: /\n")

    def test_search_homepage_and_each_icon_cannot_be_disallowed(self):
        for bot, path in (("Googlebot", "/"),
                          ("Googlebot-Image", "/assets/favicon-192.png"),
                          ("Googlebot-Image", "/favicon.ico"),
                          ("Googlebot-Image", "/assets/apple-touch-icon.png")):
            text = self.MANAGED_ROBOTS + f"User-agent: {bot}\nDisallow: {path}\n"
            with self.subTest(bot=bot, path=path), self.assertRaisesRegex(RuntimeError, bot):
                self.verify(text)
        # A later wildcard group contributes rules rather than being discarded.
        with self.assertRaisesRegex(RuntimeError, "Googlebot-Image"):
            self.verify(self.MANAGED_ROBOTS + "User-agent: *\nDisallow: /assets/\n")

    def test_more_specific_groups_and_longer_allow_rules_take_precedence(self):
        self.verify("""User-agent: *
Disallow: /
User-agent: Googlebot-Image
Disallow: /assets/
User-agent: Googlebot
Allow: /
User-agent: Googlebot-Image
Allow: /assets/favicon-192.png
Allow: /assets/apple-touch-icon.png
""")
        for rules in ("Disallow: /\nAllow: /", "Allow: /\nDisallow: /"):
            self.verify("User-agent: *\n" + rules)
        with self.assertRaisesRegex(RuntimeError, "Googlebot-Image"):
            self.verify("User-agent: *\nAllow: /\nDisallow: /assets/\n")

    def test_wildcard_and_end_anchored_rules_are_not_silently_ignored(self):
        for rules in ("User-agent: Googlebot-Image\nDisallow: /*.png$\n",
                      "User-agent: Googlebot\nDisallow: /$\n"):
            with self.subTest(rules=rules), self.assertRaisesRegex(RuntimeError, "blocks"):
                self.verify(rules)
        self.verify("User-agent: Googlebot-Image\nDisallow: /$\n")
        with self.assertRaisesRegex(RuntimeError, "Googlebot-Image"):
            self.verify("User-agent: Googlebot-Image\nDisallow: /assets/%66avicon-192.png\n")


if __name__ == "__main__":
    unittest.main()
