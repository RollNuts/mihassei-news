import unittest
from scripts import review_release_guard as g


class AssetOwnershipTests(unittest.TestCase):
    def test_photo_scene_and_diagram_paths_identify_article_without_json_edit(self):
        for path in ("assets/generated/rice.jpg", "assets/editorial-scenes/rice.svg",
                     "assets/editorial-scenes/rice.png", "assets/article-diagrams/rice.svg",
                     "assets/photos/rice.jpg"):
            class API:
                def pages(self, route):
                    return [{"filename": path}]
            self.assertEqual(g.touched(API(), {"number": 714}), {"rice"}, path)

    def test_renamed_cached_photo_reserves_old_and_new_article(self):
        class API:
            def pages(self, route):
                return [{"filename": "assets/photos/new.jpg", "previous_filename": "assets/photos/old.jpg"}]
        self.assertEqual(g.touched(API(), {"number": 714}), {"old", "new"})

    def test_cached_approval_change_identifies_article(self):
        old = {"version": 1, "approved": {"rice": {"source": "old"}}}
        new = {"version": 1, "approved": {"rice": {"source": "new"}}}
        self.assertEqual(g.changed_slugs("content/approved_photos.json", old, new), {"rice"})

    def test_nonarticle_assets_do_not_reserve_an_article(self):
        for path in ("assets/site.js", "assets/casters/rice.jpg", "other/assets/photos/rice.jpg"):
            class API:
                def pages(self, route):
                    return [{"filename": path}]
            self.assertEqual(g.touched(API(), {"number": 714}), set(), path)

    def test_matching_but_incomplete_material_count_objects_fail_closed(self):
        value = {"checkout_sha": "a" * 40,
                 "live_checks": {k: "success" for k in ("revision", "articles", "share_cards")},
                 "photo_followup": [], "attention": [],
                 "photo_followup_counts": {k: 0 for k in ("pending", "missing", "rejected")},
                 "counts": {"unknown": 0}, "publication_counts": {"unknown": 0}}
        with self.assertRaises(ValueError):
            g.photo_check(value, "a" * 40)


if __name__ == "__main__":
    unittest.main()
