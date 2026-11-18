"""The shared read-only snapshot must be fresh and successfully built."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import site_build_fixture as fixture


class SiteBuildFixtureTests(unittest.TestCase):
    def test_existing_dist_still_builds_once_for_all_readonly_consumers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "dist").mkdir()
            (root / "dist" / "stale.html").write_text("Old output is not proof of a fresh build.")
            with patch.object(fixture, "ROOT", root), \
                 patch.object(fixture, "_built", False), \
                 patch.object(fixture.subprocess, "run") as build:
                for _ in range(6):
                    fixture.ensure_site_build()
                build.assert_called_once_with(
                    [sys.executable, str(root / "scripts/build.py")], cwd=root, check=True)

    def test_failed_build_is_not_reused_as_a_successful_snapshot(self):
        failure = subprocess.CalledProcessError(1, ["test-build"])
        with patch.object(fixture, "_built", False), \
             patch.object(fixture.subprocess, "run", side_effect=[failure, None]) as build:
            with self.assertRaises(subprocess.CalledProcessError):
                fixture.ensure_site_build()
            self.assertFalse(fixture._built)
            fixture.ensure_site_build()
            fixture.ensure_site_build()
            self.assertEqual(build.call_count, 2)
            self.assertTrue(fixture._built)


class CISelectionTests(unittest.TestCase):
    def test_native_engine_skips_only_existing_exact_data_pr_scopes(self):
        supported = [[("M", path)] for path in fixture.MARKET_DATA_PATHS]
        supported += [[("M", path) for path in fixture.MARKET_DATA_PATHS],
                      [("M", fixture.PHOTO_STORY_PATH)]]
        for changes in supported:
            with self.subTest(changes=changes):
                self.assertFalse(fixture.native_image_engine_tests_required(changes, "pull_request"))
                for event in ("push", "workflow_dispatch", "", None):
                    self.assertTrue(fixture.native_image_engine_tests_required(changes, event))
        path = fixture.PHOTO_STORY_PATH
        unsafe = [None, []] + [[(status, path)] for status in ("A", "D", "T", "R100", "?")]
        unsafe += [[("M", path + ".backup")], [("M", "./" + path)],
                   [("M", "content/photo_issues.json")],
                   [("M", "content/photo_editions.json")],
                   [("M", path), ("M", "content/market_bulletins.json")],
                   [("M", path), ("M", "assets/generated/manifest.json")],
                   [("M", path), ("M", "scripts/native_image_stage.js")],
                   [("M", path), ("M", "tests/test_photo_stories.py")],
                   [("M", path), ("M", ".github/workflows/check.yml")],
                   [("M", path), ("M", "docs/DAILY_EDITORIAL_BRIEF.md")]]
        for changes in unsafe:
            with self.subTest(changes=changes):
                self.assertTrue(fixture.native_image_engine_tests_required(changes, "pull_request"))
        # A later lightweight Python scope must not silently skip native tests.
        with patch.object(fixture, "python_test_command", return_value=["future-data-scope"]):
            self.assertTrue(fixture.native_image_engine_tests_required(
                [("M", "content/future-data.json")], "pull_request"))

    def test_workflow_output_is_explicit_and_missing_output_keeps_native_tests(self):
        cases = [("pull_request", [("M", fixture.PHOTO_STORY_PATH)], "false"),
                 ("pull_request", [("M", "content/market_bulletins.json")], "false"),
                 ("push", [("M", fixture.PHOTO_STORY_PATH)], "true"),
                 ("pull_request", None, "true"), ("pull_request", [], "true")]
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "github-output"
            for event, changes, expected in cases:
                with self.subTest(event=event, changes=changes), \
                     patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), \
                     patch.object(fixture, "changed_files", return_value=changes), \
                     patch.object(fixture.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
                    output.write_text("another_output=kept\n")
                    self.assertEqual(fixture.main(["--base", "a" * 40, "--event", event]), 0)
                    self.assertEqual(output.read_text(), "another_output=kept\n"
                                     + "run_native_image_engine_tests=" + expected + "\n")
                    run.assert_called_once_with(fixture.python_test_command(changes), cwd=fixture.ROOT,
                                                env={key: value for key, value in os.environ.items()
                                                     if key != "GITHUB_OUTPUT"})
        workflow = (fixture.ROOT / ".github/workflows/check.yml").read_text()
        self.assertIn("id: python_checks", workflow)
        self.assertIn("if: steps.python_checks.outputs.run_native_image_engine_tests != 'false'", workflow)
        control_step, native_step = workflow.split("      - name: Verify native image bytes", 1)
        self.assertNotIn("run_native_image_engine_tests", control_step)
        self.assertIn("run: node scripts/native_image_ci_test.js", native_step)

    def test_child_python_cannot_overwrite_the_parent_native_engine_scope_output(self):
        child = """import os
