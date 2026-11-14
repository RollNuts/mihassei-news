"""Small offline photo-issue gates; synthetic reviews never leave temporary repos."""
import copy
import hashlib
import io
import json
import tempfile
import unittest
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET
from datetime import datetime, timezone
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import photo_issues as p
from scripts import build

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)
INITIAL_FP = "730ff0ada307460fb161775bdb7cf0bb8992258b334760e27ad33f939b9e2960"


def digest(data):
    return hashlib.sha256(data).hexdigest()


class PhotoIssueTests(unittest.TestCase):
    def setUp(self):
        self.catalog = copy.deepcopy(p.load_photo_issues(ROOT))
        self.issue = self.catalog["issues"][0]
        if self.issue["issue_sha256"] != INITIAL_FP:
            original = next(entry for entry in self.issue["editorial_history"]
                            if entry["issue_sha256"] == INITIAL_FP)
            self.issue.update(copy.deepcopy(original["editorial"]), issue_sha256=INITIAL_FP)
        self.issue.pop("editorial_history", None)
        # Ignore production delivery additions: test only the fixed editorial draft.
        self.issue.update(images={}, state="draft", published_at=None)
        self.issue.pop("generation_holds", None)
        self.issue.pop("master_archive", None)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        master = self.issue["model_snapshot"]["master"]
        self.jpeg = (ROOT / master["path"]).read_bytes()
        target = self.root / master["path"]
        target.parent.mkdir(parents=True)
        target.write_bytes(self.jpeg)

    def release_fixture(self):
        self.issue.update(state="published", published_at="2026-11-14T14:30:00+09:00")
        for shot in self.issue["shots"]:
            cut = shot["id"]
            # A harmless JPEG comment makes each synthetic fixture byte-unique.
            data = self.jpeg[:-2] + b"\xff\xfe\x00\x04" + cut.encode("ascii") + self.jpeg[-2:]
            target = self.root / p.image_path(self.issue, cut)
            target.write_bytes(data)
            logical = "photo-issue-" + self.issue["issue_sha256"] + "-cut-" + cut
            source = self.issue["source_article"]
            receipt = {"version": 1, "kind": "native_image_candidate", "status": "awaiting_visual_review",
                       "repository": "RollNuts/mihassei-news", "slug": source["slug"],
                       "article_sha256": source["article_sha256"], "logical_run_id": logical,
                       "source": {"sha256": digest(("synthetic-png-" + cut).encode()), "bytes": 100,
                                  "parts": [{"path": "maintenance/native-image-candidates/" + source["slug"] + "/source.png.part000",
                                             "sha256": "d" * 64, "blob_sha": "d" * 40, "bytes": 100}]},
                       "jpeg": {"sha256": digest(data), "bytes": len(data), "width": 1200, "height": 675,
                                "quality": 84, "path": "maintenance/native-image-candidates/" + source["slug"] + "/candidate.jpg"},
                       "baseline": {"commit_sha": "c" * 40, "tree_sha": "b" * 40,
                                    "protected_hashes": {key: "a" * 64 for key in
                                     ("content/articles.json", "assets/generated/manifest.json", "content/visual_reviews.json")}},
                       "generation": {"capture_kind": "native_tool_result", "prompt": "Synthetic cut " + cut,
                                      "prompt_sha256": digest(("Synthetic cut " + cut).encode()),
                                      "observed_at": "2026-11-14T13:00:00+09:00"}}
            receipt["identity"] = digest(json.dumps(["RollNuts/mihassei-news", source["slug"], source["article_sha256"],
                                                     logical, receipt["generation"]["prompt"]],
                                                    ensure_ascii=False, separators=(",", ":")).encode())
            receipt_data = json.dumps(receipt).encode()
            receipt_file = self.root / p.receipt_path(self.issue, cut)
            receipt_file.parent.mkdir(parents=True, exist_ok=True)
            receipt_file.write_bytes(receipt_data)
            self.issue["images"][cut] = {
                "path": p.image_path(self.issue, cut), "sha256": digest(data), "bytes": len(data),
                "source_png_sha256": receipt["source"]["sha256"],
                "provenance": {"candidate_branch": "native-image-candidate/" + source["slug"] + "/"
                               + source["article_sha256"][:16] + "-" + digest(logical.encode())[:12],
                               "candidate_commit": "a" * 40, "logical_run_id": logical,
                               "receipt_path": p.receipt_path(self.issue, cut), "receipt_sha256": digest(receipt_data)},
                "review": {"decision": "accepted", "issue_sha256": self.issue["issue_sha256"],
                           "image_sha256": digest(data), "master_sha256": self.issue["model_snapshot"]["master"]["sha256"],
                           "cut_id": cut, "reviewed_at": "2026-11-14T14:00:00+09:00",
                           "note": "Synthetic offline review only; no real visual acceptance.",
                           "swimwear_visible": cut in {"02", "04", "05", "06", "07", "08"},
                           "framing": "upper_body" if cut == "02" else "full_body"}}
        return self.issue

    def adoption_fixture(self):
        self.release_fixture()
        old_fp = self.issue["issue_sha256"]
        old_images = copy.deepcopy(self.issue["images"])
        old_receipts = {cut: (self.root / p.receipt_path(self.issue, cut)).read_bytes()
                        for cut in ("01", "02")}
        history = {"issue_sha256": old_fp,
                   "editorial": {key: copy.deepcopy(self.issue[key]) for key in p.EDITORIAL_FIELDS},
                   "images": {"01": copy.deepcopy(old_images["01"])},
                   "generation_holds": {"03": {"claim": "original-rejected-claim"}},
                   "reason": "Different swimsuit and sports setting, with original failures retained."}
        self.issue["shots"][1]["description"] = "紺のワンピース水着でタオルを持つ。"
        self.issue["shots"][3]["description"] = "紺のワンピース水着と白いシャツで浅瀬へ。"
        self.issue["issue_sha256"] = p.issue_fingerprint(self.issue)
        self.issue["editorial_history"] = [history]
        self.release_fixture()
        for cut in ("01", "02"):
            image = old_images[cut]
            original_review = copy.deepcopy(image["review"])
            receipt_data = old_receipts[cut]
            if cut == "02":
                # A distinct wardrobe study has its own original native identity,
                # contains the old FP, and deliberately has no cut-ID token.
                receipt = json.loads(receipt_data)
                logical = old_fp + "-onepiece-wardrobe-study"
                receipt["logical_run_id"] = logical
                source = receipt["article_sha256"]
                image["provenance"]["logical_run_id"] = logical
                image["provenance"]["candidate_branch"] = ("native-image-candidate/" + receipt["slug"]
                    + "/" + source[:16] + "-" + digest(logical.encode())[:12])
                receipt["identity"] = digest(json.dumps([receipt["repository"], receipt["slug"], source,
                    logical, receipt["generation"]["prompt"]], ensure_ascii=False,
                    separators=(",", ":")).encode())
                receipt_data = json.dumps(receipt).encode()
                image["provenance"]["receipt_sha256"] = digest(receipt_data)
                history["studies"] = {logical: {key: copy.deepcopy(image[key]) for key in
                    ("sha256", "bytes", "source_png_sha256", "provenance")}}
            (self.root / p.receipt_path(self.issue, cut)).write_bytes(receipt_data)
            image["adoption"] = {"source_issue_sha256": old_fp,
                "target_issue_sha256": self.issue["issue_sha256"], "target_cut_id": cut,
                "reason": "Actual photograph checked against the revised shot and column."}
            image["review_history"] = [original_review] if cut == "01" else []
            image["review"] = dict(original_review, issue_sha256=self.issue["issue_sha256"],
                                   reviewed_at="2026-11-14T14:10:00+09:00")
            self.issue["images"][cut] = image
        return old_fp

    def article_adoption_fixture(self):
        self.adoption_fixture()
        cut = "04"
        image = self.issue["images"][cut]
        source = self.issue["source_article"]
        master = self.issue["model_snapshot"]["master"]
        article = self.issue.get("article_photo_sources", {}).get(source["slug"], {}).get("article")
        if article is None:
            article = next(article for article in json.loads((ROOT / "content/articles.json").read_text())
                           if article["slug"] == source["slug"])
        article = {key: article[key] for key in p.ARTICLE_FIELDS}
        self.assertEqual(p.article_fingerprint(article), source["article_sha256"])
        receipt_file = self.root / p.receipt_path(self.issue, cut)
        receipt = json.loads(receipt_file.read_bytes())
        logical = "accepted-article-photo-original-logical-without-issue-FP"
        receipt["logical_run_id"] = logical
        receipt["jpeg"].update(sha256=digest(self.jpeg), bytes=len(self.jpeg))
        receipt["source"]["sha256"] = master["source_png_sha256"]
        receipt["identity"] = digest(json.dumps([receipt["repository"], source["slug"], source["article_sha256"],
            logical, receipt["generation"]["prompt"]], ensure_ascii=False, separators=(",", ":")).encode())
        receipt_data = json.dumps(receipt).encode()
        receipt_file.write_bytes(receipt_data)
        provenance = {"candidate_branch": "native-image-candidate/" + source["slug"] + "/"
                      + source["article_sha256"][:16] + "-" + digest(logical.encode())[:12],
                      "candidate_commit": "a" * 40, "logical_run_id": logical,
                      "receipt_path": p.receipt_path(self.issue, cut), "receipt_sha256": digest(receipt_data)}
        manifest = {"article_sha256": source["article_sha256"], "origin": "chatgpt-imagegen",
            "model": "ChatGPT ImageGen", "generation_prompt": receipt["generation"]["prompt"],
            "sha256": digest(self.jpeg), "bytes": len(self.jpeg), "width": 1200, "height": 675,
            "source_png_sha256": master["source_png_sha256"], "source_png_bytes": receipt["source"]["bytes"],
            "native_candidate": {"branch": provenance["candidate_branch"], "commit": provenance["candidate_commit"],
                "logical_run_id": logical, "identity": receipt["identity"],
                "receipt_path": "maintenance/native-image-candidates/" + source["slug"] + "/receipt.json",
                "receipt_sha256": provenance["receipt_sha256"]}}
        accepted = {"decision": "accepted", "article_sha256": source["article_sha256"],
                    "image_sha256": digest(self.jpeg), "generation_record_sha256": p.generation_record_fingerprint(manifest),
                    "reviewed_at": "2026-11-14T14:00:00+09:00", "note": "Synthetic source acceptance for offline tests."}
        snapshot = {"article": article, "manifest": manifest, "accepted_review": accepted}
        snapshot["snapshot_sha256"] = p.generation_record_fingerprint(snapshot)
        self.issue["article_photo_sources"] = {source["slug"]: snapshot}
        image.update(path=master["path"], sha256=manifest["sha256"], bytes=manifest["bytes"],
                     source_png_sha256=manifest["source_png_sha256"], provenance=provenance,
                     adoption={"kind": "accepted_article_photo", "source_article_slug": source["slug"],
                        "source_article_sha256": source["article_sha256"], "target_issue_sha256": self.issue["issue_sha256"],
                        "target_cut_id": cut, "reason": "Existing accepted source photograph fits this issue scene."})
        image["review"].update(image_sha256=manifest["sha256"], reviewed_at="2026-11-14T14:10:00+09:00")
        return snapshot

    def test_initial_catalog_has_seven_adult_models_and_fixed_editorial_fingerprint(self):
        self.assertEqual(len(self.catalog["models"]), 7)
        self.assertEqual([(m["name"], m["age"]) for m in self.catalog["models"]], [
            ("凪野さや", 27), ("汐羽りお", 29), ("月城れな", 23), ("花瀬みお", 20),
            ("白峰ゆき", 24), ("天音ひかり", 21), ("桐谷すみれ", 26)])
        self.assertEqual(p.issue_fingerprint(self.issue), "730ff0ada307460fb161775bdb7cf0bb8992258b334760e27ad33f939b9e2960")
        self.assertIn("画面の友達が足で水を払うと", self.issue["column"][1])
        p.validate_catalog(self.catalog)

    def test_release_and_image_state_do_not_change_issue_fingerprint(self):
        baseline = p.issue_fingerprint(self.issue)
        self.release_fixture()
        self.issue.update(modified_at="2026-11-15T13:30:00+09:00", generation_failures={"03": {"status": "blocked"}})
        self.assertEqual(p.issue_fingerprint(self.issue), baseline)
        self.issue["column"][0] += "意味の変わる加筆。"
        self.assertNotEqual(p.issue_fingerprint(self.issue), baseline)
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            p.validate_catalog(self.catalog)

    def test_model_or_cut_changes_require_a_new_editorial_fingerprint(self):
        baseline = p.issue_fingerprint(self.issue)
        for field in ("model_snapshot", "shots"):
            changed = copy.deepcopy(self.issue)
            if field == "model_snapshot": changed[field]["appearance"] += "髪型を変更。"
            else: changed[field][0]["description"] += "別の場所。"
            self.assertNotEqual(p.issue_fingerprint(changed), baseline)

    def test_schema_rejects_minor_unknown_model_and_cut_gaps(self):
        for kind in ("minor", "unknown", "gap"):
            bad = copy.deepcopy(self.catalog)
            if kind == "minor": bad["models"][0]["age"] = 19
            elif kind == "unknown": bad["issues"][0]["model_id"] = "unknown"
            else: bad["issues"][0]["shots"].pop()
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                p.validate_catalog(bad)

    def test_model_character_minimum_fields_are_required(self):
        for missing in ("first_person", "personality", "voice", "likes", "weakness"):
            bad = copy.deepcopy(self.catalog)
            bad["models"][0]["character"].pop(missing)
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                p.validate_catalog(bad)
        bad = copy.deepcopy(self.catalog)
        bad["models"][0]["character"]["likes"] = []
        with self.assertRaises(ValueError):
            p.validate_catalog(bad)
        changed = copy.deepcopy(self.catalog)
        changed["models"][0]["character"]["weakness"] += "後日追加の人物設定。"
        self.assertEqual(p.issue_fingerprint(changed["issues"][0]), self.issue["issue_sha256"])

    def test_internal_real_person_references_cannot_become_reader_copy(self):
        for target in ("model", "column", "caption", "location"):
            bad = copy.deepcopy(self.catalog)
            if target == "model": bad["models"][0]["name"] = "藤嶌果歩"
            elif target == "column": bad["issues"][0]["column"][0] += "齋藤飛鳥"
            elif target == "caption": bad["issues"][0]["shots"][0]["caption"] += "藤嶌果歩"
            else: bad["issues"][0]["setting"]["location"] += "齋藤飛鳥"
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "reference name"):
                p.validate_catalog(bad)

    def test_draft_produces_no_pages_assets_routes_or_navigation(self):
        self.release_fixture()
        self.issue["state"] = "draft"
        published = p.published_issues(self.catalog, root=self.root, now=NOW)
        self.assertEqual(published, [])
        written = []
        p.build_photo_issues(published, self.catalog["models"], build.layout,
                             lambda *args: written.append(args), self.root / "out", root=self.root)
        self.assertEqual(written, [])
        self.assertEqual(p.photo_sitemap_entries(published), [])
        self.assertFalse((self.root / "out").exists())
        with patch.object(build, "PHOTO_ISSUES_AVAILABLE", False):
            self.assertNotIn("/photo/weekly/", build.layout("通常ページ", "説明", "/example/", "本文"))

    def test_all_ten_accepted_jpegs_publish_without_source_parts_or_receipts(self):
        self.release_fixture()
        published = p.published_issues(self.catalog, root=self.root, now=NOW)
        written = {}
        output = self.root / "out"
        p.build_photo_issues(published, self.catalog["models"], build.layout, written.__setitem__, output, root=self.root)
        self.assertEqual(len(written), 3)
        self.assertIn(p.feed_url().lstrip("/"), written)
        self.assertEqual(len(list(output.rglob("*.jpg"))), 10)
        self.assertFalse(list(output.rglob("*.json")))
        self.assertFalse(list(output.rglob("*.png")))
        self.assertEqual(len(p.photo_sitemap_entries(published)), 2)
        markup = written[p.issue_url(self.issue).lstrip("/") + "index.html"]
        self.assertIn('property="og:image" content="https://veripsa.com' + p.cover_url(self.issue), markup)
        self.assertIn('"datePublished": "2026-11-14T14:30:00+09:00"', markup)
        self.assertIn("創作写真コラム", markup)
        self.assertIn('property="og:type" content="article"', markup)
        self.assertNotIn("native-image-candidate", markup)
        self.assertNotIn(self.issue["issue_sha256"], markup)
        self.assertIn('href="' + p.feed_url() + '"', markup)

    def test_weekly_feed_has_released_cover_enclosure_real_date_and_canonical_link(self):
        self.release_fixture()
        self.issue["cover_cut_id"] = "04"
        published = p.published_issues(self.catalog, root=self.root, now=NOW)
        draft = {"state": "draft", "title": "Must not be published"}
        feed = ET.fromstring(p.generate_photo_rss([draft, *published]))
        channel = feed.find("channel")
        self.assertEqual(channel.findtext("link"), "https://veripsa.com" + p.index_url())
        entries = channel.findall("item")
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        canonical = "https://veripsa.com" + p.issue_url(self.issue)
        self.assertEqual(entry.findtext("link"), canonical)
        self.assertEqual(entry.findtext("guid"), canonical)
        self.assertEqual(parsedate_to_datetime(entry.findtext("pubDate")),
                         datetime.fromisoformat(self.issue["published_at"]))
        cover_shot = p.cover_shot(self.issue)
        self.assertEqual(cover_shot["id"], "04")
        cover = self.issue["images"][cover_shot["id"]]
        self.assertEqual(entry.find("enclosure").attrib, {
            "url": "https://veripsa.com" + p.cover_url(self.issue),
            "length": str(cover["bytes"]), "type": "image/jpeg"})
        self.assertIn("?v=" + cover["sha256"][:12], entry.find("enclosure").get("url"))
        self.assertEqual(channel.find("{http://www.w3.org/2005/Atom}link").get("href"),
                         "https://veripsa.com" + p.feed_url())
        self.assertEqual(entry.findtext("{http://search.yahoo.com/mrss/}description"), cover_shot["alt"])
        self.assertNotIn(draft["title"], p.generate_photo_rss([draft, *published]))

    def test_published_issue_requires_every_cut_and_matching_visual_review(self):
        self.release_fixture()
        self.issue["images"].pop("03")
        with self.assertRaisesRegex(ValueError, "Missing.*03"):
            p.published_issues(self.catalog, root=self.root, now=NOW)
        self.release_fixture()
        self.issue["images"]["03"]["review"]["issue_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "visual review"):
            p.published_issues(self.catalog, root=self.root, now=NOW)

    def test_changed_jpeg_or_native_receipt_never_reuses_an_acceptance(self):
        self.release_fixture()
        path = self.root / self.issue["images"]["01"]["path"]
        path.write_bytes(path.read_bytes()[:-2] + b"changed\xff\xd9")
        with self.assertRaisesRegex(ValueError, "JPEG hash"):
            p.published_issues(self.catalog, root=self.root, now=NOW)
        self.release_fixture()
        path = self.root / p.receipt_path(self.issue, "01")
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "receipt hash"):
            p.published_issues(self.catalog, root=self.root, now=NOW)

    def test_storage_counter_includes_gallery_and_preserves_both_caps(self):
        self.release_fixture()
        total = sum(file.stat().st_size for file in (self.root / "assets/generated").glob("*.jpg"))
        self.assertEqual(p.archive_bytes(self.root), total)
        with patch.object(p, "MAX_ARCHIVE_BYTES", total - 1), self.assertRaisesRegex(ValueError, "1.5 GiB"):
            p.published_issues(self.catalog, root=self.root, now=NOW)
        path = self.root / self.issue["images"]["01"]["path"]
        path.write_bytes(b"x" * (p.MAX_SCENE_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "size limit"):
            p.published_issues(self.catalog, root=self.root, now=NOW)

    def test_swimwear_and_framing_require_observed_review_results(self):
        self.release_fixture()
        baseline = p.issue_fingerprint(self.issue)
        for count in (0, 5):
            for number, image in enumerate(self.issue["images"].values()):
                image["review"]["swimwear_visible"] = number < count
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, "six reviewed visible-swimwear"):
                p.published_issues(self.catalog, root=self.root, now=NOW)
        self.release_fixture()
        p.published_issues(self.catalog, root=self.root, now=NOW)
        for image in self.issue["images"].values():
            image["review"]["framing"] = "full_body"
        with self.assertRaisesRegex(ValueError, "near and full/wide"):
            p.published_issues(self.catalog, root=self.root, now=NOW)
        self.assertEqual(p.issue_fingerprint(self.issue), baseline)

    def test_release_cannot_precede_visual_review(self):
        self.release_fixture()
        self.issue["published_at"] = "2026-11-14T13:30:00+09:00"
        with self.assertRaisesRegex(ValueError, "publication/review order"):
            p.published_issues(self.catalog, root=self.root, now=NOW)

    def test_incomplete_native_receipt_is_rejected_even_after_local_rehash(self):
        self.release_fixture()
        image = self.issue["images"]["01"]
        path = self.root / image["provenance"]["receipt_path"]
        receipt = json.loads(path.read_bytes())
        receipt.pop("generation")
        data = json.dumps(receipt).encode()
        path.write_bytes(data)
        image["provenance"]["receipt_sha256"] = digest(data)
        with self.assertRaisesRegex(ValueError, "Incomplete native"):
            p.published_issues(self.catalog, root=self.root, now=NOW)

    def test_status_reports_held_cuts_and_recorded_acceptance_without_claiming_verification(self):
        self.issue["images"] = {"01": {"review": {"decision": "accepted"}, "path": "not-read"}}
        self.issue["generation_holds"] = {"03": {"raw": "PRIVATE RAW ERROR"}, "04": {"raw": "PRIVATE C2PA"}}
        report = p.status_report(self.catalog)
        row = report["issues"][0]
        self.assertEqual(row["status"], "blocked_holds")
        self.assertEqual(row["recorded_accepted_count"], 1)
        self.assertEqual(row["held_cut_ids"], ["03", "04"])
        self.assertEqual(row["missing_cut_ids"], [f"{i:02}" for i in range(2, 11)])
        self.assertIs(report["fully_verified"], False)
        self.assertEqual(report["verification"], "metadata_only")
        self.assertNotIn("PRIVATE", json.dumps(report))
        self.assertNotIn("models", report)
        self.assertIn("元claim・失敗来歴を保持", row["next_action"])
        self.assertIn("担当が原因と入力の変更を確認", row["next_action"])
        self.assertIn("同条件再試行・判定迂回はしない", row["next_action"])
        self.release_fixture()
        with self.assertRaisesRegex(ValueError, "unresolved generation holds"):
            p.published_issues(self.catalog, root=self.root, now=NOW)

    def test_status_cli_does_not_verify_images_build_or_contact_network(self):
        output = io.StringIO()
        with patch.object(p, "load_photo_issues", return_value=self.catalog), \
             patch.object(p, "validate_published_issue") as verify, \
             patch.object(p, "build_photo_issues") as build_pages, \
             patch("urllib.request.urlopen") as network, redirect_stdout(output):
            self.assertEqual(p.main(["--status", "--json"]), 0)
        verify.assert_not_called()
        build_pages.assert_not_called()
        network.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["issues"][0]["status"], "draft_incomplete")

    def test_explicit_adoption_preserves_native_origin_and_requires_fresh_target_review(self):
        old_fp = self.adoption_fixture()
        history = self.issue["editorial_history"][0]
        current_fp = self.issue["issue_sha256"]
        self.assertNotEqual(current_fp, old_fp)
        self.assertEqual(p.issue_fingerprint(history["editorial"]), old_fp)
        p.validate_catalog(self.catalog)
        p.published_issues(self.catalog, root=self.root, now=NOW)
        image = self.issue["images"]["01"]
        self.assertEqual(image["provenance"], history["images"]["01"]["provenance"])
        self.assertEqual(image["review_history"], [history["images"]["01"]["review"]])
        self.assertEqual(image["review"]["issue_sha256"], current_fp)
        self.assertEqual(history["generation_holds"]["03"]["claim"], "original-rejected-claim")
        self.assertNotIn("cut-02", self.issue["images"]["02"]["provenance"]["logical_run_id"])
        # Merely revising the issue does not clear an active cut hold.
        self.issue["generation_holds"] = copy.deepcopy(history["generation_holds"])
        with self.assertRaisesRegex(ValueError, "unresolved generation holds"):
            p.published_issues(self.catalog, root=self.root, now=NOW)
        self.issue.pop("generation_holds")
        # The exact same study is unusable by the default current-FP/cut route.
        self.issue["images"]["02"].pop("adoption")
        with self.assertRaisesRegex(ValueError, "generation reference"):
            p.published_issues(self.catalog, root=self.root, now=NOW)

    def test_adoption_rejects_unverified_history_rebinding_and_reused_old_acceptance(self):
        self.adoption_fixture()
        baseline = copy.deepcopy(self.issue)
        mutations = {
            "snapshot": lambda issue: issue["editorial_history"][0]["editorial"]["column"].append("改変"),
            "changed model master": lambda issue: issue["model_snapshot"]["master"].update(sha256="f" * 64),
            "unregistered study": lambda issue: issue["editorial_history"][0].pop("studies"),
            "rejected source disguised as study": lambda issue: issue["editorial_history"][0]["images"].update(
                {"02": dict(issue["images"]["02"], review={"decision": "rejected"})}),
            "changed study provenance": lambda issue: issue["images"]["02"]["provenance"].update(candidate_commit="b" * 40),
            "unknown source": lambda issue: issue["images"]["02"]["adoption"].update(source_issue_sha256="f" * 64),
            "target cut": lambda issue: issue["images"]["02"]["adoption"].update(target_cut_id="04"),
            "target FP": lambda issue: issue["images"]["02"]["adoption"].update(target_issue_sha256="f" * 64),
            "raw provenance": lambda issue: issue["images"]["01"]["provenance"].update(candidate_commit="b" * 40),
            "lost old review": lambda issue: issue["images"]["01"].update(review_history=[]),
            "old acceptance": lambda issue: issue["images"]["01"].update(review=copy.deepcopy(
                issue["editorial_history"][0]["images"]["01"]["review"])),
            "stale review time": lambda issue: issue["images"]["01"]["review"].update(
                reviewed_at=issue["images"]["01"]["review_history"][0]["reviewed_at"]),
        }
        for name, mutate in mutations.items():
            candidate = copy.deepcopy(baseline)
            mutate(candidate)
            with self.subTest(name=name), self.assertRaises(ValueError):
                p.validate_published_issue(candidate, root=self.root, now=NOW)

    def test_accepted_article_photo_is_frozen_independently_of_future_article_image(self):
        snapshot = self.article_adoption_fixture()
        master = self.issue["model_snapshot"]["master"]
        original_fingerprint = self.issue["issue_sha256"]
        frozen = "assets/generated/photo-master-" + self.issue["model_id"] + "-" + master["sha256"][:12] + ".jpg"
        (self.root / frozen).write_bytes(self.jpeg)
        self.issue["master_archive"] = {"path": frozen, "sha256": master["sha256"]}
        self.issue["images"]["04"]["path"] = frozen
        # A legitimate later news-photo replacement must not invalidate the old issue.
        (self.root / master["path"]).write_bytes(b"later article photograph")
        p.validate_catalog(self.catalog)
        p.published_issues(self.catalog, root=self.root, now=NOW)
        self.assertEqual(p.issue_fingerprint(self.issue), original_fingerprint)
        self.assertEqual(p.photo_image_url(self.issue, "04"),
                         p.issue_url(self.issue) + "04.jpg?v=" + snapshot["manifest"]["sha256"][:12])
        self.assertNotIn(original_fingerprint, self.issue["images"]["04"]["provenance"]["logical_run_id"])
        output = self.root / "out"
        p.build_photo_issues([self.issue], self.catalog["models"], build.layout,
                            lambda *args: None, output, root=self.root)
        self.assertEqual(len(list(output.rglob("*.jpg"))), 10)
        published = output / p.issue_url(self.issue).lstrip("/") / "04.jpg"
        self.assertEqual(published.read_bytes(), self.jpeg)
        published.write_bytes(b"conflicting output")
        with self.assertRaisesRegex(ValueError, "conflicts with its accepted source"):
            p.build_photo_issues([self.issue], self.catalog["models"], build.layout,
                                lambda *args: None, output, root=self.root)
        for archive in ({"path": frozen, "sha256": "f" * 64},
                        {"path": master["path"], "sha256": master["sha256"]}):
            self.issue["master_archive"] = archive
            with self.assertRaisesRegex(ValueError, "archive binding"):
                p.published_issues(self.catalog, root=self.root, now=NOW)

    def test_article_photo_adoption_rejects_changed_source_review_native_origin_and_target(self):
        self.article_adoption_fixture()
        baseline = copy.deepcopy(self.issue)
        source_slug = self.issue["source_article"]["slug"]
        def tamper_native_and_rehash_snapshot(issue):
            snapshot = issue["article_photo_sources"][source_slug]
            snapshot["manifest"]["native_candidate"]["identity"] = "f" * 64
            snapshot["accepted_review"]["generation_record_sha256"] = p.generation_record_fingerprint(snapshot["manifest"])
            snapshot["snapshot_sha256"] = p.generation_record_fingerprint({key: snapshot[key] for key in
                ("article", "manifest", "accepted_review")})
        mutations = {
            "native mismatch after snapshot rehash": tamper_native_and_rehash_snapshot,
            "article snapshot": lambda issue: issue["article_photo_sources"][source_slug]["article"].update(title="改変"),
            "manifest snapshot": lambda issue: issue["article_photo_sources"][source_slug]["manifest"].update(bytes=1),
            "original accepted": lambda issue: issue["article_photo_sources"][source_slug]["accepted_review"].update(decision="rejected"),
            "raw provenance": lambda issue: issue["images"]["04"]["provenance"].update(candidate_commit="b" * 40),
            "target cut": lambda issue: issue["images"]["04"]["adoption"].update(target_cut_id="05"),
            "target review": lambda issue: issue["images"]["04"]["review"].update(issue_sha256="f" * 64),
            "stale review": lambda issue: issue["images"]["04"]["review"].update(reviewed_at="2026-11-14T14:00:00+09:00"),
            "duplicated saved JPEG": lambda issue: issue["images"]["04"].update(path=p.image_path(issue, "04")),
        }
        for name, mutate in mutations.items():
            candidate = copy.deepcopy(baseline)
            mutate(candidate)
            with self.subTest(name=name), self.assertRaises(ValueError):
                p.validate_published_issue(candidate, root=self.root, now=NOW)

    def test_generation_binding_and_future_release_fail_closed(self):
        self.release_fixture()
        self.issue["images"]["01"]["provenance"]["logical_run_id"] = "wrong-cut-01"
        with self.assertRaisesRegex(ValueError, "generation reference"):
            p.published_issues(self.catalog, root=self.root, now=NOW)
        self.release_fixture()
        self.issue["published_at"] = "2031-11-14T13:30:00+09:00"
        with self.assertRaisesRegex(ValueError, "not released"):
            p.published_issues(self.catalog, root=self.root, now=NOW)


if __name__ == "__main__":
    unittest.main()
