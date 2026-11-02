"""Basic checks run with no network and no external packages."""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
class SiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([sys.executable, str(ROOT/"scripts/build.py")],cwd=ROOT,check=True)

    def test_home_and_articles(self):
        a=json.loads((ROOT/"content/articles.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(a),5)
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        self.assertIn("FICTION",home)
        self.assertIn(a[0]["title"],home)
        for item in a:
            page=ROOT/"dist/articles"/item["slug"]/"index.html"
            image=ROOT/"dist/images"/(item["slug"]+".svg")
            self.assertTrue(page.exists())
            self.assertTrue(image.exists())
            text=page.read_text(encoding="utf-8")
            self.assertIn("実際には起きていない",text)
            self.assertIn(item["source"]["url"],text)
            self.assertIn(item["source"]["topic"],text)
            self.assertIn("5年後",text)
    def test_sitemap_rss_valid_xml(self):
        ET.parse(ROOT/"dist/sitemap.xml")
        ET.parse(ROOT/"dist/feed.xml")
    def test_robots_asset_routes(self):
        for name in ("assets/style.css","assets/article-extra.css","assets/site.js","archive/index.html","about/index.html","robots.txt","404.html"):
            self.assertTrue((ROOT/"dist"/name).exists(),name)

if __name__ == "__main__":
    unittest.main()
