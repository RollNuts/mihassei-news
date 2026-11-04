"""Production delivery must include the selected videos and their exact captions."""
import hashlib
import html
import json
import tempfile
import unittest
from pathlib import Path

from scripts.check_live import verify_broadcast_delivery


class LiveBroadcastTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "assets/clips").mkdir(parents=True)
        self.script = b"/* current verified broadcast player */"
        (self.root / "assets/news-broadcast.js").write_bytes(self.script)
        self.script_url = "/assets/news-broadcast.js?v=" + hashlib.sha256(self.script).hexdigest()[:12]
        self.playlist = []
        self.responses = {self.script_url: (self.script, "text/javascript")}
        self.requests = []
        for slug in ("new-story", "previous-story", "third-story"):
            movie = ("verified-" + slug).encode()
            (self.root / "assets/clips" / (slug + ".mp4")).write_bytes(movie)
            video_url = f'/clips/{slug}.mp4?v={hashlib.sha256(movie).hexdigest()[:12]}'
            subtitles = ("WEBVTT\n\n00:00:00.000 --> 00:00:04.000\n" + slug + "\n").encode()
            track_url = f'/clips/{slug}.vtt?v={hashlib.sha256(subtitles).hexdigest()[:12]}'
            self.playlist.append({"slug": slug, "video_url": video_url, "track_url": track_url})
            self.responses[video_url] = movie, "video/mp4"
            self.responses[track_url] = subtitles, "text/vtt; charset=utf-8"

    def markup(self, playlist=None):
        playlist = self.playlist if playlist is None else playlist
        data = html.escape(json.dumps(playlist), quote=True)
        return (f'<section class="broadcast-spotlight" data-broadcast-playlist="{data}">'
                f'<video class="broadcast-video"><source src="{playlist[0]["video_url"]}"></video>'
                f'</section><script defer src="{self.script_url}"></script>')

    def fetch(self, path):
        self.requests.append(path)
        return self.responses[path]

    def verify(self, markup=None):
        verify_broadcast_delivery(self.markup() if markup is None else markup,
                                  self.playlist, fetch=self.fetch, root=self.root)

    def test_checks_all_three_selected_clips_and_tracks_with_bounded_gets(self):
        self.verify()
        self.assertEqual(len(self.requests), 7)
        self.assertEqual(set(self.requests), set(self.responses))

    def test_rejects_stale_playlist_even_when_all_urls_respond(self):
        with self.assertRaisesRegex(RuntimeError, "editorial selection"):
            self.verify(self.markup(self.playlist[::-1]))
        self.assertEqual(self.requests, [])

    def test_rejects_stale_script_and_media_despite_success_status(self):
        for path in (self.script_url, self.playlist[-1]["video_url"]):
            with self.subTest(path=path):
                original = self.responses[path]
                self.responses[path] = b"old deployed bytes", original[1]
                with self.assertRaisesRegex(RuntimeError, "stale|verified clip"):
                    self.verify()
                self.responses[path] = original

    def test_rejects_stale_captions_and_html_fallback(self):
        path = self.playlist[-1]["track_url"]
        for response in ((b"WEBVTT\n\nold caption\n", "text/vtt"),
                         (b"<!doctype html>not found", "text/html")):
            with self.subTest(response=response):
                self.responses[path] = response
                with self.assertRaisesRegex(RuntimeError, "native subtitles"):
                    self.verify()

    def test_no_eligible_video_does_not_force_or_fetch_a_player(self):
        verify_broadcast_delivery("<main>Articles only</main>", [], fetch=self.fetch, root=self.root)
        self.assertEqual(self.requests, [])
        with self.assertRaisesRegex(RuntimeError, "without a verified selection"):
            verify_broadcast_delivery(self.markup(), [], fetch=self.fetch, root=self.root)


if __name__ == "__main__":
    unittest.main()
