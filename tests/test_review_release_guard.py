import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import review_release_guard as g

A, B, C = "a" * 40, "b" * 40, "c" * 40


def pr(number, head=A, state="open"):
    return {"number": number, "state": state, "draft": True,
            "head": {"sha": head}, "base": {"sha": B, "ref": "main"}}


class API:
    def __init__(self):
        self.prs = {710: pr(710), 711: pr(711, C)}
        self.files = {n: [{"filename": "content/articles.json"}] for n in self.prs}
        self.docs = {B: [{"slug": "rice", "title": "old"}],
                     A: [{"slug": "rice", "title": "owner"}],
                     C: [{"slug": "rice", "title": "duplicate"}]}
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        if path.startswith("/compare/"):
            return {"merge_base_commit": {"sha": B}}
        return copy.deepcopy(self.prs[int(path.split("/")[2])])

    def pages(self, path):
        self.calls.append(path)
        if path.startswith("/pulls?"):
            return [copy.deepcopy(p) for p in self.prs.values() if p["state"] == "open"]
        return self.files[int(path.split("/")[2])]

    def document(self, path, revision):
        self.calls.append((path, revision))
        return self.docs[revision]


def report():
    return {"checkout_sha": A, "live_checks": {k: "success" for k in ("revision", "articles", "share_cards")},
            "photo_followup": [], "photo_followup_counts": {k: 0 for k in ("pending", "missing", "rejected")},
            "attention": [], "publication_counts": {"generated_jpeg": 2, "editorial_svg": 1, "stock_photo": 0, "generic_fallback": 0},
            "counts": {"accepted": 2, "pending": 0, "missing": 1, "rejected": 0}}


class OwnershipTests(unittest.TestCase):
    def test_earlier_draft_owner_blocks_later_pr(self):
        result = g.owner_check(API(), 711, C)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["existing_owners"][0]["pr"], 710)
        self.assertEqual(result["slugs"], ["rice"])

    def test_original_owner_is_not_blocked_by_later_duplicate(self):
        self.assertEqual(g.owner_check(API(), 710, A)["status"], "clear")

    def test_closed_owner_is_not_an_open_conflict(self):
        api = API(); api.prs[710]["state"] = "closed"
        self.assertEqual(g.owner_check(api, 711, C)["status"], "clear")

    def test_different_article_in_same_json_does_not_conflict(self):
        api = API(); api.docs[C] = api.docs[B] + [{"slug": "kitchen", "title": "new"}]
        self.assertEqual(g.owner_check(api, 711, C)["status"], "clear")

    def test_unrelated_code_pr_does_not_read_all_articles(self):
        api = API(); api.files[711] = [{"filename": "scripts/test.py"}]
        self.assertEqual(g.owner_check(api, 711, C)["status"], "clear")
        self.assertFalse(any(isinstance(x, tuple) for x in api.calls))

    def test_image_only_pr_is_same_article_work(self):
        api = API(); api.files[711] = [{"filename": "assets/generated/rice.jpg"}]
        self.assertEqual(g.owner_check(api, 711, C)["status"], "blocked")

    def test_renamed_image_keeps_old_owner(self):
        api = API(); api.files[711] = [{"filename": "assets/generated/new.jpg", "previous_filename": "assets/generated/rice.jpg"}]
        self.assertEqual(g.owner_check(api, 711, C)["status"], "blocked")

    def test_stale_head_fails_before_any_content_read(self):
        api = API()
        with self.assertRaisesRegex(ValueError, "head changed"):
            g.owner_check(api, 711, A)
        self.assertEqual(len(api.calls), 1)

    def test_own_head_change_during_check_is_not_clear(self):
        api = API(); original = api.get; count = 0
        def read(path):
            nonlocal count
            if path == "/pulls/711":
                count += 1
                if count == 2:
                    api.prs[711]["head"]["sha"] = A
            return original(path)
        api.get = read
        with self.assertRaisesRegex(ValueError, "changed during"):
            g.owner_check(api, 711, C)

    def test_earlier_inventory_change_is_not_clear(self):
        api = API(); original = api.pages; count = 0
        def read(path):
            nonlocal count
            if path.startswith("/pulls?"):
                count += 1
                if count == 2:
                    api.prs[710]["head"]["sha"] = C
            return original(path)
        api.pages = read
        with self.assertRaisesRegex(ValueError, "inventory changed"):
            g.owner_check(api, 711, C)

    def test_failed_listing_is_not_no_owner(self):
        api = API(); api.pages = lambda path: (_ for _ in ()).throw(OSError("network unavailable"))
        with self.assertRaises(OSError):
            g.owner_check(api, 711, C)

    def test_article_order_is_not_a_change_but_scene_is(self):
        rows = [{"slug": "rice", "scene_prompt": "one"}, {"slug": "kitchen"}]
        self.assertEqual(g.changed_slugs("content/articles.json", rows, rows[::-1]), set())
        changed = copy.deepcopy(rows); changed[0]["scene_prompt"] = "two"
        self.assertEqual(g.changed_slugs("content/articles.json", rows, changed), {"rice"})

    def test_media_decisions_and_large_integer_seed_are_preserved(self):
        old = {"images": {"rice": {"seed": 5318895560765377914}}, "rejected": {}}
        new = copy.deepcopy(old); new["rejected"]["rice"] = {"decision": "rejected"}
        self.assertEqual(g.changed_slugs("assets/generated/manifest.json", old, new), {"rice"})
        self.assertEqual(old["images"]["rice"]["seed"], 5318895560765377914)

    def test_invalid_and_duplicate_articles_are_not_empty(self):
        for value in ({}, [{"slug": "rice"}, {"slug": "rice"}]):
            with self.assertRaises(ValueError):
                g.records("content/articles.json", value)

    def test_pagination_requires_the_terminal_page(self):
        api = g.GitHub(); api.get = lambda path: [{}] * 100
        with self.assertRaisesRegex(ValueError, "Listing limit"):
            api.pages("/pulls?state=open")


