"""Evidence guards for manual mobile capture; these are not browser-render tests."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from scripts import capture_live_mobile
from scripts.capture_live_mobile import SITE, TARGETS, expected_image_urls, live_revision, measurement_errors, read_only_route


class MobileCaptureEvidenceTests(unittest.TestCase):
    def test_runner_setup_installs_only_missing_japanese_fonts_once(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/verify-public-photos.yml").read_text()
        step = workflow.split("      - name: Use runner Chrome with the pinned library and Japanese fonts\n", 1)[1].split("      - name: Capture the two live pages", 1)[0]
        source = textwrap.dedent(step.split("        run: |\n", 1)[1])
        # Execute the real step with harmless command stubs: no package install,
        # browser launch, network request or sudo is performed by this test.
        harness = r"""
        google-chrome() { printf 'CHROME %s\n' "$*"; return "$CHROME_STATUS"; }
        python3() { printf 'PYTHON %s\n' "$*"; }
        command() { [[ "$*" == '-v fc-list' && "$FONTCONFIG" == present ]]; }
        fc-list() { [[ "$FONT_READY" == yes ]]; }
        sudo() { printf 'APT %s\n' "$*"; FONT_READY="$AFTER_INSTALL"; }
        """
        for name, fontconfig, ready, after, chrome, packages, success in (
            ("already available", "present", "yes", "yes", "0", [], True),
            ("missing Japanese", "present", "no", "yes", "0", ["fonts-ipafont-gothic", "fonts-ipafont-mincho"], True),
            ("missing fontconfig", "missing", "no", "yes", "0", ["fontconfig", "fonts-ipafont-gothic", "fonts-ipafont-mincho"], True),
            ("still unavailable", "present", "no", "no", "0", ["fonts-ipafont-gothic", "fonts-ipafont-mincho"], False),
            ("Chrome missing", "present", "yes", "yes", "1", [], False),
        ):
            with self.subTest(case=name):
                result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", textwrap.dedent(harness) + source],
                    env={**os.environ, "FONTCONFIG": fontconfig, "FONT_READY": ready,
                         "AFTER_INSTALL": after, "CHROME_STATUS": chrome},
                    text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode == 0, success, result.stderr)
                calls = result.stdout.splitlines()
                self.assertEqual([call for call in calls if call.startswith("APT ")],
                    ["APT apt-get install --no-install-recommends -y " + " ".join(packages)] if packages else [])
                self.assertEqual([call for call in calls if call.startswith("PYTHON ")],
                    ["PYTHON -m pip install --no-cache-dir playwright==1.65.2"] if chrome == "0" else [])

    def test_installed_chrome_and_japanese_font_evidence_must_be_real_outputs(self):
        with patch.object(capture_live_mobile.shutil, "which", return_value="/usr/bin/google-chrome"), \
                patch.object(capture_live_mobile.subprocess, "run") as run:
            run.side_effect = [SimpleNamespace(stdout="156.0.8000.100\n"),
                               SimpleNamespace(stdout="IPAMincho\nIPAGothic\nIPAGothic\n")]
            evidence = capture_live_mobile.installed_browser_evidence()
            self.assertEqual(evidence, {"chrome_command": "/usr/bin/google-chrome",
                "chrome_cli_version": "156.0.8000.100", "japanese_font_families": ["IPAGothic", "IPAMincho"]})
            self.assertEqual(run.call_args_list[0].args[0], ["/usr/bin/google-chrome", "--product-version"])
            self.assertEqual(run.call_args_list[1].args[0], ["fc-list", ":lang=ja", "family"])
            run.side_effect = [SimpleNamespace(stdout="156.0.8000.100\n"), SimpleNamespace(stdout="")]
            with self.assertRaisesRegex(RuntimeError, "Japanese font support is required"):
                capture_live_mobile.installed_browser_evidence()
        with patch.object(capture_live_mobile.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "preinstalled Google Chrome is required"):
                capture_live_mobile.installed_browser_evidence()

    def test_capture_uses_the_system_channel_and_reports_actual_runner_and_versions(self):
        expected = "a" * 40
        for browser_version, success in (("156.0.8000.100", True), ("157.0.8103.50", False)):
            with self.subTest(browser_version=browser_version), tempfile.TemporaryDirectory() as directory:
                manager = MagicMock()
                browser = manager.__enter__.return_value.chromium.launch.return_value
                browser.version = browser_version
                api = SimpleNamespace(sync_playwright=lambda: manager)
                evidence = {"chrome_command": "/usr/bin/google-chrome", "chrome_cli_version": "156.0.8000.100",
                            "japanese_font_families": ["IPAGothic", "IPAMincho"]}
                with patch.dict(sys.modules, {"playwright.sync_api": api}), \
                        patch.dict(os.environ, {"ImageOS": "ubuntu24", "ImageVersion": "20261111.127.1", "GITHUB_STEP_SUMMARY": ""}), \
                        patch.object(capture_live_mobile, "installed_browser_evidence", return_value=evidence), \
                        patch.object(capture_live_mobile, "version", return_value="1.65.2"), \
                        patch.object(capture_live_mobile, "live_revision", return_value=expected), \
                        patch.object(capture_live_mobile, "capture_page", side_effect=lambda context, target, output: {"name": target[0], "status": "captured"}) as capture, \
                        patch("builtins.print"):
                    status = capture_live_mobile.main(["--expected-sha", expected, "--output", directory])
                report = json.loads((Path(directory) / "report.json").read_text())
                self.assertEqual(status == 0, success, report)
                self.assertEqual(report["runner_image"], {"os": "ubuntu24", "version": "20261111.127.1"})
                self.assertEqual(report["chrome_cli_version"], "156.0.8000.100")
                self.assertEqual(report["browser_version"], browser_version)
                self.assertEqual(report["browser_channel"], "chrome")
                manager.__enter__.return_value.chromium.launch.assert_called_once_with(channel="chrome")
                self.assertEqual(capture.call_count, 2 if success else 0)
                browser.close.assert_called_once_with()

    def test_actual_pr_request_gate_rejects_untrusted_stale_or_unverified_requests(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/verify-public-photos.yml").read_text()
        step = workflow.split("      - name: Authorize only a current-main manual capture request\n", 1)[1].split("      - uses: actions/checkout", 1)[0]
        source = textwrap.dedent(step.split("          script: |\n", 1)[1])
        # Execute the actual trusted workflow script against only read-only API
        # fixtures. The old base.sha deliberately differs from current main.
        cases = [
            ("valid merged PR with old base", {}, "captured"),
            ("same request", {"unchanged": True}, "skipped"),
            ("title-only edit", {"context.payload.changes": {}}, "skipped"),
            ("wrong requester", {"request.requested_by": "someone-else"}, "rejected"),
            ("wrong sender", {"context.payload.sender.login": "someone-else"}, "rejected"),
            ("wrong rerun actor", {"triggering_actor": "someone-else"}, "rejected"),
            ("not a writer", {"permission": "read"}, "rejected"),
            ("not PR author", {"pr.user.login": "someone-else"}, "rejected"),
            ("unmerged PR", {"pr.merged": False}, "rejected"),
            ("fork PR", {"pr.head.repo.full_name": "someone-else/mihassei-news"}, "rejected"),
            ("other base", {"pr.base.ref": "other"}, "rejected"),
            ("body changed after event", {"stale_body": True}, "rejected"),
            ("head changed after event", {"pr.head.sha": "d" * 40}, "rejected"),
            ("stale main", {"main": "d" * 40}, "rejected"),
            ("stale workflow revision", {"context.sha": "d" * 40}, "rejected"),
            ("abbreviated SHA", {"request.expected_sha": "aaaaaaa"}, "rejected"),
            ("arbitrary URL", {"request.pages": ["https://example.com/"]}, "rejected"),
            ("other PR identifier", {"request.request": "pr-43-mobile-390"}, "rejected"),
            ("unknown field", {"request.branch": "other"}, "rejected"),
            ("not main history", {"comparison": "behind"}, "rejected"),
            ("unrelated workflow", {"runs.0.path": ".github/workflows/check.yml"}, "rejected"),
            ("latest run failed", {"runs.0.conclusion": "failure"}, "rejected"),
            ("check job missing", {"jobs": []}, "rejected"),
            ("wrong job SHA", {"jobs.0.head_sha": "d" * 40}, "rejected"),
            ("old job attempt", {"jobs.0.run_attempt": 2}, "rejected"),
            ("revision step failed", {"jobs.0.steps.1.conclusion": "failure"}, "rejected"),
            ("publication handoff present", {"publication": True}, "rejected"),
            ("manual capture", {"context.eventName": "workflow_dispatch",
                "context.payload.inputs": {"capture_mobile": "true"}}, "captured"),
        ]
        harness = r"""
        const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
        const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
        const execute = new AsyncFunction('github', 'context', 'core', input.source);
        (async () => {
          const results = [];
          for (const [name, patches] of input.cases) {
            const sha = 'a'.repeat(40), head = 'b'.repeat(40), merge = 'c'.repeat(40);
            const state = {
              request: {version: 1, request: 'pr-42-mobile-390', requested_by: 'RollNuts', expected_sha: sha, pages: input.pages},
              context: {eventName: 'pull_request_target', sha, ref: 'refs/heads/main', actor: 'RollNuts', runId: 700,
                repo: {owner: 'RollNuts', repo: 'mihassei-news'}, payload: {action: 'edited', number: 42,
                  pull_request: {merged: true, head: {sha: head}, merge_commit_sha: merge},
                  changes: {body: {from: 'Original PR description'}}, sender: {login: 'RollNuts'}}},
              pr: {number: 42, merged: true, state: 'closed', draft: false, user: {login: 'RollNuts'},
                head: {sha: head, repo: {full_name: 'RollNuts/mihassei-news'}}, merge_commit_sha: merge,
                base: {ref: 'main', sha: '1'.repeat(40), repo: {full_name: 'RollNuts/mihassei-news'}}},
              triggering_actor: 'RollNuts', permission: 'write', main: sha, comparison: 'ahead',
              runs: [{id: 9001, path: '.github/workflows/verify-public-photos.yml', event: 'push',
                head_branch: 'main', head_sha: sha, status: 'completed', conclusion: 'success', run_attempt: 1}],
              jobs: [{name: 'check', head_sha: sha, run_id: 9001, run_attempt: 1, status: 'completed', conclusion: 'success',
                steps: ['Bind verification to a full main revision before selecting its files',
                  'Wait for the public revision or a proven docs-only ancestor',
                  'Verify the published revision and live stories without generating traffic',
                  'Verify that Twitterbot can fetch each first-party image card'].map(name => ({name, conclusion: 'success'}))}]
            };
            for (const [path, value] of Object.entries(patches)) {
              const keys = path.split('.'), last = keys.pop();
              keys.reduce((object, key) => object[key], state)[last] = value;
            }
            const body = '<!-- mobile-capture\n' + JSON.stringify(state.request) + '\n-->' +
              (state.publication ? '\n<!-- publication-finalize\n{}\n-->' : '');
            state.context.payload.pull_request.body = body;
            state.pr.body = body + (state.stale_body ? ' updated' : '');
            if (state.unchanged) state.context.payload.changes.body.from = body;
            process.env.GITHUB_TRIGGERING_ACTOR = state.triggering_actor;
            process.env.CHECKED_SHA = sha;
            const calls = [], outputs = {};
            const read = (name, data) => async args => { calls.push({name, args}); return {data: data()}; };
            const github = {rest: {
              repos: {get: read('repo', () => ({full_name: 'RollNuts/mihassei-news', private: false})),
                getCollaboratorPermissionLevel: read('permission', () => ({permission: state.permission})),
                compareCommitsWithBasehead: read('comparison', () => ({status: state.comparison}))},
              pulls: {get: read('pr', () => state.pr)}, git: {getRef: read('main', () => ({object: {sha: state.main}}))},
              actions: {listWorkflowRuns: read('runs', () => ({workflow_runs: state.runs})),
                listJobsForWorkflowRun: read('jobs', () => ({jobs: state.jobs}))}
            }};
            let error;
            try { await execute(github, state.context, {setOutput: (key, value) => outputs[key] = value, info: () => {}}); }
            catch (exception) { error = exception.message; }
            results.push({name, status: error ? 'rejected' : outputs.capture === 'true' ? 'captured' : 'skipped', outputs, error, calls});
          }
          process.stdout.write(JSON.stringify(results));
        })().catch(error => { console.error(error); process.exit(1); });
        """
        result = subprocess.run(["node", "-e", textwrap.dedent(harness)], input=json.dumps({
            "source": source, "cases": cases, "pages": [SITE + target[1] + "?analytics=off" for target in TARGETS],
        }), text=True, capture_output=True, check=True, timeout=20)
        actual = json.loads(result.stdout)
        for case, outcome in zip(cases, actual):
            with self.subTest(case=case[0]):
                self.assertEqual(outcome["status"], case[2], outcome)
        self.assertEqual(actual[0]["outputs"], {"capture": "true", "sha": "a" * 40,
            "request": "pr-42-mobile-390", "verified_run": "9001"})
        run_call = next(call for call in actual[0]["calls"] if call["name"] == "runs")
        self.assertEqual(run_call["args"]["per_page"], 1)
        self.assertNotIn("status", run_call["args"])  # Never search past a failed latest run for an older success.

    def test_unexpected_write_requests_are_aborted_before_delivery(self):
        for method in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"):
            route = Mock(request=SimpleNamespace(method=method, url="https://veripsa.com/api/engagement"))
            blocked = []
            read_only_route(route, blocked)
            if method in ("GET", "HEAD"):
                route.continue_.assert_called_once_with()
                route.abort.assert_not_called()
                self.assertEqual(blocked, [])
            else:
                route.continue_.assert_not_called()
                route.abort.assert_called_once_with("blockedbyclient")
                self.assertEqual(blocked[0]["method"], method)

    def test_screenshots_cannot_be_bound_to_a_different_or_redirected_revision(self):
        expected = "a" * 40
        for status, actual, valid in ((200, expected, True), (200, "b" * 40, False), (302, expected, False)):
            request = Mock()
            request.get.return_value = SimpleNamespace(status=status, text=lambda: actual)
            if valid:
                self.assertEqual(live_revision(request, expected), expected)
            else:
                with self.assertRaisesRegex(RuntimeError, "Exact public revision required"):
                    live_revision(request, expected)
            self.assertEqual(request.get.call_args.kwargs["max_redirects"], 0)

    def test_configured_width_cannot_hide_actual_overflow_or_missing_photographs(self):
        expected = [SITE + "/generated/example.jpg?v=123456789abc"]
        image = {"src": expected[0], "complete": True, "natural_width": 1200, "natural_height": 675}
        valid = {"inner_width": 390, "client_width": 390, "visual_width": 390,
                 "scroll_width": 390, "title": "記事", "images": [image], "required_images": [image]}
        self.assertEqual(measurement_errors(valid, expected), [])
        for changes, message in (({"inner_width": 980}, "390 CSS pixels"),
                                 ({"visual_width": 980}, "390 CSS pixels"),
                                 ({"scroll_width": 406}, "horizontal overflow"),
                                 ({"required_images": []}, "main photographs")):
            with self.subTest(changes=changes):
                self.assertTrue(any(message in error for error in measurement_errors({**valid, **changes}, expected)))
        broken = copy.deepcopy(valid)
        broken["images"][0]["natural_width"] = 0
        self.assertTrue(any("did not load" in error for error in measurement_errors(broken, expected)))

    def test_loaded_svg_wrong_jpeg_and_old_version_cannot_pass_as_the_expected_photograph(self):
        expected = [SITE + "/generated/example.jpg?v=123456789abc"]
        for src in (SITE + "/images/example.svg", SITE + "/generated/other.jpg?v=123456789abc",
                    SITE + "/generated/example.jpg?v=ffffffffffff", "https://other.example/example.jpg"):
            image = {"src": src, "complete": True, "natural_width": 1200, "natural_height": 675}
            measured = {"inner_width": 390, "client_width": 390, "visual_width": 390,
                        "scroll_width": 390, "title": "記事", "images": [image], "required_images": [image]}
            with self.subTest(src=src):
                self.assertTrue(any("identity differs" in error for error in measurement_errors(measured, expected)))

    def test_fixed_targets_use_actual_checkout_jpeg_hashes_in_display_order(self):
        root = Path(__file__).resolve().parents[1]
        article = expected_image_urls(TARGETS[0])
        digest = hashlib.sha256((root / "assets/generated/old-coat-recut-as-strangers-jacket.jpg").read_bytes()).hexdigest()
        self.assertEqual(article, [SITE + "/generated/old-coat-recut-as-strangers-jacket.jpg?v=" + digest[:12]])
        photo = expected_image_urls(TARGETS[1])
        self.assertEqual(len(photo), 10)
        for url, cut in zip(photo, ["05", "01", "02", "03", "04", "06", "07", "08", "09", "10"]):
            digest = hashlib.sha256((root / ("assets/generated/photo-issue-november-rooftop-pool-sumire-011-" + cut + ".jpg")).read_bytes()).hexdigest()
            self.assertEqual(url, SITE + "/photo/weekly/november-rooftop-pool-sumire-011/" + cut + ".jpg?v=" + digest[:12])
        with self.assertRaisesRegex(ValueError, "fixed capture targets"):
            expected_image_urls(("article", "/arbitrary/", "img", 1))


if __name__ == "__main__":
    unittest.main()
