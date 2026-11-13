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

    def test_branch_only_repair_declares_its_existing_article_target(self):
        class API:
            def pages(self, route):
                return [{"filename": ".github/workflows/temporary-pr708-repair.yml"}]
        body = "## 対象\n既に統合されたPR #708の公開記事 `rice` だけを修正する。\n\n## 検証\n別記事 `other` は触らない。"
        self.assertEqual(g.touched(API(), {"number": 711, "body": body}), {"rice"})

    def test_existing_handoff_slug_fields_are_read_without_incidental_mentions(self):
        for line in ("- 対象slug: `rice`", "- slug：rice", "**slug**: `rice`"):
            self.assertEqual(g.declared_slugs({"body": line + "\n比較記事 `other`"}), {"rice"})
        self.assertEqual(g.declared_slugs({"body": "炊飯器 `rice` は別PRで完了。"}), set())

    def test_handoff_target_change_invalidates_the_ownership_snapshot(self):
        before = {"number": 714, "state": "open", "head": {"sha": "a" * 40},
                  "base": {"sha": "b" * 40, "ref": "main"}, "body": "slug: rice"}
        after = {**before, "body": "slug: kitchen"}
        self.assertNotEqual(g.signature(before), g.signature(after))

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
