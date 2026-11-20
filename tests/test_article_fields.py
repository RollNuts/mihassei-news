"""Article input errors must stop before the shared site renderer runs."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import build


class ArticleFieldTests(unittest.TestCase):
    def test_missing_fiction_company_is_reported_by_load_before_rendering(self):
        articles = json.loads((build.BASE / "content/articles.json").read_text(encoding="utf-8"))
        slug = articles[0]["slug"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "content/articles.json"
            path.parent.mkdir()
            path.write_text(json.dumps(articles, ensure_ascii=False), encoding="utf-8")
            with patch.object(build, "BASE", root):
                self.assertEqual(len(build.load()), len(articles))
                del articles[0]["fiction_company"]
                path.write_text(json.dumps(articles, ensure_ascii=False), encoding="utf-8")
                with self.assertRaisesRegex(
                    ValueError, r"Missing article field: fiction_company.*" + re.escape(slug)
                ):
                    build.load()


if __name__ == "__main__":
    unittest.main()
