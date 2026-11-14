"""Pure new-issue plan checks: no files, network, images or publication."""
import copy
import io
import json
import hashlib
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from scripts.photo_issue_plan import freeze_model_plan, generation_inputs
from scripts.photo_body_profiles import BASIS, body_profile_fingerprint


def model_fixture():
    master = {"path": "assets/generated/body-master.jpg", "sha256": "a" * 64,
              "source_png_sha256": "b" * 64, "provenance": "assets/generated/manifest.json#source"}
    return {"id": "fictional-model", "name": "架空さや", "age": 27, "fictional": True,
            "appearance": "短い暗色ボブ。自然な成人の体型。", "master": master,
            "preferred_face_reference": {"path": "assets/generated/face-master.jpg", "sha256": "c" * 64},
            "character": {"first_person": "私", "personality": ["穏やか"], "voice": "柔らかな短い文。",
                          "likes": ["水辺"], "weakness": "寄り道が多い。"},
            "body_profile": {"version": 1, "basis": BASIS, "height_cm": 164.0, "bust_cm": 91,
                             "waist_cm": 61, "hip_cm": 91, "shoulder_width_cm": 37,
                             "inseam_cm": 76, "heads_tall": 7.3},
            "body_reference": {"state": "accepted_reference", **master}}


def issue_fixture(snapshot):
    # Fingerprint/catalog validation belongs to the caller, before plan assembly.
    return {"issue_sha256": "d" * 64, "model_id": snapshot["id"], "model_snapshot": snapshot,
            "setting": {"year": 2031, "month": 11, "location": "屋内リゾートプール", "aspect_ratio": "16:9"},
            "shots": [{"id": "04", "description": "青いタイルのプールサイドで、普通のビキニを着て自然に座る。"}]}


class PhotoIssuePlanTests(unittest.TestCase):
    def test_freeze_copies_and_normalizes_without_changing_registry(self):
        model = model_fixture()
        before = copy.deepcopy(model)
        snapshot = freeze_model_plan(model)
        self.assertEqual(model, before)
        self.assertIs(type(snapshot["body_profile"]["height_cm"]), int)
        self.assertEqual(snapshot["body_profile_sha256"], body_profile_fingerprint(model["body_profile"]))
        self.assertEqual(snapshot["face_reference"], model["preferred_face_reference"])
        snapshot["character"]["likes"].append("散歩")
        snapshot["body_reference"]["sha256"] = "e" * 64
        snapshot["master"]["sha256"] = "f" * 64
        self.assertEqual(model, before)

    def test_face_only_or_unprepared_body_never_authorizes_plan(self):
        model = model_fixture()
        for reference in ({"state": "unprepared"}, None):
            with self.subTest(reference=reference):
                model["body_reference"] = reference
                with self.assertRaises(ValueError):
                    freeze_model_plan(model)
        for age in (19, True):
            model = model_fixture()
            model["age"] = age
            with self.assertRaisesRegex(ValueError, "adult"):
                freeze_model_plan(model)

    def test_generation_uses_frozen_design_and_role_specific_references(self):
        model = model_fixture()
        issue = issue_fixture(freeze_model_plan(model))
        before = copy.deepcopy(issue)
        plan = generation_inputs(issue, "04")
        model["body_profile"]["bust_cm"] += 10
        model["preferred_face_reference"]["sha256"] = "e" * 64
        model["appearance"] = "別の髪形"
        self.assertEqual(generation_inputs(issue, "04"), plan)
        self.assertEqual(issue, before)
        self.assertEqual([ref["role"] for ref in plan["references"]], ["face", "body"])
        self.assertEqual([ref["sha256"] for ref in plan["references"]], ["c" * 64, "a" * 64])
        self.assertEqual(plan["issue_sha256"], issue["issue_sha256"])
        self.assertEqual(plan["cut_id"], "04")
        self.assertEqual(plan["body_profile_sha256"], issue["model_snapshot"]["body_profile_sha256"])
        self.assertEqual(plan["verification"], "metadata_only")
        self.assertIs(plan["requires_visual_review"], True)
        for text in ("27歳", "身長164cm", "バスト91cm", "実測値ではなく", "普通のビキニ", "2031年11月"):
            self.assertIn(text, plan["prompt"])
        plan["references"][0]["path"] = "changed.jpg"
        self.assertEqual(issue, before)


