"""Live GitHub global stop checks, without cloud mutations or inference."""
import contextlib
import io
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from scripts import cloud_image_guard as guard
from scripts import github_cpu_scenes as cpu

ACTIVE = {"path": ".github/workflows/free-cpu-scenes.yml", "state": "active"}


class CpuCloudGuardTests(unittest.TestCase):
    def test_every_check_reads_the_exact_workflow_again_without_cache(self):
        completed = subprocess.CompletedProcess([], 0, json.dumps(ACTIVE), "")
        with patch.dict(os.environ, {"GH_TOKEN": "offline-fixture"}), \
             patch.object(guard.subprocess, "run", return_value=completed) as run:
            guard.check_cloud_images_allowed()
            guard.check_cloud_images_allowed()
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            self.assertEqual(call.args[0], ["gh", "api", "--method", "GET",
                "repos/RollNuts/mihassei-news/actions/workflows/free-cpu-scenes.yml"])
            self.assertEqual(call.kwargs["timeout"], 30)
            self.assertTrue(call.kwargs["check"])

    def test_disabled_missing_and_wrong_workflow_responses_stop(self):
        for state in ({**ACTIVE, "state": "disabled_manually"}, {**ACTIVE, "state": "disabled_inactivity"},
                      {"path": ACTIVE["path"]}, {**ACTIVE, "path": ".github/workflows/check.yml"}, None):
            with self.subTest(state=state), self.assertRaisesRegex(RuntimeError, "not confirmed active"):
                guard.check_cloud_images_allowed(lambda: state)

    def test_auth_network_timeout_and_malformed_json_fail_closed(self):
        failures = (subprocess.CalledProcessError(1, ["gh"]),
                    subprocess.TimeoutExpired(["gh"], 30), OSError("offline"))
        for error in failures:
            with self.subTest(error=type(error).__name__), \
                 patch.dict(os.environ, {"GH_TOKEN": "offline-fixture"}), \
                 patch.object(guard.subprocess, "run", side_effect=error), \
                 self.assertRaisesRegex(RuntimeError, "stop state unavailable"):
                guard.check_cloud_images_allowed()
        with patch.dict(os.environ, {"GH_TOKEN": "offline-fixture"}), \
             patch.object(guard.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "bad JSON", "")), \
             self.assertRaisesRegex(RuntimeError, "stop state unavailable"):
            guard.check_cloud_images_allowed()
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(guard.subprocess, "run", side_effect=AssertionError("unauthenticated request")), \
             self.assertRaisesRegex(RuntimeError, "stop state unavailable"):
            guard.check_cloud_images_allowed()

    def test_stopped_cpu_never_downloads_a_model_or_writes_an_image(self):
        article = {"slug": "cpu-guard-fixture"}
        with patch.object(cpu, "load_articles", return_value=[article]), \
             patch.object(cpu, "select_articles", return_value=[article]), \
             patch.object(cpu, "permitted_ci", return_value=True), \
             patch.object(cpu, "check_cloud_images_allowed", side_effect=RuntimeError("cloud stopped")), \
             patch.object(cpu, "load_pipeline", side_effect=AssertionError("model downloaded")), \
             patch.object(cpu, "write_generated", side_effect=AssertionError("image saved")), \
             contextlib.redirect_stdout(io.StringIO()), \
             self.assertRaisesRegex(RuntimeError, "cloud stopped"):
            cpu.main(["--slug", article["slug"]])

    def test_stop_after_model_loading_is_checked_before_inference(self):
        article = {"slug": "cpu-guard-fixture"}
        with patch.object(cpu, "load_articles", return_value=[article]), \
             patch.object(cpu, "select_articles", return_value=[article]), \
             patch.object(cpu, "permitted_ci", return_value=True), \
             patch.object(cpu, "check_cloud_images_allowed", side_effect=[None, None, RuntimeError("cloud stopped")]), \
             patch.object(cpu, "ensure_disk"), patch.object(cpu, "load_pipeline", return_value=(object(), None)), \
             patch.object(cpu, "render_one", side_effect=AssertionError("inference started")), \
             patch.object(cpu, "write_generated", side_effect=AssertionError("image saved")), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
             self.assertRaisesRegex(RuntimeError, "No scenes passed"):
            cpu.main(["--slug", article["slug"]])

    def test_offline_dry_run_does_not_require_live_state(self):
        with patch.object(cpu, "load_articles", return_value=[]), \
             patch.object(cpu, "select_articles", return_value=[]), \
             patch.object(cpu, "check_cloud_images_allowed", side_effect=AssertionError("online check")), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cpu.main(["--dry-run"]), 0)
