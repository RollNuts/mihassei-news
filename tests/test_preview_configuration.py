"""Preview config is explicit and never binds to a production DO namespace."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PreviewConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "wrangler.jsonc").read_text(encoding="utf-8"))

    def test_preview_declares_runtime_bindings_without_production_targets(self):
        preview = self.config["previews"]
        bindings = preview["durable_objects"]["bindings"]
        self.assertEqual(bindings, self.config["durable_objects"]["bindings"])
        self.assertEqual({item["name"] for item in bindings},
                         {"NEWSROOM_STATS", "READER_COMMENTS"})
        for binding in bindings:
            # Same-Worker classes get isolated Preview namespaces automatically.
            self.assertEqual(set(binding), {"name", "class_name"})
            self.assertNotIn("script_name", binding)
            self.assertNotIn("namespace_id", binding)

    def test_assets_and_sqlite_migrations_stay_at_top_level(self):
        preview = self.config["previews"]
        self.assertNotIn("assets", preview)
        self.assertNotIn("migrations", preview)
        self.assertEqual(self.config["assets"]["directory"], "./dist")
        self.assertEqual(self.config["assets"]["binding"], "ASSETS")
        classes = {name for migration in self.config["migrations"]
                   for name in migration.get("new_sqlite_classes", [])}
        self.assertTrue({"NewsroomStats", "ReaderComments"} <= classes)

    def test_preview_does_not_override_public_routes_or_add_paid_services(self):
        self.assertEqual(set(self.config["previews"]), {"durable_objects"})
        self.assertEqual(self.config["name"], "mihassei-news")
        self.assertEqual(self.config["main"], "src/worker.js")


if __name__ == "__main__":
    unittest.main()
