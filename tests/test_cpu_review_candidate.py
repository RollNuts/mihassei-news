"""CPU candidate persistence using exact local bytes and an in-memory Git API."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import github_cpu_scenes as cpu
from scripts import stage_cpu_review as stage
from scripts.generated_photos import article_fingerprint, MAX_SCENE_BYTES
from scripts.local_scene_photos import stable_seed, write_generated
from scripts.media_policy import MIN_JPEG_BYTES, jpeg_dimensions

ROOT = Path(__file__).resolve().parents[1]


class MemoryGitHub(stage.GitHub):
    """Exercise the helper's real ref/tree/read parsers without any network."""
    def __init__(self, originals):
        self.blobs, self.trees, self.commits, self.refs = {}, {}, {}, {}
        self.calls = []
        self.ref_mode = "ok"
        self.fail_path = None
        self.source_commit = self.install(originals)
        self.refs["main"] = self.source_commit

    def install(self, files):
        tree = {}
        for path, data in files.items():
            sha = stage.blob_sha(data)
            self.blobs[sha] = data
            tree[path] = {"mode": "100644", "type": "blob", "sha": sha}
        tree_sha = hashlib.sha1(stage.encoded(tree)).hexdigest()
        self.trees[tree_sha] = tree
        commit = hashlib.sha1(stage.encoded({"tree": tree_sha, "parents": []})).hexdigest()
        self.commits[commit] = {"tree": {"sha": tree_sha}}
        return commit

    def api(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        if self.fail_path == path:
            raise RuntimeError("simulated unavailable GitHub operation")
        if method == "GET":
            if path.startswith("/git/ref/heads/"):
                branch = path.removeprefix("/git/ref/heads/")
                if branch not in self.refs:
                    raise stage.MissingRef("absent exact ref")
                return {"ref": "refs/heads/" + branch,
                        "object": {"type": "commit", "sha": self.refs[branch]}}
            if path.startswith("/git/commits/"):
                return self.commits[path.removeprefix("/git/commits/")]
            if path.startswith("/git/trees/"):
                sha = path.removeprefix("/git/trees/").removesuffix("?recursive=1")
                return {"sha": sha, "truncated": False,
                        "tree": [{"path": name, **record}
                                 for name, record in self.trees[sha].items()]}
            if path.startswith("/git/blobs/"):
                data = self.blobs[path.removeprefix("/git/blobs/")]
                return {"encoding": "base64", "content": base64.b64encode(data).decode(),
                        "size": len(data)}
        if method == "POST" and path == "/git/blobs":
            data = base64.b64decode(body["content"], validate=True)
            sha = stage.blob_sha(data)
            self.blobs[sha] = data
            return {"sha": sha}
        if method == "POST" and path == "/git/trees":
            tree = copy.deepcopy(self.trees[body["base_tree"]])
            for entry in body["tree"]:
                tree[entry["path"]] = {key: entry[key] for key in ("mode", "type", "sha")}
            sha = hashlib.sha1(stage.encoded(tree)).hexdigest()
            self.trees[sha] = tree
            return {"sha": sha}
        if method == "POST" and path == "/git/commits":
            sha = hashlib.sha1(stage.encoded(body)).hexdigest()
            self.commits[sha] = {"tree": {"sha": body["tree"]}}
            return {"sha": sha}
        if method == "POST" and path == "/git/refs":
            branch = body["ref"].removeprefix("refs/heads/")
            if branch in self.refs:
                raise RuntimeError("existing ref; never replace")
            if self.ref_mode in {"ok", "lost_response"}:
                self.refs[branch] = body["sha"]
            if self.ref_mode in {"lost_response", "failed"}:
                raise RuntimeError("simulated lost or failed create response")
            return {"ref": body["ref"], "object": {"sha": body["sha"]}}
        raise AssertionError("Unexpected API call: " + method + " " + path)

    def bytes_at(self, commit, path):
        _, files = self.tree(commit)
        return self.read(files, path)

    def writes(self):
        return [call for call in self.calls if call[0] != "GET"]


class CpuReviewCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for path in sorted((ROOT / "assets/generated").glob("*.jpg")):
            data = path.read_bytes()
            if MIN_JPEG_BYTES <= len(data) <= MAX_SCENE_BYTES and jpeg_dimensions(data) == (1200, 675):
                cls.jpeg = data
                break
        else:
            raise AssertionError("A conforming existing JPEG is required for the offline fixture")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cpu-candidate-test-")
        self.base = Path(self.temporary.name)
        self.root = self.base / "source"
        (self.root / "content").mkdir(parents=True)
        self.original = {"slug": "cpu-candidate-fixture", "title": "A fictional fixture",
                         "summary": "A short comedy", "category": "社会",
                         "paragraphs": ["A fictional article about a plate."],
                         "scene_prompt": "A bright dinner plate beside a volleyball.",
                         "visual_strategy": "cpu-photo"}
        self.request = {"backfill_scene": "", "backfill_reference": False, "retry_held": False}

    def tearDown(self):
        self.temporary.cleanup()

    def fixture(self, *, backfill=False):
        if backfill:
            self.original["visual_strategy"] = "illustration"
            self.request.update(backfill_scene="A bright ceramic plate holds a small volleyball in a kitchen.",
                                retry_held=True)
        originals = {"content/articles.json": stage.encoded([self.original]),
                     "assets/generated/manifest.json": b'{"version":1,"images":{}}\n',
                     "content/visual_reviews.json": b'{"version":1,"reviews":{}}\n',
                     "keep.txt": b"untouched source baseline\n"}
        (self.root / "content/articles.json").write_bytes(originals["content/articles.json"])
        api = MemoryGitHub(originals)
        plan = stage.prepare(self.root, slug=self.original["slug"], run_id="12345",
                             source_commit=api.source_commit, request=copy.deepcopy(self.request))
        clone = copy.deepcopy(self.original)
        if backfill:
            clone.update(scene_prompt=self.request["backfill_scene"], visual_strategy="cpu-photo")
        generated = self.base / "generated"
        generated.mkdir()
        write_generated(clone, self.jpeg, cpu.MODEL, root=generated, origin=cpu.ORIGIN)
        bundle = self.base / "bundle"
        cpu.export_review_bundle([clone], bundle, root=generated,
                                 source_article_sha256={clone["slug"]: article_fingerprint(self.original)}
                                 if backfill else None)
        return api, plan, bundle, clone

    def test_exact_original_manifest_jpeg_and_snapshot_hashes_are_preserved(self):
        api, plan, bundle, clone = self.fixture(backfill=True)
        manifest = (bundle / "assets/generated/manifest.json").read_bytes()
        record = json.loads(manifest)["images"][clone["slug"]]
        self.assertGreater(record["seed"], 2**53)
        self.assertEqual(record["seed"], stable_seed(clone))
        self.assertEqual(record["source_article_sha256"], article_fingerprint(self.original))
        self.assertEqual(record["article_sha256"], article_fingerprint(clone))
        self.assertNotEqual(record["source_article_sha256"], record["article_sha256"])
        result = stage.save(api, plan, bundle)
        prefix = plan["directory"] + "/"
        self.assertEqual(api.bytes_at(result["commit"], prefix + "bundle/assets/generated/manifest.json"), manifest)
        self.assertEqual(api.bytes_at(result["commit"], prefix + "bundle/content/articles.json"),
                         (bundle / "content/articles.json").read_bytes())
        self.assertEqual(api.bytes_at(result["commit"], prefix + "source-article.json"),
                         stage.encoded(self.original))
        self.assertEqual(api.bytes_at(result["commit"], prefix + "bundle/assets/generated/" + clone["slug"] + ".jpg"), self.jpeg)
        receipt = json.loads(api.bytes_at(result["commit"], prefix + "receipt.json"))
        self.assertEqual(receipt["kind"], "cpu_image_candidate")
        self.assertEqual(receipt["status"], "awaiting_visual_review")
        self.assertEqual(result["jpeg_sha256"], hashlib.sha256(self.jpeg).hexdigest())
        for path, item in receipt["files"].items():
            data = api.bytes_at(result["commit"], path)
            self.assertEqual(item, {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})

    def test_backfill_wrong_original_fingerprint_is_rejected_before_writes(self):
        api, plan, bundle, clone = self.fixture(backfill=True)
        path = bundle / "assets/generated/manifest.json"
        payload = json.loads(path.read_bytes())
        payload["images"][clone["slug"]]["source_article_sha256"] = article_fingerprint(clone)
        path.write_bytes(stage.encoded(payload))
        with self.assertRaisesRegex(ValueError, "original source article hash mismatch"):
            stage.save(api, plan, bundle)
        self.assertEqual(api.writes(), [])

    def test_mismatched_original_source_stops_before_writes(self):
        api, plan, bundle, _ = self.fixture()
        wrong = {**self.original, "title": "A mismatched source snapshot"}
        (self.root / "content/articles.json").write_bytes(stage.encoded([wrong]))
        plan = stage.prepare(self.root, slug=wrong["slug"], run_id="12345",
                             source_commit=api.source_commit, request=self.request)
        with self.assertRaisesRegex(RuntimeError, "source article snapshot mismatched"):
            stage.save(api, plan, bundle)
        self.assertEqual(api.writes(), [])

    def test_direct_recovery_cannot_change_ref_destination_or_original_hash(self):
        api, plan, bundle, _ = self.fixture()
        for key, value in (("branch", "main"), ("directory", "content"),
                           ("source_article_sha256", "0" * 64)):
            with self.subTest(key=key):
                changed = {**plan, key: value}
                with self.assertRaisesRegex(ValueError, "identity or isolated destination mismatch"):
                    stage.save(api, changed, bundle)
                self.assertEqual(api.writes(), [])

    def test_later_main_edit_preserves_original_candidate_and_reports_stale(self):
        api, plan, bundle, _ = self.fixture()
        original_sha = plan["source_article_sha256"]
        changed = copy.deepcopy(self.original)
        changed["paragraphs"] = ["The current article has changed."]
        api.refs["main"] = api.install({"content/articles.json": stage.encoded([changed])})
        saved = stage.save(api, plan, bundle)
        self.assertEqual(stage.source_state(api, plan), {
            "source_state": "stale", "current_article_sha256": article_fingerprint(changed)})
        receipt = json.loads(api.bytes_at(saved["commit"], plan["directory"] + "/receipt.json"))
        self.assertEqual(receipt["source_article_sha256"], original_sha)
        self.assertIn(original_sha[:16], saved["branch"])
        self.assertNotEqual(original_sha, article_fingerprint(changed))

    def test_mismatched_bundled_article_stops_before_writes(self):
        api, plan, bundle, clone = self.fixture()
        clone["paragraphs"] = ["This valid bundle belongs to a different article revision."]
        other = self.base / "other"
        other.mkdir()
        write_generated(clone, self.jpeg, cpu.MODEL, root=other, origin=cpu.ORIGIN)
        mismatched = self.base / "mismatched"
        cpu.export_review_bundle([clone], mismatched, root=other)
        with self.assertRaisesRegex(ValueError, "does not match the original source"):
            stage.save(api, plan, mismatched)
        self.assertEqual(api.writes(), [])

    def test_existing_candidate_probe_reuses_exact_bytes_without_writes(self):
        api, plan, bundle, _ = self.fixture()
        saved = stage.save(api, plan, bundle)
        api.calls.clear()
        self.assertEqual(stage.probe(api, plan, stage.validate_source(api, plan)), saved)
        self.assertEqual(stage.save(api, plan, bundle), saved)
        self.assertEqual(api.writes(), [])

    def test_existing_candidate_conflicting_provenance_is_never_overwritten(self):
        api, plan, bundle, clone = self.fixture()
        stage.save(api, plan, bundle)
        api.calls.clear()
        manifest_path = bundle / "assets/generated/manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["images"][clone["slug"]]["generated_at"] = "2026-11-16T00:00:00+00:00"
        manifest_path.write_bytes(stage.encoded(manifest))
        with self.assertRaisesRegex(RuntimeError, "different candidate"):
            stage.save(api, plan, bundle)
        self.assertEqual(api.writes(), [])

    def test_lost_create_ref_response_recovers_only_from_verified_branch(self):
        api, plan, bundle, _ = self.fixture()
        api.ref_mode = "lost_response"
        saved = stage.save(api, plan, bundle)
        self.assertEqual(api.refs[plan["branch"]], saved["commit"])
        self.assertEqual(saved["jpeg_sha256"], hashlib.sha256(self.jpeg).hexdigest())
        self.assertEqual(len([c for c in api.writes() if c[1] == "/git/refs"]), 1)

    def test_failed_or_unconfirmed_ref_creation_stops(self):
        for mode in ("failed", "unconfirmed"):
            with self.subTest(mode=mode):
                api, plan, bundle, _ = self.fixture()
                api.ref_mode = mode
                with self.assertRaises(RuntimeError):
                    stage.save(api, plan, bundle)
                self.assertNotIn(plan["branch"], api.refs)
                self.assertEqual(len([c for c in api.writes() if c[1] == "/git/refs"]), 1)
                shutil.rmtree(bundle)
                shutil.rmtree(self.base / "generated")

    def test_ambiguous_ref_read_is_not_treated_as_absent(self):
        api, plan, bundle, _ = self.fixture()
        api.fail_path = "/git/ref/heads/" + plan["branch"]
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            stage.save(api, plan, bundle)
        self.assertEqual(api.writes(), [])

    def test_only_candidate_paths_and_create_only_api_are_written(self):
        api, plan, bundle, _ = self.fixture()
        main_before = api.refs["main"]
        result = stage.save(api, plan, bundle)
        self.assertEqual(api.refs["main"], main_before)
        self.assertEqual(set(api.refs), {"main", plan["branch"]})
        for method, path, body in api.writes():
            self.assertEqual(method, "POST")
            self.assertIn(path, {"/git/blobs", "/git/trees", "/git/commits", "/git/refs"})
            if path == "/git/trees":
                self.assertTrue(all(item["path"].startswith(plan["directory"] + "/") for item in body["tree"]))
            if path == "/git/refs":
                self.assertEqual(body["ref"], "refs/heads/" + plan["branch"])
        for protected in ("content/articles.json", "assets/generated/manifest.json", "content/visual_reviews.json"):
            self.assertEqual(api.bytes_at(result["commit"], protected), api.bytes_at(main_before, protected))

    def test_same_run_retry_recovers_existing_candidate_without_download_or_generation(self):
        api, plan, bundle, _ = self.fixture()
        saved = stage.save(api, plan, bundle)
        api.calls.clear()
        with patch.object(stage.subprocess, "run", side_effect=AssertionError("artifact download attempted")), \
             patch.object(cpu, "load_pipeline", side_effect=AssertionError("generation attempted")):
            self.assertEqual(stage.recover(api, plan, self.base / "retry", attempt=2), saved)
        self.assertEqual(api.writes(), [])

    def test_same_run_retry_saves_only_the_original_downloaded_bundle(self):
        api, plan, bundle, _ = self.fixture(backfill=True)
        destination = self.base / "retry"
        def download(argv, **options):
            self.assertEqual(argv[:4], ["gh", "run", "download", plan["logical_run_id"]])
            self.assertIn("cpu-scenes-review-" + plan["logical_run_id"], argv)
            shutil.copytree(bundle, destination)
            return subprocess.CompletedProcess(argv, 0)
        with patch.object(stage.subprocess, "run", side_effect=download) as run, \
             patch.object(cpu, "load_pipeline", side_effect=AssertionError("generation attempted")):
            result = stage.recover(api, plan, destination, attempt=2)
        self.assertEqual(result["status"], "saved")
        run.assert_called_once()
        self.assertEqual((destination / "assets/generated/manifest.json").read_bytes(),
                         (bundle / "assets/generated/manifest.json").read_bytes())

    def test_failed_or_invalid_artifact_download_never_regenerates_or_writes(self):
        api, plan, bundle, _ = self.fixture()
        for failure in ("download", "bundle"):
            destination = self.base / ("retry-" + failure)
            def download(argv, **options):
                if failure == "download":
                    raise subprocess.CalledProcessError(1, argv)
                destination.mkdir()
                (destination / "unexpected.txt").write_text("not a review bundle")
                return subprocess.CompletedProcess(argv, 0)
            with self.subTest(failure=failure), \
                 patch.object(stage.subprocess, "run", side_effect=download), \
                 patch.object(cpu, "load_pipeline", side_effect=AssertionError("generation attempted")):
                with self.assertRaises((subprocess.CalledProcessError, ValueError)):
                    stage.recover(api, plan, destination, attempt=2)
                self.assertEqual(api.writes(), [])


class CpuReviewWorkflowTests(unittest.TestCase):
    def test_recovery_precedes_selection_quota_and_runtime_and_save_is_review_only(self):
        workflow = (ROOT / ".github/workflows/free-cpu-scenes.yml").read_text()
        self.assertIn("ref: ${{ github.sha }}", workflow)
        recovery = workflow.index("id: candidate")
        for step in ("id: plan", "id: backfill_quota", "Install CPU-only"):
            self.assertLess(recovery, workflow.index(step))
        self.assertIn("if: ${{ !inputs.publish }}", workflow)
        self.assertIn('if [ "$CANDIDATE_SAVED" = 1 ]; then', workflow)
        self.assertIn("if: steps.generate.outcome == 'success' && !inputs.publish", workflow)
        self.assertLess(workflow.index("stage_cpu_review.py save"),
                        workflow.index("Verify the build after successful generation"))
        self.assertIn("if: always() && steps.plan.outcome == 'success'", workflow)


if __name__ == "__main__":
    unittest.main()
