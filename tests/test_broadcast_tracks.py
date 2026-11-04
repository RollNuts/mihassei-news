"""Native video subtitles retain the vetted story and the actual shot timing."""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import unittest
from unittest.mock import patch

from scripts import build


class VideoTracks(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.in_video = False
        self.tracks = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        if tag == "video":
            self.in_video = True
        if tag == "track":
            self.tracks.append((self.in_video, dict(attrs)))

    def handle_endtag(self, tag):
        if tag == "video":
            self.in_video = False


class BroadcastTrackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.article = next(a for a in build.load()
                           if a["slug"] == "bakery-no-important-announcement-panic")
        cls.clip = {
            "motion_style_version": 3, "duration_ms": 12000,
            "sha256": "c" * 64, "created_at": "2026-11-04T00:00:00+00:00",
        }

    def test_native_subtitles_cover_all_three_shots_without_overlap(self):
        cues = build.broadcast_cues(self.article, self.clip)
        content = build.broadcast_vtt(self.article, self.clip)
        blocks = content.strip().split("\n\n")
        self.assertEqual(blocks[0], "WEBVTT")
        self.assertEqual(len(blocks), 4)
        self.assertEqual([block.splitlines()[0] for block in blocks[1:]], [
            "00:00:00.000 --> 00:00:04.000",
            "00:00:04.000 --> 00:00:08.000",
            "00:00:08.000 --> 00:00:12.000",
        ])
        self.assertEqual([block.splitlines()[1:] for block in blocks[1:]],
                         [[cue["label"], cue["text"]] for cue in cues])
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
        self.assertEqual(build.broadcast_track_url(self.article, self.clip),
                         "/clips/bakery-no-important-announcement-panic.vtt?v=" + digest)

    def test_home_and_article_attach_native_track_inside_the_actual_video(self):
        with patch.object(build, "usable_clip", return_value=self.clip):
            surfaces = (build.broadcast_feature([self.article]),
                        build.article_hero_media(self.article))
        for markup in surfaces:
            tracks = VideoTracks(markup).tracks
            self.assertEqual(len(tracks), 1)
            inside_video, attrs = tracks[0]
            self.assertTrue(inside_video)
            self.assertEqual(attrs["kind"], "subtitles")
            self.assertEqual(attrs["srclang"], "ja")
            self.assertEqual(attrs["label"], "日本語")
            self.assertEqual(attrs["src"], build.broadcast_track_url(self.article, self.clip))
            self.assertIn("data-broadcast-track", attrs)
            self.assertNotIn("default", attrs, "the inline overlay must not have duplicate captions")

    def test_revised_unselected_and_legacy_stories_do_not_publish_stale_subtitles(self):
        cases = (
            ({**self.article, "summary": "本文を改稿したニュース"}, self.clip),
            ({**self.article, "slug": "unselected-news-story"}, self.clip),
            (self.article, {**self.clip, "motion_style_version": 2, "duration_ms": 4000}),
        )
        for article, clip in cases:
            with self.subTest(slug=article["slug"], style=clip["motion_style_version"]):
                self.assertEqual(build.broadcast_vtt(article, clip), "")
                self.assertEqual(build.broadcast_track_url(article, clip), "")
                self.assertEqual(build.broadcast_track(article, clip), "")
        with patch.object(build, "usable_clip", return_value=None):
            self.assertEqual(VideoTracks(build.article_hero_media(self.article)).tracks, [])

    def test_editorial_text_cannot_inject_vtt_tags_or_add_cue_blocks(self):
        cues = [dict(cue) for cue in build.broadcast_cues(self.article, self.clip)]
        cues[0]["text"] = '<b>パン</b> & お知らせ\n\n続報'
        with patch.object(build, "broadcast_cues", return_value=cues):
            content = build.broadcast_vtt(self.article, self.clip)
        self.assertNotIn("<b>", content)
        self.assertIn("&lt;b&gt;パン&lt;/b&gt; &amp; お知らせ 続報", content)
        self.assertEqual(len(content.strip().split("\n\n")), 4)

    def test_caption_edit_refreshes_the_subtitle_url_without_reencoding_video(self):
        original_url = build.broadcast_track_url(self.article, self.clip)
        movie_url = build.clip_url(self.article)
        cues = [dict(cue) for cue in build.broadcast_cues(self.article, self.clip)]
        cues[2]["text"] = "翌朝は、店主がスマホを伏せた"
        with patch.object(build, "broadcast_cues", return_value=cues):
            changed_url = build.broadcast_track_url(self.article, self.clip)
        self.assertNotEqual(original_url, changed_url)
        self.assertEqual(original_url.split("?")[0], changed_url.split("?")[0])
        self.assertEqual(build.clip_url(self.article), movie_url)


if __name__ == "__main__":
    unittest.main()