class PhotoTests(unittest.TestCase):
    def test_intentional_svg_is_not_an_unfinished_photo(self):
        self.assertEqual(g.photo_check(report(), A)["status"], "backlog_empty")

    def test_green_delivery_does_not_hide_missing_pending_or_rejected_photo(self):
        for state in ("pending", "missing", "rejected"):
            value = report(); value["photo_followup"] = [{"slug": "rice", "status": state, "next_action": "recover saved candidate"}]
            value["photo_followup_counts"][state] = 1
            self.assertEqual(g.photo_check(value, A)["status"], "recovery_required")

    def test_generic_fallback_cannot_be_reported_complete(self):
        value = report(); value["publication_counts"]["generated_jpeg"] = 1; value["publication_counts"]["generic_fallback"] = 1
        self.assertEqual(g.photo_check(value, A)["status"], "recovery_required")

    def test_unverified_delivery_never_passes(self):
        for state in ("failure", "skipped", "not_run", "cancelled"):
            value = report(); value["live_checks"]["revision"] = state
            with self.assertRaises(ValueError):
                g.photo_check(value, A)

    def test_old_revision_never_passes(self):
        with self.assertRaises(ValueError):
            g.photo_check(report(), B)

    def test_missing_counts_inventory_or_unknown_status_never_passes(self):
        for key in ("photo_followup", "photo_followup_counts", "attention", "publication_counts", "counts"):
            value = report(); value.pop(key)
            with self.assertRaises(ValueError):
                g.photo_check(value, A)
        value = report(); value["photo_followup"] = [{"slug": "rice", "status": "unknown"}]
        with self.assertRaises(ValueError):
            g.photo_check(value, A)

    def test_false_zero_and_boolean_counts_never_pass(self):
        value = report(); value["photo_followup_counts"]["pending"] = 1
        with self.assertRaises(ValueError):
            g.photo_check(value, A)
        value = report(); value["photo_followup_counts"]["pending"] = False
        with self.assertRaises(ValueError):
            g.photo_check(value, A)

    def test_diagram_defect_not_hidden_by_accepted_photo(self):
        value = report(); value["attention"] = [{"slug": "rice", "reason": "stale_diagram", "next_action": "review SVG"}]
        self.assertEqual(g.photo_check(value, A)["status"], "recovery_required")

    def test_cli_missing_report_exits_unverified(self):
        with tempfile.TemporaryDirectory() as folder, patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(g.main(["photos", "--report", str(Path(folder) / "absent.json"), "--expected-sha", A]), 2)
            self.assertEqual(json.loads(out.getvalue())["status"], "unverified")

    def test_check_payload_is_separate_from_delivery_and_marks_recovery_failed(self):
        value = report(); value["photo_followup"] = [{"slug": "rice", "status": "pending", "next_action": "review PNG/JPEG"}]
        value["photo_followup_counts"]["pending"] = 1
        payload = g.photo_payload(value, A, "https://github.com/RollNuts/mihassei-news/actions/runs/123")
        self.assertEqual(payload["conclusion"], "failure")
        self.assertIn("rice", payload["output"]["summary"])
        self.assertEqual(payload["head_sha"], A)

    def test_check_payload_rejects_foreign_details_url(self):
        with self.assertRaises(ValueError):
            g.photo_payload(report(), A, "https://example.org/123")


if __name__ == "__main__":
    unittest.main()