class PhotoIssuePlanIntegrationTests(unittest.TestCase):
    def test_cli_checks_real_reference_bytes_and_rejects_a_changed_reference(self):
        from scripts import photo_issues as p
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(p.main(["--model-plan", "nagino-saya", "--json"]), 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report["reference_bytes_verified"])
        self.assertTrue(report["requires_visual_review"])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            snapshot = report["model_snapshot"]
            for ref in (snapshot["face_reference"], snapshot["body_reference"]):
                target = root / ref["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((p.ROOT / ref["path"]).read_bytes())
            (root / snapshot["body_reference"]["path"]).write_bytes(b"not the accepted JPEG")
            with patch.object(p, "ROOT", root), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    p.main(["--model-plan", "nagino-saya", "--json"])
            self.assertEqual(error.exception.code, 2)

    def test_new_snapshot_changes_issue_fingerprint_and_checks_its_body_design(self):
        from scripts import photo_issues as p
        catalog = copy.deepcopy(p.load_photo_issues())
        issue = catalog["issues"][0]
        original = issue["issue_sha256"]
        issue.update(slug="new-body-design-test-issue", images={})
        issue.pop("editorial_history", None)
        issue.pop("article_photo_sources", None)
        issue["model_snapshot"] = freeze_model_plan(catalog["models"][0])
        issue["issue_sha256"] = p.issue_fingerprint(issue)
        self.assertNotEqual(issue["issue_sha256"], original)
        p.validate_catalog(catalog)
        issue["model_snapshot"]["body_profile"]["height_cm"] += 1
        issue["issue_sha256"] = p.issue_fingerprint(issue)
        with self.assertRaisesRegex(ValueError, "body profile fingerprint"):
            p.validate_catalog(catalog)

    def test_new_publication_requires_the_same_body_plan_in_generation_and_review(self):
        from scripts import photo_issues as p
        from tests import test_photo_issues as base
        fixture = base.PhotoIssueTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        issue = fixture.issue
        issue["slug"] = "new-body-design-test-issue"
        snapshot = freeze_model_plan(fixture.catalog["models"][0])
        issue["model_snapshot"] = snapshot
        issue["issue_sha256"] = p.issue_fingerprint(issue)
        for ref in (snapshot["master"], snapshot["face_reference"], snapshot["body_reference"]):
            target = fixture.root / ref["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((p.ROOT / ref["path"]).read_bytes())
        fixture.release_fixture()
        for cut, image in issue["images"].items():
            plan = generation_inputs(issue, cut)
            receipt_file = fixture.root / p.receipt_path(issue, cut)
            receipt = json.loads(receipt_file.read_bytes())
            receipt["generation"].update(prompt=plan["prompt"],
                prompt_sha256=hashlib.sha256(plan["prompt"].encode()).hexdigest())
            receipt["identity"] = hashlib.sha256(json.dumps([
                receipt["repository"], receipt["slug"], receipt["article_sha256"],
                receipt["logical_run_id"], plan["prompt"]], ensure_ascii=False,
                separators=(",", ":")).encode()).hexdigest()
            raw = json.dumps(receipt, ensure_ascii=False).encode()
            receipt_file.write_bytes(raw)
            image["provenance"].update(receipt_sha256=hashlib.sha256(raw).hexdigest(),
                body_profile_sha256=plan["body_profile_sha256"], reference_images=plan["references"])
            image["review"]["body_profile_sha256"] = plan["body_profile_sha256"]
        p.validate_published_issue(issue, root=fixture.root, now=base.NOW)
        bad = copy.deepcopy(issue)
        bad["images"]["04"]["review"].pop("body_profile_sha256")
        with self.assertRaisesRegex(ValueError, "body plan"):
            p.validate_published_issue(bad, root=fixture.root, now=base.NOW)
        bad = copy.deepcopy(issue)
        bad["images"]["04"]["provenance"]["reference_images"][1]["sha256"] = "e" * 64
        with self.assertRaisesRegex(ValueError, "body plan"):
            p.validate_published_issue(bad, root=fixture.root, now=base.NOW)

    def test_tampered_snapshot_and_unknown_cut_are_rejected(self):
        original = issue_fixture(freeze_model_plan(model_fixture()))
        for change in (lambda i: i["model_snapshot"]["body_profile"].update(bust_cm=92),
                       lambda i: i["model_snapshot"].update(body_profile_sha256="e" * 64),
                       lambda i: i["model_snapshot"].update(body_reference={"state": "unprepared"}),
                       lambda i: i["model_snapshot"]["face_reference"].update(path="../../face.jpg"),
                       lambda i: i.update(model_id="another-model"),
                       lambda i: i["shots"].append(copy.deepcopy(i["shots"][0]))):
            bad = copy.deepcopy(original)
            change(bad)
            with self.assertRaises(ValueError):
                generation_inputs(bad, "04")
        for cut in ("05", "4", 4):
            with self.assertRaises(ValueError):
                generation_inputs(original, cut)

    def test_legacy_snapshot_is_not_silently_upgraded(self):
        model = model_fixture()
        legacy = {key: copy.deepcopy(model[key]) for key in
                  ("id", "name", "age", "fictional", "appearance", "master")}
        issue = issue_fixture(legacy)
        before = copy.deepcopy(issue)
        with self.assertRaisesRegex(ValueError, "Legacy"):
            generation_inputs(issue, "04")
        self.assertEqual(issue, before)


if __name__ == "__main__":
    unittest.main()
