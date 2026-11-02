"""Original long-form serialization: published chapters, safe links and disclosures."""
import json
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from scripts.build import (
    BASE, SITE, load_serials, serial_index, serial_overview,
    serial_chapter, serial_rss, serial_episode_url, series_url,
)

OUT = BASE / "dist"


class SeriesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The main SiteTests class builds dist in the same unittest suite;
        # don't force a duplicate site build in the serial tests.
        cls.serials=load_serials()

    def test_two_distinct_original_series_have_real_first_chapters(self):
        self.assertEqual(len(self.serials),2)
        self.assertEqual({s["kind"] for s in self.serials},{"mystery","market-fiction"})
        ids=set()
        for serial in self.serials:
            self.assertNotIn(serial["slug"],ids)
            ids.add(serial["slug"])
            self.assertTrue(serial["episodes"])
            for episode in serial["episodes"]:
                self.assertGreaterEqual(len("".join(episode["paragraphs"])),650)
                self.assertEqual(episode["published_on"],"2026-11-03")
                self.assertNotIn("2031-11-",episode["published_on"])
                self.assertEqual(
                    serial_episode_url(serial,episode),
                    "/series/"+serial["slug"]+"/"+episode["slug"]+"/",
                )

    def test_market_episodes_cannot_appear_as_investment_recommendations(self):
        market=next(s for s in self.serials if s["kind"]=="market-fiction")
        self.assertIn("架空",market["disclaimer"])
        self.assertIn("投資助言",market["disclaimer"])
        self.assertIn("売買",market["disclaimer"])
        story=serial_chapter(market,market["episodes"][0])
        self.assertIn(market["disclaimer"],story)
        self.assertIn('"@type": "CreativeWork"',story)
        self.assertIn('"datePublished": "2026-11-03"',story)
        self.assertNotIn('"datePublished": "2031',story)
        self.assertIn('name="twitter:card" content="summary_large_image"',story)

    def test_original_episode_links_and_future_chapter_honesty(self):
        index=serial_index(self.serials)
        self.assertIn('<h1>未発生新聞の連載</h1>',index)
        self.assertIn('/series/missing-five-minutes/',index)
        self.assertIn('/series/market-after-tomorrow/',index)
        for serial in self.serials:
            page=serial_overview(serial)
            self.assertIn(serial["disclaimer"],page)
            self.assertIn('href="'+series_url(serial)+'"',page)
            self.assertIn('href="'+serial_episode_url(serial,serial["episodes"][0])+'"',page)
            self.assertIn('href="'+"/feeds/series-"+serial["slug"]+".xml"+'"',page)
            episode=serial["episodes"][0]
            chapter=serial_chapter(serial,episode)
            self.assertIn('href="'+series_url(serial)+'"',chapter)
            self.assertIn("続きはまだ公開されていません",chapter)
            self.assertNotIn("次の話：",chapter)
            self.assertIn("現実の公開日：2026-11-03",chapter)

    def test_series_feeds_only_include_actual_released_chapters(self):
        for serial in self.serials:
            feed=ET.fromstring(serial_rss(serial))
            channel=feed.find("channel")
            self.assertIn("創作連載",channel.findtext("title"))
            entries=channel.findall("item")
            self.assertEqual(len(entries),len(serial["episodes"]))
            self.assertLessEqual(len(entries),30)
            for entry, episode in zip(entries,reversed(serial["episodes"])):
                uri=SITE+serial_episode_url(serial,episode)
                self.assertEqual(entry.findtext("link"),uri)
                self.assertEqual(entry.findtext("guid"),uri)
                self.assertIn("創作",entry.findtext("description"))
                # Do not manufacture a clock time of midnight as if known.
                self.assertIsNone(entry.find("pubDate"))

    def test_published_pages_exist_in_main_build_when_available(self):
        if not (OUT / "series/index.html").is_file():
            self.skipTest("Dist output not built yet")
        index=(OUT / "series/index.html").read_text(encoding="utf-8")
        self.assertIn("未発生新聞の連載",index)
        sitemap=(OUT / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn("https://veripsa.com/series/",sitemap)
        for serial in self.serials:
            base=series_url(serial).lstrip("/")
            self.assertTrue((OUT / base / "index.html").is_file())
            self.assertIn(SITE+series_url(serial),sitemap)
            self.assertTrue((OUT / "feeds" / ("series-"+serial["slug"]+".xml")).is_file())
            image=OUT / "og" / "series" / (serial["slug"]+".png")
            self.assertTrue(image.is_file())
            data=image.read_bytes()
            self.assertTrue(data.startswith(bytes((137,80,78,71,13,10,26,10))))
            self.assertEqual(int.from_bytes(data[16:20],"big"),1200)
            self.assertEqual(int.from_bytes(data[20:24],"big"),630)
            for episode in serial["episodes"]:
                path=serial_episode_url(serial,episode).lstrip("/")
                self.assertTrue((OUT / path / "index.html").is_file())
                self.assertIn(SITE+serial_episode_url(serial,episode),sitemap)
                markup=(OUT / path / "index.html").read_text(encoding="utf-8")
                self.assertIn('href="/series/'+serial["slug"]+'/"',markup)
                self.assertIn('content="'+SITE+'/og/series/'+serial["slug"]+'.png"',markup)


if __name__=="__main__":
    unittest.main()
