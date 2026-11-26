"""Release checks distinguish a canonical 200 from a redirected or excluded page."""
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from scripts import check_live
from scripts.audit_search_index import Page, excludes_google_index


class LiveIndexabilityTests(unittest.TestCase):
    def test_robots_directives_retain_scope_and_repeated_meta(self):
        page = Page()
        page.feed('<meta name="GoogleBot" content="NONE"><meta name="googlebot" content="index">')
        self.assertTrue(excludes_google_index(page.index_directives))
        self.assertTrue(excludes_google_index(["max-image-preview: none, noindex"], http_header=True))
        self.assertTrue(excludes_google_index(["bingbot: noindex, googlebot: none"], http_header=True))
        self.assertFalse(excludes_google_index(["bingbot: noindex, nofollow", "max-image-preview: none",
                                                "googlebot: index, noimageindex"], http_header=True))

    def test_release_probe_checks_first_response_headers_and_canonical_html(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                requests.append(self.path)
                if self.path == "/moved/":
                    self.send_response(301)
                    self.send_header("Location", "/live/")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("X-Robots-Tag", "otherbot: noindex")
                if self.path == "/excluded-header/":
                    self.send_header("X-Robots-Tag", "Googlebot: NONE")
                self.end_headers()
                path = "/live/" if self.path == "/other-canonical/" else self.path
                markup = f'<link rel="canonical" href="{origin}{path}">'
                if self.path == "/excluded-meta/":
                    markup += '<meta content="noindex" name="ROBOTS"><meta name="robots" content="index">'
                self.wfile.write(markup.encode())

        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            origin = f"http://127.0.0.1:{server.server_port}"
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                with patch.object(check_live, "SITE", origin):
                    check_live.get("/live/", indexable=True)
                    for path, error in (("/moved/", "HTTP 301"), ("/excluded-header/", "X-Robots-Tag"),
                                        ("/excluded-meta/", "Robots meta"), ("/other-canonical/", "canonical")):
                        with self.subTest(path=path), self.assertRaisesRegex(RuntimeError, error):
                            check_live.get(path, indexable=True)
                    self.assertEqual(requests, ["/live/", "/moved/", "/excluded-header/",
                                                "/excluded-meta/", "/other-canonical/"])
                    check_live.get("/excluded-meta/")  # Deliberate noindex on a utility remains valid.
            finally:
                server.shutdown()
                thread.join()
