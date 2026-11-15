"""Small read-only PV and frozen seven-person contents contracts."""
import copy
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from scripts import photo_editions as p

DAY = "2026-11-15"
NOW = datetime(2026, 11, 15, 4, tzinfo=timezone.utc)
SEED = "0123456789abcdef0123456789abcdef"
IDS = ["model-" + letter for letter in "abcdefg"]


def catalog():
    return {"models": [{"id": model_id} for model_id in IDS], "issues": [
        {"slug": "old-a", "model_id": IDS[0], "state": "published", "published_at": "2026-11-14T12:00:00+09:00"},
        {"slug": "older-a", "model_id": IDS[0], "state": "published", "published_at": "2026-10-01T12:00:00+09:00"},
        {"slug": "old-b", "model_id": IDS[1], "state": "published", "published_at": "2026-11-13T12:00:00+09:00"},
    ]}


def payload(rows=()):
    return {"pageViewTracking": {"unit": "page-view", "timezone": "Asia/Tokyo"},
            "topPhotoIssues": {"version": 1,
                "window": {"startDateJst": "2026-11-08", "endDateJst": "2026-11-14",
                           "timezone": "Asia/Tokyo", "unit": "page-view", "measuredDays": 7, "complete": True},
                "issues": [{"article": key, "pageViews": value} for key, value in rows], "truncated": False}}


def freeze(data=None, observed=None, **kwargs):
    return p.freeze_order(catalog() if data is None else data, publication_day=DAY,
                          api_payload=payload() if observed is None else observed, seed=SEED, now=NOW, **kwargs)