assert os.environ['VERIPSA_TEST_CHILD_ENV'] == 'kept'
output = os.environ.get('GITHUB_OUTPUT')
if output:
    with open(output, 'a') as stream:
        stream.write('run_native_image_engine_tests=true\\n')
"""
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "github-output"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output), "VERIPSA_TEST_CHILD_ENV": "kept"}), \
                 patch.object(fixture, "changed_files", return_value=[("M", fixture.PHOTO_STORY_PATH)]), \
                 patch.object(fixture, "python_test_command", return_value=[sys.executable, "-c", child]):
                self.assertEqual(fixture.main(["--base", "a" * 40, "--event", "pull_request"]), 0)
                self.assertEqual(output.read_text(), "run_native_image_engine_tests=false\n")

    def test_only_existing_modified_market_data_can_narrow_the_suite(self):
        for path in fixture.MARKET_DATA_PATHS:
            with self.subTest(path=path):
                self.assertTrue(fixture.market_data_only([("M", path)]))
        self.assertTrue(fixture.market_data_only([("M", path) for path in fixture.MARKET_DATA_PATHS]))
        path = "content/market_bulletins.json"
        for changes in (None, [], [("A", path)], [("D", path)], [("T", path)],
                        [("R100", path)], [("M", path + ".backup")],
                        [("M", path), ("M", "docs/DAILY_EDITORIAL_BRIEF.md")],
                        [("M", path), ("M", "scripts/market_bulletins.py")],
                        [("M", path), ("M", ".github/workflows/check.yml")],
                        [("M", path), ("M", "tests/test_market_outlooks.py")]):
            with self.subTest(changes=changes):
                self.assertFalse(fixture.market_data_only(changes))
                self.assertEqual(fixture.python_test_command(changes),
                                 [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])

    def test_market_suite_keeps_full_build_public_outputs_and_all_market_modules(self):
        command = fixture.python_test_command([("M", "content/market_bulletins.json")])
        self.assertEqual(command[:5], [sys.executable, "-m", "unittest", "-v", "tests.test_site"])
        for module in ("tests.test_site_build_fixture", "tests.test_serials", "tests.test_serial_history",
                       "tests.test_forecast_ledger", "tests.test_search_indexability",
                       "tests.test_sitemap_freshness", "tests.test_serial_discovery"):
            self.assertIn(module, command)
        for path in (fixture.ROOT / "tests").glob("test_market*.py"):
            self.assertIn("tests." + path.stem, command)
        self.assertNotIn("tests.test_cpu_scenes", command)
        self.assertNotIn("tests.test_photo_stories", command)

    def test_only_existing_modified_photo_story_data_can_use_its_own_suite(self):
        path = "content/photo_stories.json"
        self.assertTrue(fixture.photo_story_data_only([("M", path)]))
        self.assertFalse(fixture.market_data_only([("M", path)]))
        for model_path in fixture.STORY_RECORD_PATHS:
            self.assertTrue(fixture.photo_story_data_only([("M", model_path)]))
            self.assertEqual(fixture.python_test_command([("M", model_path)]),
                             fixture.python_test_command([("M", path)]))
        for changes in (None, [], [("A", path)], [("D", path)], [("T", path)],
                        [("R100", path)], [("?", path)], [("M", path + ".backup")],
                        [("M", "./" + path)], [("M", "content/photo_issues.json")],
                        [("M", path), ("M", "content/market_bulletins.json")],
                        [("M", path), ("M", "content/articles.json")],
                        [("M", path), ("M", "scripts/photo_stories.py")],
                        [("M", path), ("M", "tests/test_photo_stories.py")],
                        [("M", path), ("M", ".github/workflows/check.yml")],
                        [("M", path), ("M", "docs/ARCHITECTURE.md")],
                        [("M", path), ("M", "unknown")],
                        [("M", path), ("M", "content/photo_stories/shioba-rio.json")],
                        [("M", "content/photo_stories/nonmember.json")],
                        [("A", "content/photo_stories/shioba-rio.json")],
                        [("M", "content/photo_stories/shioba-rio.json"),
                         ("M", "content/photo_stories/nagino-saya.json")]):
            with self.subTest(changes=changes):
                self.assertFalse(fixture.photo_story_data_only(changes))
                self.assertEqual(fixture.python_test_command(changes),
                                 [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])

    def test_photo_story_suite_keeps_public_build_source_schema_and_seo_contracts(self):
        command = fixture.python_test_command([("M", "content/photo_stories.json")])
        self.assertEqual(command[:5], [sys.executable, "-m", "unittest", "-v", "tests.test_site"])
        for module in ("photo_stories", "photo_story_motion", "photo_model_pages", "photo_issue_pages",
                       "photo_edition_pages", "photo_issues", "photo_editions", "photo_supplied",
                       "photo_profile_portraits", "photo_biographies", "home_visual", "photo_health_report",
                       "image_context", "share_cards", "reader_paths", "internal_links", "route_scripts",
                       "mobile_reading_flow", "site_build_fixture", "serial_discovery", "search_indexability",
                       "sitemap_freshness"):
            self.assertIn("tests.test_" + module, command)
        for module in ("mac_scene_setup", "mac_scene_resources", "cpu_photo_model", "cpu_scenes",
                       "cpu_publication", "scene_recovery", "news_caster_preview", "daily_metrics",
                       "market_figures", "market_forecast_summary", "market_outlooks", "forecast_ledger"):
            self.assertNotIn("tests.test_" + module, command)
        self.assertEqual(len(command[4:]), len(set(command[4:])))

    def test_pr_uses_complete_merge_base_diff_and_push_uses_before_diff(self):
        base = "a" * 40
        for path in ("content/market_bulletins.json", "content/photo_stories.json"):
            result = subprocess.CompletedProcess([], 0, b"M\0" + path.encode() + b"\0")
            for event, suffix in (("pull_request", "...HEAD"), ("push", "..HEAD")):
                with self.subTest(path=path, event=event), \
                     patch.object(fixture.subprocess, "run", return_value=result) as run:
                    changes = fixture.changed_files(base, event)
                    self.assertEqual(changes, [("M", path)])
                    self.assertNotIn("discover", fixture.python_test_command(changes))
                    self.assertIn(base + suffix, run.call_args.args[0])
                    self.assertIn("--no-renames", run.call_args.args[0])

    def test_unknown_refs_events_failed_diffs_and_empty_changes_stay_full(self):
        with patch.object(fixture.subprocess, "run") as run:
            for base, event in (("", "push"), ("0" * 40, "push"), ("main", "push"),
                                ("a" * 40, "workflow_dispatch"), ("HEAD;echo bad", "pull_request")):
                self.assertIsNone(fixture.changed_files(base, event))
            run.assert_not_called()
        for output in (b"", b"M\0content/market_bulletins.json", b"M\0\xff\0"):
            with patch.object(fixture.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output)):
                changes = fixture.changed_files("a" * 40, "push")
                self.assertFalse(fixture.market_data_only(changes))
                self.assertFalse(fixture.photo_story_data_only(changes))
        with patch.object(fixture.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["git"])):
            self.assertIsNone(fixture.changed_files("a" * 40, "pull_request"))

    def test_ci_returns_failures_without_success_fallback_or_second_attempt(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(os.environ, {"GITHUB_OUTPUT": str(Path(folder) / "github-output")}):
            for changes in (None, [("M", "content/market_outlooks.json")],
                            [("M", "content/photo_stories.json")]):
                with patch.object(fixture, "changed_files", return_value=changes), \
                     patch.object(fixture.subprocess, "run", return_value=subprocess.CompletedProcess([], 9)) as run:
                    self.assertEqual(fixture.main(["--base", "a" * 40, "--event", "push"]), 9)
                    run.assert_called_once_with(fixture.python_test_command(changes), cwd=fixture.ROOT,
                                                env={key: value for key, value in os.environ.items()
                                                     if key != "GITHUB_OUTPUT"})


if __name__ == "__main__":
    unittest.main()