class PhotoEditionOrderTests(unittest.TestCase):
    def test_window_is_seven_finished_jst_dates_before_the_actual_publication_day(self):
        self.assertEqual(p.closed_window(DAY), {"start_date_jst": "2026-11-08", "end_date_jst": "2026-11-14",
                                                "timezone": "Asia/Tokyo", "unit": "page-view"})
        self.assertEqual(p.closed_window("2027-01-01")["start_date_jst"], "2026-12-25")
        for invalid in ("2026-02-30", "2026-1-01", None, "2026-11-15T00:00:00Z"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                p.closed_window(invalid)

    def test_model_totals_sum_all_earlier_features_and_use_only_observed_page_views(self):
        data = catalog()
        before = copy.deepcopy(data)
        result = freeze(data, payload([("photo-issue:old-a", 3), ("photo-issue:older-a", 2),
                                      ("photo-issue:old-b", 7), ("photo-issue:unlisted", 999)]))
        rows = {row["model_id"]: row for row in result["models"]}
        self.assertEqual(result["status"], "measured")
        self.assertEqual(rows[IDS[0]]["page_views"], 5)
        self.assertEqual(rows[IDS[1]]["page_views"], 7)
        self.assertEqual(result["order"][:2], [IDS[1], IDS[0]])
        self.assertEqual(rows[IDS[0]]["feature_keys"], ["photo-issue:old-a", "photo-issue:older-a"])
        self.assertEqual(data, before)
        self.assertEqual(result["source"]["issue_page_views"],
                         {"photo-issue:old-a": 3, "photo-issue:older-a": 2, "photo-issue:old-b": 7})

    def test_observed_zero_and_catalog_debut_are_explicitly_different(self):
        result = freeze()
        rows = {row["model_id"]: row for row in result["models"]}
        self.assertEqual((rows[IDS[0]]["status"], rows[IDS[0]]["page_views"]), ("measured", 0))
        self.assertEqual((rows[IDS[2]]["status"], rows[IDS[2]]["page_views"], rows[IDS[2]]["feature_keys"]),
                         ("debut", 0, []))

    def test_today_published_features_do_not_erase_the_first_appearance_basis(self):
        data = {"models": catalog()["models"], "issues": [
            {"slug": "new-" + str(i), "model_id": model_id, "state": "published",
             "published_at": "2026-11-15T11:23:10+09:00"} for i, model_id in enumerate(IDS)]}
        with patch.object(p, "_get_json", side_effect=AssertionError("debut needs no invented API observations")):
            result = freeze(data)
        self.assertEqual(result["status"], "debut")
        self.assertTrue(all(row["status"] == "debut" and row["page_views"] == 0 for row in result["models"]))

    def test_only_tied_groups_shuffle_once_with_a_persistable_string_seed(self):
        observed = payload([("photo-issue:old-a", 8), ("photo-issue:old-b", 3)])
        result = freeze(observed=observed)
        again = freeze(observed=observed)
        self.assertEqual(result, again)
        self.assertEqual(result["order"][:2], IDS[:2])
        self.assertEqual(set(result["order"][2:]), set(IDS[2:]))
        self.assertIsInstance(result["seed"], str)
        changed = p.freeze_order(catalog(), publication_day=DAY, api_payload=observed,
                                 seed="abcdef0123456789abcdef0123456789", now=NOW)
        self.assertEqual(changed["order"][:2], IDS[:2])
        self.assertNotEqual(changed["order"][2:], result["order"][2:])

    def test_old_rolling_wrong_window_partial_or_truncated_responses_use_honest_fallback(self):
        bad = [{"pageViewTracking": {"unit": "page-view", "timezone": "Asia/Tokyo"},
                "topPhotoIssues": {"last7Days": [{"article": "photo-issue:old-a", "pageViews": 900}]}}]
        for field, value in (("complete", False), ("endDateJst", "2026-11-15"), ("measuredDays", 6)):
            changed = payload()
            changed["topPhotoIssues"]["window"][field] = value
            bad.append(changed)
        for field, value in (("truncated", True), ("version", 0)):
            changed = payload()
            changed["topPhotoIssues"][field] = value
            bad.append(changed)
        for observed in bad:
            with self.subTest(observed=observed):
                result = freeze(observed=observed, previous_order=list(reversed(IDS)))
                self.assertEqual(result["status"], "fallback")
                self.assertEqual(result["order"], list(reversed(IDS)))
                self.assertIsNone(result["models"][0]["page_views"])
                self.assertEqual(result["models"][2]["page_views"], 0)
                self.assertTrue(result["reason"])

    def test_network_failure_keeps_previous_order_and_appends_only_new_members_once(self):
        result = p.freeze_order(catalog(), publication_day=DAY, seed=SEED,
                                previous_order=IDS[:3], fetcher=lambda _url: (_ for _ in ()).throw(OSError("offline")), now=NOW)
        self.assertEqual(result["status"], "fallback")
        self.assertEqual(result["order"][:3], IDS[:3])
        self.assertEqual(set(result["order"][3:]), set(IDS[3:]))
        self.assertEqual(result["reason"], "offline")
        initial = p.freeze_order(catalog(), publication_day=DAY, seed=SEED,
                                 fetcher=lambda _url: (_ for _ in ()).throw(OSError("offline")), now=NOW)
        self.assertEqual(set(initial["order"]), set(IDS))
        self.assertTrue(all(row["page_views"] is None for row in initial["models"][:2]))

    def test_daily_bootstrap_makes_seven_bounded_gets_with_all_existing_keys_and_exact_echo(self):
        calls = []
        def get(url):
            query = parse_qs(urlsplit(url).query)
            calls.append(query)
            day = query["day"][0]
            return {"pageViewTracking": {"unit": "page-view", "timezone": "Asia/Tokyo"},
                    "day": {"dateJst": day, "pageViewsCoverage": "complete"},
                    "articles": {key: {"requestedDayPageViews": 2} for key in query["articles"][0].split(",")}}
        result = p.freeze_order(catalog(), publication_day=DAY, seed=SEED, fetcher=get, daily_bootstrap=True, now=NOW)
        self.assertEqual(len(calls), 7)
        self.assertTrue(all(set(call) == {"day", "articles"} for call in calls))
        self.assertTrue(all(len(call["articles"][0].split(",")) == 3 for call in calls))
        self.assertEqual(result["source"]["kind"], "daily-public-api")
        self.assertEqual(len(result["source"]["daily"]), 7)
        self.assertEqual(result["models"][0]["page_views"], 28)
        self.assertEqual(result["models"][1]["page_views"], 14)
        changed = copy.deepcopy(result)
        changed["source"]["daily"][0]["issue_page_views"]["photo-issue:old-a"] = 90
        with self.assertRaisesRegex(ValueError, "daily observations"):
            p.validate_order_snapshot(changed, IDS)

    def test_daily_wrong_day_unmeasured_missing_or_boolean_count_never_becomes_observed_zero(self):
        for fault in ("day", "coverage", "missing", "bool"):
            def get(url):
                query = parse_qs(urlsplit(url).query)
                day = query["day"][0]
                result = {"pageViewTracking": {"unit": "page-view", "timezone": "Asia/Tokyo"},
                          "day": {"dateJst": day, "pageViewsCoverage": "complete"},
                          "articles": {key: {"requestedDayPageViews": 0} for key in query["articles"][0].split(",")}}
                if fault == "day": result["day"]["dateJst"] = "2026-11-15"
                if fault == "coverage": result["day"]["pageViewsCoverage"] = "partial"
                if fault == "missing": result["articles"].pop("photo-issue:old-a")
                if fault == "bool": result["articles"]["photo-issue:old-a"]["requestedDayPageViews"] = True
                return result
            with self.subTest(fault=fault):
                result = p.freeze_order(catalog(), publication_day=DAY, seed=SEED, fetcher=get, daily_bootstrap=True, now=NOW)
                self.assertEqual(result["status"], "fallback")
                self.assertIsNone(result["models"][0]["page_views"])

    def test_existing_output_is_reused_without_fetching_or_reshuffling(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data_path, output = root / "catalog.json", root / "ranking.json"
            data_path.write_text(json.dumps(catalog()))
            original = freeze()
            output.write_text(json.dumps(original))
            before = output.read_bytes()
            with patch.object(p, "_get_json", side_effect=AssertionError("no reread")), redirect_stdout(io.StringIO()):
                self.assertEqual(p.main(["--catalog", str(data_path), "--publication-date", DAY,
                                         "--output", str(output), "--seed", "f" * 32]), 0)
            self.assertEqual(output.read_bytes(), before)


class PhotoEditionCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "content").mkdir()
        self.catalog = catalog()
        self.new = [{"slug": "feature-" + str(i), "model_id": model_id, "state": "published",
                     "published_at": "2026-11-15T11:23:10+09:00"} for i, model_id in enumerate(IDS)]
        self.catalog["issues"].extend(self.new)
        ranking = freeze(self.catalog)
        by_model = {issue["model_id"]: issue["slug"] for issue in self.new}
        self.edition = {"number": 1, "slug": "issue-001", "state": "published", "legacy": False,
                        "title": "週刊グラビア 創刊号", "summary": "七人の架空の成人モデルの写真とコラム。",
                        "published_at": "2026-11-15T12:00:00+09:00",
                        "features": [by_model[model_id] for model_id in ranking["order"]], "ranking": ranking}

    def load(self, edition=None, published=None):
        (self.root / "content/photo_editions.json").write_text(json.dumps({
            "schema_version": 1, "editions": [self.edition if edition is None else edition]}))
        return p.load_photo_editions(self.root, catalog=self.catalog,
                                     published=self.catalog["issues"] if published is None else published, now=NOW)

    def test_joint_issue_one_loads_seven_fixed_features_in_the_frozen_order(self):
        result = self.load()[0]
        self.assertEqual([issue["model_id"] for issue in result["ordered_features"]], self.edition["ranking"]["order"])
        self.assertEqual(p.edition_url(result), "/photo/weekly/issue-001/")
        self.assertEqual(len(result["ordered_features"]), 7)

    def test_drafts_missing_features_duplicate_models_bad_order_and_wrong_date_cannot_publish(self):
        for change in (lambda e: e.update(features=e["features"][:-1]),
                       lambda e: e["features"].reverse(), lambda e: e.update(slug="issue-002"),
                       lambda e: e.update(published_at="2026-11-14T12:00:00+09:00")):
            edition = copy.deepcopy(self.edition)
            change(edition)
            with self.subTest(edition=edition), self.assertRaises(ValueError):
                self.load(edition)
        with self.assertRaisesRegex(ValueError, "unpublished"):
            self.load(published=self.new[:-1])
        self.new[0]["state"] = "draft"
        with self.assertRaisesRegex(ValueError, "unpublished"):
            self.load()

    def test_old_saya_style_legacy_url_is_preserved_without_rewriting_the_feature(self):
        feature = self.catalog["issues"][0]
        legacy = {"number": 1, "slug": feature["slug"], "state": "published", "legacy": True,
                  "title": "旧特集", "summary": "旧特集をそのまま参照。", "published_at": feature["published_at"],
                  "features": [feature["slug"]], "ranking": None}
        before = copy.deepcopy(feature)
        self.assertEqual(p.edition_url(self.load(legacy)[0]), "/photo/weekly/old-a/")
        self.assertEqual(feature, before)

    def test_snapshot_cannot_turn_an_existing_feature_into_a_debut_or_change_count_order(self):
        edition = copy.deepcopy(self.edition)
        row = next(row for row in edition["ranking"]["models"] if row["model_id"] == IDS[0])
        for key in row["feature_keys"]:
            edition["ranking"]["source"]["issue_page_views"].pop(key)
        row.update(feature_keys=[], page_views=0, status="debut")
        with self.assertRaisesRegex(ValueError, "omits or invents"):
            self.load(edition)
        edition = copy.deepcopy(self.edition)
        edition["ranking"]["models"][0]["page_views"] = 100
        with self.assertRaisesRegex(ValueError, "actual feature observations"):
            self.load(edition)
        edition = copy.deepcopy(self.edition)
        next(row for row in edition["ranking"]["models"] if row["status"] == "debut")["page_views"] = False
        with self.assertRaisesRegex(ValueError, "structural zero"):
            self.load(edition)


if __name__ == "__main__":
    unittest.main()
