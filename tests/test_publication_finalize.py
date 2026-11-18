"""Exact-head finalization contracts; no live API, dispatch, CI or site build."""
import copy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from types import SimpleNamespace
from unittest.mock import patch

from scripts import publication_finalize as f

H, B, M = "a" * 40, "b" * 40, "c" * 40


def handoff(kind="news"):
    obj = {"version": 1, "kind": kind, "approved_head": H, "authorized_by": "RollNuts",
           "author_run": "author-run-123", "reviewer_run": "reviewer-run-456",
           "first_read": "passed", "merge_after_ci": True}
    if kind in {"news", "market"}:
        obj["article_sha256"] = {"rice": "d" * 64}
    return obj


def body(obj):
    return "Existing editorial handoff\n<!-- publication-finalize\n" + json.dumps(obj) + "\n-->"


class API:
    def __init__(self):
        self.pr = {"number": 819, "body": body(handoff()), "state": "open", "draft": False,
                   "merged": False, "mergeable": True, "mergeable_state": "clean",
                   "user": {"login": "RollNuts"},
                   "head": {"sha": H, "repo": {"full_name": f.REPO}},
                   "base": {"ref": "main", "sha": B, "repo": {"full_name": f.REPO}}}
        self.run = {"path": f.WORKFLOW, "event": "pull_request", "status": "completed",
                    "conclusion": "success", "head_sha": H, "head_repository": {"full_name": f.REPO},
                    "pull_requests": [{"number": 819, "head": {"sha": H}, "base": {"sha": B, "ref": "main"}}]}
        self.files = [{"filename": "content/articles.json", "status": "modified"}]
        self.jobs = [{"name": "test", "conclusion": "success"}]
        self.permission = "write"
        self.main = B
        self.calls = []
        self.writes = []
        self.reads = 0
        self.race = None
        self.fail = None
        self.run_inventory = None
        self.extra_runs = {}
        self.workflow_state = "active"
        self.transitions = {}

    def get(self, path):
        self.calls.append(("GET", path))
        if self.fail == path:
            raise OSError("API unavailable")
        if path == "":
            return {"full_name": f.REPO, "private": False, "visibility": "public"}
        if path == "/pulls/819":
            self.reads += 1
            if self.reads == 2 and self.race:
                self.race(self)
            if self.reads in self.transitions:
                self.transitions[self.reads](self)
            return copy.deepcopy(self.pr)
        if path.startswith("/collaborators/"):
            return {"permission": self.permission}
        if path == "/actions/workflows/free-cpu-scenes.yml":
            return {"path": ".github/workflows/free-cpu-scenes.yml", "state": self.workflow_state}
        if path == "/actions/runs/123":
            return copy.deepcopy(self.run)
        if path.startswith("/actions/workflows/check.yml/runs?"):
            return {"workflow_runs": copy.deepcopy(self.run_inventory if self.run_inventory is not None
                                                   else [{**self.run, "id": 123}])}
        if path.startswith("/actions/runs/") and path.rsplit("/", 1)[-1].isdigit():
            return copy.deepcopy(self.extra_runs[int(path.rsplit("/", 1)[-1])])
        if path == "/actions/runs/123/jobs?per_page=100":
            return {"total_count": len(self.jobs), "jobs": copy.deepcopy(self.jobs)}
        if path == "/git/ref/heads/main":
            return {"object": {"sha": self.main}}
        if path == "/compare/" + M + "...main":
            return {"status": "identical"}
        raise AssertionError(path)

    def pages(self, path):
        self.calls.append(("GET", path))
        if path.endswith("/files"):
            return copy.deepcopy(self.files)
        raise AssertionError(path)

    def request(self, method, path, data):
        self.calls.append((method, path))
        if self.fail == path:
            raise OSError("API unavailable")
        self.writes.append((method, path, data))
        if method == "PUT":
            self.pr.update(state="closed", merged=True, merge_commit_sha=M)
            return {"merged": True, "sha": M}
        return None


class FinalizationTests(unittest.TestCase):
    def setUp(self):
        sleep = patch.object(f.time, "sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def go(self, api, **kwargs):
        def validate(api, pr, run, root):
            api.calls.append(("LOCAL", "fresh merged source/history checks"))
            return B
        def owner(api, number, head):
            api.calls.append(("GET", "fresh owner check"))
            return {"status": "clear"}
        return f.finalize(api, 819, H, None if kwargs.get("body_edit") else 123, validate=kwargs.get("validate", validate),
                          owner=kwargs.get("owner", owner))

    def test_success_merges_only_reviewed_head_then_dispatches_exact_returned_sha(self):
        api = API()
        self.assertEqual(self.go(api), {"status": "verification_dispatched", "merge_sha": M})
        self.assertEqual(api.writes, [
            ("PUT", "/pulls/819/merge", {"sha": H, "merge_method": "merge"}),
            ("POST", f.VERIFY, {"ref": "main", "inputs": {"expected_sha": M}})])
        self.assertLess(api.calls.index(("LOCAL", "fresh merged source/history checks")),
                        api.calls.index(("GET", "fresh owner check")))
        self.assertLess(api.calls.index(("GET", "fresh owner check")), api.calls.index(("PUT", "/pulls/819/merge")))

    def test_missing_optin_does_not_mutate_or_validate(self):
        api = API(); api.pr["body"] = "Initial read pending"
        self.assertEqual(self.go(api)["status"], "not_armed")
        self.assertEqual(api.writes, [])
        self.assertFalse(any(call[0] == "LOCAL" for call in api.calls))

    def test_body_edit_after_ci_finalizes_without_a_gpt_workflow_dispatch(self):
        api = API()
        self.assertEqual(self.go(api, body_edit=True)["status"], "verification_dispatched")
        self.assertTrue(any(path.startswith("/actions/workflows/check.yml/runs?") for _, path in api.calls))
        self.assertEqual(api.writes[-1][2]["inputs"], {"expected_sha": M})

    def test_body_edit_before_ci_or_without_trusted_optin_does_not_mutate(self):
        for inventory in [[], [{**API().run, "id": 123}]]:
            api = API(); api.run_inventory = inventory; api.run["status"] = "in_progress"
            self.assertEqual(self.go(api, body_edit=True)["status"], "ci_pending")
            self.assertEqual(api.writes, [])
        for trust in ["absent", "untrusted"]:
            api = API()
            if trust == "absent": api.pr["body"] = "First read pending"
            else: api.permission = "read"
            if trust == "absent": self.assertEqual(self.go(api, body_edit=True)["status"], "not_armed")
            else:
                with self.assertRaises(ValueError): self.go(api, body_edit=True)
            self.assertFalse(any(path.startswith("/actions/") for _, path in api.calls))
            self.assertEqual(api.writes, [])

    def test_body_edit_does_not_choose_an_older_green_run_over_latest_failed_ci(self):
        api = API()
        failed = {**api.run, "id": 124, "conclusion": "failure"}
        api.run_inventory = [{**api.run, "id": 123}, failed]
        api.extra_runs[124] = failed
        with self.assertRaises(ValueError): self.go(api, body_edit=True)
        self.assertEqual(api.writes, [])

    def test_invalid_or_stale_handoff_never_mutates(self):
        for mutate in [lambda o: o.update(approved_head=M), lambda o: o.update(merge_after_ci=False),
                       lambda o: o.update(first_read="pending"), lambda o: o.update(reviewer_run=o["author_run"]),
                       lambda o: o.pop("article_sha256")]:
            with self.subTest(mutate=mutate):
                api = API(); obj = handoff(); mutate(obj); api.pr["body"] = body(obj)
                with self.assertRaises((ValueError, TypeError)):
                    self.go(api)
                self.assertEqual(api.writes, [])

    def test_duplicate_marker_and_infra_paths_are_not_an_optin(self):
        for files in [[{"filename": ".github/workflows/check.yml", "status": "modified"}],
                      [{"filename": "scripts/build.py", "status": "modified"}],
                      [{"filename": "content/articles.json", "status": "removed"}]]:
            api = API(); api.files = files
            with self.assertRaises(ValueError): self.go(api)
            self.assertEqual(api.writes, [])
        api = API(); api.pr["body"] *= 2
        with self.assertRaises(ValueError): self.go(api)
        self.assertEqual(api.writes, [])

    def test_untrusted_fork_wrong_base_draft_unknown_mergeability_fail_closed(self):
        for mutate in [lambda a: setattr(a, "permission", "read"),
                       lambda a: a.pr["head"]["repo"].update(full_name="outsider/fork"),
                       lambda a: a.pr["base"].update(ref="develop"),
                       lambda a: a.pr.update(draft=True), lambda a: a.pr.update(mergeable=None)]:
            api = API(); mutate(api)
            with self.assertRaises(ValueError): self.go(api)
            self.assertEqual(api.writes, [])

    def test_blocking_snapshots_identify_safe_next_operation_without_api_triage(self):
        cases = [(dict(draft=True), "ready"),
                 (dict(mergeable=False, mergeable_state="dirty"), "conflicts"),
                 (dict(mergeable=None, mergeable_state="unknown"), "mergeability")]
        for state, operation in cases:
            for stage in ("preflight", "premerge"):
                with self.subTest(state=state, stage=stage):
                    api = API()
                    if stage == "preflight": api.pr.update(state)
                    else: api.race = lambda a: a.pr.update(state)
                    with self.assertRaises(f.PublicationBlocked) as blocked:
                        self.go(api)
                    result = blocked.exception.result
                    self.assertEqual((result["pr"], result["expected_head"], result["current_head"]), (819, H, H))
                    self.assertEqual(result["stage"], stage)
                    self.assertEqual(result["blocking_snapshot"], {
                        k: api.pr[k] for k in ("state", "draft", "mergeable", "mergeable_state")})
                    self.assertIn(operation, result["next_operation"])
                    self.assertEqual(api.writes, [])

    def test_cli_reports_draft_block_in_json_and_workflow_summary(self):
        api = API(); api.pr["draft"] = True
        with tempfile.TemporaryDirectory() as folder:
            event, summary = Path(folder) / "event.json", Path(folder) / "summary.md"
            event.write_text(json.dumps({"number": 819, "pull_request": api.pr}))
            with patch.object(f, "GitHub", return_value=api), patch("sys.stdout", new_callable=io.StringIO) as output, \
                    patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}):
                self.assertEqual(f.main(["--event", str(event)]), 1)
            result = json.loads(output.getvalue())
            self.assertEqual(result["blocking_snapshot"]["draft"], True)
            self.assertEqual(result["expected_head"], H)
            self.assertIn("ready", result["next_operation"])
            self.assertIn(json.dumps(result, indent=2), summary.read_text())
            self.assertEqual(api.writes, [])

    def test_failed_wrong_head_or_unverified_ci_cannot_merge(self):
        for mutate in [lambda a: a.run.update(conclusion="failure"),
                       lambda a: a.run.update(head_sha=M), lambda a: a.run.update(event="push"),
                       lambda a: a.run.update(path=".github/workflows/other.yml"),
                       lambda a: a.run.update(pull_requests=[]), lambda a: setattr(a, "jobs", [])]:
            api = API(); mutate(api)
            with self.assertRaises(ValueError): self.go(api)
            self.assertEqual(api.writes, [])

    def test_optional_finalizer_failure_can_leave_unstable_after_primary_ci_passes(self):
        # PR828 had only its own optional finalize failure; required Validate
        # newspaper passed. Test both initial and final mergeability snapshots.
        for phase in ("initial", "final"):
            with self.subTest(phase=phase):
                api = API(); obj = handoff("market"); obj["article_sha256"] = {}
                api.pr["body"] = body(obj)
                api.files = [{"filename": "content/market_bulletins.json", "status": "modified"}]
                if phase == "initial": api.pr["mergeable_state"] = "unstable"
                else: api.race = lambda a: a.pr.update(mergeable_state="unstable")
                self.assertEqual(self.go(api)["status"], "verification_dispatched")
                self.assertLess(api.calls.index(("GET", "/actions/runs/123")),
                                api.calls.index(("PUT", "/pulls/819/merge")))
                self.assertLess(api.calls.index(("LOCAL", "fresh merged source/history checks")),
                                api.calls.index(("PUT", "/pulls/819/merge")))

    def test_unstable_with_pending_primary_ci_returns_pending_without_writes(self):
        api = API(); api.pr["mergeable_state"] = "unstable"
        api.run.update(status="in_progress", conclusion=None)
        self.assertEqual(self.go(api)["status"], "ci_pending")
        self.assertEqual(api.writes, [])
        self.assertFalse(any(call[0] == "LOCAL" for call in api.calls))

    def test_unstable_never_bypasses_failed_primary_ci(self):
        api = API(); api.pr["mergeable_state"] = "unstable"
        api.run["conclusion"] = "failure"
        with self.assertRaisesRegex(ValueError, "CI is not successful"):
            self.go(api)
        self.assertEqual(api.writes, [])
        self.assertFalse(any(call[0] == "LOCAL" for call in api.calls))

    def test_unknown_conflicting_or_blocked_mergeability_still_rejects_at_both_gates(self):
        for mergeable, state in ((None, "unknown"), (True, "unknown"), (False, "dirty"),
                                 (True, "dirty"), (True, "blocked"), (True, None),
                                 (None, "unstable"), (False, "unstable"), (True, "behind")):
            for phase in ("initial", "final"):
                with self.subTest(mergeable=mergeable, state=state, phase=phase):
                    api = API()
                    if phase == "initial": api.pr.update(mergeable=mergeable, mergeable_state=state)
                    else: api.race = lambda a: a.pr.update(mergeable=mergeable, mergeable_state=state)
                    with self.assertRaises(ValueError): self.go(api)
                    self.assertEqual(api.writes, [])

    def test_transient_unknown_is_reread_at_both_gates_without_skipping_validation(self):
        api = API(); api.pr.update(mergeable=None, mergeable_state="unknown")
        api.transitions = {
            2: lambda a: a.pr.update(mergeable=True, mergeable_state="clean"),
            3: lambda a: a.pr.update(mergeable=None, mergeable_state="unknown"),
            4: lambda a: a.pr.update(mergeable=True, mergeable_state="clean"),
        }
        self.assertEqual(self.go(api)["status"], "verification_dispatched")
        self.assertEqual(api.reads, 4)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [2, 2])
        self.assertLess(api.calls.index(("LOCAL", "fresh merged source/history checks")),
                        api.calls.index(("PUT", "/pulls/819/merge")))

    def test_persistent_unknown_has_bounded_retries_and_never_merges(self):
        for stage in ("preflight", "premerge"):
            with self.subTest(stage=stage):
                self.sleep.reset_mock(); api = API()
                if stage == "preflight": api.pr.update(mergeable=None, mergeable_state="unknown")
                else: api.race = lambda a: a.pr.update(mergeable=None, mergeable_state="unknown")
                with self.assertRaises(f.PublicationBlocked) as blocked: self.go(api)
                self.assertEqual(blocked.exception.result["stage"], stage)
                self.assertEqual(api.reads, 3 if stage == "preflight" else 4)
                self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [2, 5])
                self.assertEqual(api.writes, [])

    def test_retry_stops_on_changed_head_review_conflict_or_api_error(self):
        changes = [lambda a: a.pr["head"].update(sha=M),
                   lambda a: a.pr.update(body=body({**handoff(), "reviewer_run": "other-review-789"})),
                   lambda a: a.pr.update(mergeable=False, mergeable_state="dirty"),
                   lambda a: a.pr.update(draft=True),
                   lambda a: a.pr.update(state="closed")]
        for change in changes:
            with self.subTest(change=change):
                self.sleep.reset_mock(); api = API()
                api.pr.update(mergeable=None, mergeable_state="unknown"); api.transitions[2] = change
                with self.assertRaises(ValueError): self.go(api)
                self.assertEqual(self.sleep.call_count, 1)
                self.assertEqual(api.reads, 2)
                self.assertEqual(api.writes, [])
        api = API(); api.pr.update(mergeable=None, mergeable_state="unknown")
        api.transitions[2] = lambda a: (_ for _ in ()).throw(OSError("API unavailable"))
        self.sleep.reset_mock()
        with self.assertRaises(OSError): self.go(api)
        self.assertEqual(self.sleep.call_count, 1)
        self.assertEqual(api.reads, 2)
        self.assertEqual(api.writes, [])

    def test_retry_does_not_admit_main_or_ci_changes_while_waiting(self):
        for change in [lambda a: setattr(a, "main", M),
                       lambda a: a.run.update(status="in_progress", conclusion=None)]:
            with self.subTest(change=change):
                api = API(); api.race = lambda a: a.pr.update(mergeable=None, mergeable_state="unknown")
                def clear_and_change(a):
                    a.pr.update(mergeable=True, mergeable_state="clean"); change(a)
                api.transitions[3] = clear_and_change
                with self.assertRaises(ValueError): self.go(api)
                self.assertEqual(api.writes, [])

    def test_head_main_approval_races_or_owner_conflict_stop_before_merge(self):
        for mutate in [lambda a: a.pr["head"].update(sha=M), lambda a: setattr(a, "main", M),
                       lambda a: a.pr["base"].update(ref="other-branch"),
                       lambda a: a.run.update(status="in_progress"),
                       lambda a: a.pr.update(body=body({**handoff(), "reviewer_run": "other-review-789"}))]:
            api = API(); api.race = mutate
            with self.assertRaises(ValueError): self.go(api)
            self.assertEqual(api.writes, [])
        api = API()
        with self.assertRaises(ValueError): self.go(api, owner=lambda *args: {"status": "blocked"})
        self.assertEqual(api.writes, [])

    def test_compatible_new_main_can_merge_while_pr_base_metadata_remains_old_or_refreshes(self):
        current = "e" * 40
        for refresh_metadata in (False, True):
            with self.subTest(refresh_metadata=refresh_metadata):
                api = API(); api.main = current
                if refresh_metadata:
                    api.race = lambda a: a.pr["base"].update(sha=current)
                def validated(api, pr, run, root):
                    self.assertEqual(pr["base"]["sha"], B)
                    self.assertEqual(run["pull_requests"][0]["base"]["sha"], B)
                    self.assertEqual(api.get("/git/ref/heads/main")["object"]["sha"], current)
                    # Existing real-Git tests cover fresh merged-tree validation;
                    # here its result deliberately differs from PR/CI base metadata.
                    return current
                self.assertEqual(self.go(api, validate=validated)["status"], "verification_dispatched")
                self.assertEqual(api.writes[0], ("PUT", "/pulls/819/merge", {"sha": H, "merge_method": "merge"}))
                self.assertEqual(api.pr["base"]["sha"], current if refresh_metadata else B)

    def test_true_main_advance_after_snapshot_still_blocks_before_put(self):
        snapshot, advanced = "e" * 40, "f" * 40
        for timing in ("during_validation", "before_put"):
            with self.subTest(timing=timing):
                api = API(); api.main = snapshot
                if timing == "before_put":
                    api.race = lambda a: setattr(a, "main", advanced)
                def validated(api, pr, run, root):
                    self.assertEqual(api.main, snapshot)
                    if timing == "during_validation": api.main = advanced
                    return snapshot
                with self.assertRaisesRegex(ValueError, "PR or main changed"):
                    self.go(api, validate=validated)
                self.assertIn(("GET", "/git/ref/heads/main"), api.calls)
                self.assertEqual(api.writes, [])

    def test_api_or_source_guard_failure_is_not_reinterpreted_as_success(self):
        for endpoint in ["/actions/runs/123", "/pulls/819/merge"]:
            api = API(); api.fail = endpoint
            with self.assertRaises(OSError): self.go(api)
            self.assertEqual(api.writes, [])
        api = API()
        with self.assertRaises(ValueError):
            self.go(api, validate=lambda *args: (_ for _ in ()).throw(ValueError("history changed")))
        self.assertEqual(api.writes, [])

    def test_image_stop_or_guard_api_failure_blocks_images_but_not_body_only_news(self):
        for kind, path in [("cast_story", "content/photo_stories.json"),
                           ("photo_issue", "content/photo_issues.json"),
                           ("news", "assets/generated/supermarkets-buy-back-empty-pet-bottles.jpg")]:
            for state in ("disabled_manually", "unknown", "api_error"):
                api = API(); api.pr["body"] = body(handoff(kind))
                api.files = [{"filename": path, "status": "modified"}]
                api.workflow_state = state
                if state == "api_error": api.fail = "/actions/workflows/free-cpu-scenes.yml"
                with self.assertRaises((ValueError, OSError)): self.go(api)
                self.assertEqual(api.writes, [])
                self.assertFalse(any(call[0] == "LOCAL" for call in api.calls))
        api = API(); api.workflow_state = "disabled_manually"
        self.assertEqual(self.go(api)["status"], "verification_dispatched")
        self.assertFalse(any(path == "/actions/workflows/free-cpu-scenes.yml" for _, path in api.calls))

    def test_image_guard_is_freshly_rechecked_immediately_before_merge(self):
        api = API(); api.pr["body"] = body(handoff("cast_story"))
        api.files = [{"filename": "content/photo_stories.json", "status": "modified"}]
        def owner(*args):
            api.workflow_state = "disabled_manually"
            return {"status": "clear"}
        with self.assertRaisesRegex(ValueError, "stopped or unverified"):
            self.go(api, owner=owner)
        self.assertEqual(api.writes, [])
        self.assertEqual(sum(path == "/actions/workflows/free-cpu-scenes.yml" for _, path in api.calls), 2)

    def test_merged_retry_only_dispatches_and_dispatch_failure_keeps_recovery_possible(self):
        api = API(); api.fail = f.VERIFY
        with self.assertRaises(OSError): self.go(api)
        self.assertTrue(api.pr["merged"])
        api.fail = None
        self.go(api)
        self.assertEqual(sum(method == "PUT" for method, _, _ in api.writes), 1)
        self.assertEqual(api.writes[-1][2]["inputs"], {"expected_sha": M})

    def test_merged_body_edit_can_only_redispatch_the_verified_merge_revision(self):
        api = API(); api.pr.update(state="closed", merged=True, merge_commit_sha=M)
        self.assertEqual(self.go(api, body_edit=True), {"status": "verification_dispatched", "merge_sha": M})
        self.assertEqual(api.writes, [("POST", f.VERIFY, {"ref": "main", "inputs": {"expected_sha": M}})])
        self.assertFalse(any(method == "LOCAL" or path.startswith("/actions/workflows/check.yml/runs?")
                             for method, path in api.calls))
        for invalid in ("head", "fork", "approval", "writer", "ancestry"):
            with self.subTest(invalid=invalid):
                api = API(); api.pr.update(state="closed", merged=True, merge_commit_sha=M)
                if invalid == "head": api.pr["head"]["sha"] = B
                elif invalid == "fork": api.pr["head"]["repo"]["full_name"] = "outsider/fork"
                elif invalid == "approval": api.pr["body"] = body({**handoff(), "first_read": "pending"})
                elif invalid == "writer": api.permission = "read"
                else: api.get = lambda path, original=api.get: {"status": "diverged"} if path.startswith("/compare/") else original(path)
                with self.assertRaises(ValueError): self.go(api, body_edit=True)
                self.assertEqual(api.writes, [])

    def test_photo_editor_self_read_is_allowed_but_cannot_relabel_news(self):
        for kind, path in [("cast_story", "content/photo_stories.json"), ("photo_issue", "content/photo_issues.json")]:
            api = API(); obj = handoff(kind); obj["reviewer_run"] = obj["author_run"]
            api.pr["body"] = body(obj); api.files[0]["filename"] = path
            self.assertEqual(self.go(api)["status"], "verification_dispatched")
            api = API(); api.pr["body"] = body(obj)
            with self.assertRaises(ValueError): self.go(api)
            self.assertEqual(api.writes, [])

    def test_typical_news_with_required_editorial_docs_and_real_jpeg_is_allowed(self):
        api = API()
        api.files += [{"filename": path, "status": "modified"} for path in (
            "docs/DAILY_EDITORIAL_BRIEF.md", "docs/EDITORIAL_IDEAS.md",
            "assets/generated/supermarkets-buy-back-empty-pet-bottles.jpg")]
        self.assertEqual(self.go(api)["status"], "verification_dispatched")
        for path in ("docs/OPERATIONS.md", "docs/EDITORIAL_LOOP.md", "README.md"):
            api = API(); api.files.append({"filename": path, "status": "modified"})
            with self.assertRaises(ValueError): self.go(api)
            self.assertEqual(api.writes, [])

    def test_real_photo_issue_plan_json_paths_are_allowed_only_for_photo_issue(self):
        paths = ["content/photo_issues.json", "content/photo_editions.json",
                 "maintenance/photo-issue-plans/november-warm-water-and-riverside-yuki-009/handoff.json",
                 "maintenance/photo-issue-plans/november-warm-water-and-riverside-yuki-009/binary-map.json",
                 "assets/generated/photo-issue-november-warm-water-and-riverside-yuki-009-01.jpg",
                 "maintenance/photo-issues/november-warm-water-and-riverside-yuki-009/01/receipt.json"]
        api = API(); api.pr["body"] = body(handoff("photo_issue"))
        api.files = [{"filename": path, "status": "added"} for path in paths]
        self.assertEqual(self.go(api)["status"], "verification_dispatched")
        self.assertFalse(f.allowed_for("news", paths[2]))
        self.assertFalse(f.allowed(paths[2].replace("handoff.json", "run.py")))
        self.assertFalse(f.allowed(paths[2].replace("handoff.json", "../handoff.json")))

    def test_actual_market_sources_are_allowed_and_figures_are_build_output(self):
        # PR816 changed only market_bulletins.json. Current market_figures.py
        # renders SVGs from JSON under serials/.../figures/, not assets/market-*.
        api = API(); obj = handoff("market"); obj["article_sha256"] = {}
        api.pr["body"] = body(obj)
        api.files = [{"filename": path, "status": "modified"} for path in
                     ("content/market_bulletins.json", "content/market_figures.json")]
        self.assertEqual(self.go(api)["status"], "verification_dispatched")
        from scripts.market_figures import _figure_path
        output = _figure_path({"slug": "market"}, {"slug": "gs-yuasa-margin-test-02"},
                              {"id": "01-segment-sales"})
        self.assertEqual(output, "serials/market/figures/gs-yuasa-margin-test-02/01-segment-sales.svg")
        self.assertFalse(f.allowed(output))
        self.assertFalse(f.allowed("assets/market-figures/01-segment-sales.svg"))

    def test_canonical_chatgpt_identifier_syntax_is_supported_without_accepting_urls(self):
        # Deliberately synthetic syntax fixtures, never claimed as real runs.
        obj = handoff()
        obj["author_run"] = "chatgpt:00000000-0000-4000-8000-000000000001"
        obj["reviewer_run"] = "chatgpt:00000000-0000-4000-8000-000000000002"
        self.assertEqual(f.approval({"body": body(obj)})["reviewer_run"], obj["reviewer_run"])
        obj["reviewer_run"] = "https://chatgpt.com/c/00000000-0000-4000-8000-000000000002"
        with self.assertRaises(ValueError): f.approval({"body": body(obj)})

    def test_cli_handoff_only_prints_existing_body_marker(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(f, "GitHub") as network:
            self.assertEqual(f.main(["--print-handoff", "--kind", "cast_story", "--head", H,
                                     "--authorized-by", "RollNuts", "--author-run", "editor-run-123",
                                     "--reviewer-run", "editor-run-123"]), 0)
            self.assertEqual(f.approval({"body": output.getvalue()})["approved_head"], H)
            network.assert_not_called()


class AdapterTests(unittest.TestCase):
    def test_actual_workflow_admits_only_trusted_ready_or_merged_edit_handoffs(self):
        workflow = (f.ROOT / ".github/workflows/verify-public-photos.yml").read_text().split("\n  finalize:\n", 1)[1]
        expression = re.search(r"(?m)^    if: \$\{\{ (.+) \}\}$", workflow).group(1)
        expression = re.sub(r"\bfalse\b", "False", re.sub(r"\btrue\b", "True", expression))
        expression = expression.replace("&&", " and ").replace("||", " or ")
        def allowed(action="edited", **changes):
            pr = copy.deepcopy(API().pr)
            pr["author_association"] = "OWNER"
            pr.update(changes)
            event = {"repository": {"private": False}, "action": action, "pull_request": pr}
            github = {"repository": f.REPO, "ref": "refs/heads/main", "event_name": "pull_request_target", "event": event}
            namespace = json.loads(json.dumps(github), object_hook=lambda o: SimpleNamespace(**o))
            return eval(expression, {"__builtins__": {}, "contains": lambda a, b: b in a, "fromJSON": json.loads},
                        {"github": namespace, "inputs": SimpleNamespace(finalize_pr="")})
        self.assertTrue(allowed())
        self.assertTrue(allowed(state="closed", merged=True))
        self.assertFalse(allowed(action="ready_for_review", state="closed", merged=True))
        self.assertFalse(allowed(state="closed", merged=False))
        self.assertFalse(allowed(draft=True))
        self.assertFalse(allowed(state="closed", merged=True, author_association="CONTRIBUTOR"))
        self.assertFalse(allowed(state="closed", merged=True, body="No opt-in"))
        self.assertFalse(allowed(state="closed", merged=True, head={"repo": {"full_name": "outsider/fork"}}))
        self.assertIn("ref: refs/heads/main", workflow)
        self.assertNotIn("actions/download-artifact", workflow)

    def test_real_class_reads_exact_repository_root_via_its_no_redirect_opener(self):
        client = f.GitHub()
        redirects = [h for h in client.opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
        self.assertEqual(len(redirects), 1)
        self.assertIsNone(redirects[0].redirect_request(None, None, 302, "Found", {}, "https://evil.example/"))
        responses = []
        def open_request(request, timeout):
            responses.append((request, timeout))
            return io.BytesIO(json.dumps({"full_name": f.REPO, "private": False, "visibility": "public"}).encode())
        client.opener = SimpleNamespace(open=open_request)
        with patch.dict(os.environ, {"GH_TOKEN": "synthetic-test-token"}):
            self.assertEqual(client.get("")["full_name"], f.REPO)
        request, timeout = responses[0]
        self.assertEqual(request.full_url, "https://api.github.com/repos/" + f.REPO)
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-test-token")
        self.assertEqual(timeout, 20)
        for path in ("https://evil.example/", "//evil.example/"):
            with self.assertRaises(ValueError): client.get(path)
        self.assertEqual(len(responses), 1)

    def test_real_mutation_adapter_only_targets_fixed_merge_and_verifier(self):
        client = f.GitHub(); calls = []
        def open_request(request, timeout):
            calls.append((request.get_method(), request.full_url, json.loads(request.data)))
            return io.BytesIO(json.dumps({"merged": True, "sha": M}).encode() if request.get_method() == "PUT" else b"")
        client.opener = SimpleNamespace(open=open_request)
        with patch.dict(os.environ, {"GH_TOKEN": "synthetic-test-token"}):
            self.assertTrue(client.request("PUT", "/pulls/819/merge", {"sha": H})["merged"])
            self.assertIsNone(client.request("POST", f.VERIFY, {"ref": "main", "inputs": {"expected_sha": M}}))
            for method, path in [("PATCH", "/git/refs/heads/main"), ("POST", "https://evil.example/")]:
                with self.assertRaises(ValueError): client.request(method, path, {})
        self.assertEqual(calls, [
            ("PUT", "https://api.github.com/repos/" + f.REPO + "/pulls/819/merge", {"sha": H}),
            ("POST", "https://api.github.com/repos/" + f.REPO + f.VERIFY,
             {"ref": "main", "inputs": {"expected_sha": M}})])

    def test_finalizer_serializes_by_pr_without_sharing_production_concurrency(self):
        workflow = (f.ROOT / ".github/workflows/verify-public-photos.yml").read_text()
        production, finalizer = workflow.split("\n  finalize:\n", 1)
        self.assertIn("'verify-live-production-main'", production)
        self.assertIn("group: publication-finalize-${{ github.event.pull_request.number || github.event.workflow_run.pull_requests[0].number || inputs.finalize_pr || github.run_id }}", finalizer)
        self.assertIn("cancel-in-progress: false", finalizer)


class MergedSourceTests(unittest.TestCase):
    def test_unrelated_data_merges_without_configured_committer_and_guards_receive_no_token(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def git(*args):
                return f.git(root, *args)
            git("init", "-q")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Test")
            (root / "content").mkdir()
            (root / "content/photo_stories.json").write_text("[]\n")
            (root / "content/articles.json").write_text("[]\n")
            git("add", "."); git("commit", "-qm", "base")
            baseline = git("rev-parse", "HEAD")
            git("checkout", "-qb", "editor")
            (root / "content/photo_stories.json").write_text('[{"slug":"new-story"}]\n')
            git("commit", "-qam", "story")
            head = git("rev-parse", "HEAD")
            git("checkout", "-qb", "later-main", baseline)
            (root / "content/articles.json").write_text('[{"slug":"other-news"}]\n')
            git("commit", "-qam", "unrelated main news")
            current = git("rev-parse", "HEAD")
            git("config", "--unset", "user.name"); git("config", "--unset", "user.email")
            api = API(); api.main = current
            obj = handoff("cast_story"); obj["approved_head"] = head
            pr = copy.deepcopy(api.pr); pr["head"]["sha"] = head; pr["body"] = body(obj)
            run = copy.deepcopy(api.run); run["pull_requests"][0]["base"]["sha"] = baseline
            real_git, real_run = f.git, subprocess.run
            seen = []
            def local_git(where, *args):
                return "" if args[0] == "fetch" else real_git(where, *args)
            def execute(command, **kwargs):
                if command[0] == sys.executable:
                    seen.append(command)
                    self.assertNotIn("GH_TOKEN", kwargs["env"])
                    merged = Path(kwargs["cwd"])
                    self.assertIn("other-news", (merged / "content/articles.json").read_text())
                    self.assertIn("new-story", (merged / "content/photo_stories.json").read_text())
                    return subprocess.CompletedProcess(command, 0)
                return real_run(command, **kwargs)
            isolated = {k: v for k, v in os.environ.items() if k not in
                        {"GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"}}
            isolated.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_COUNT="0",
                            GH_TOKEN="must-not-reach-source-checks")
            with patch.object(f, "git", side_effect=local_git), patch.object(f.subprocess, "run", side_effect=execute), patch.dict(os.environ, isolated, clear=True):
                with self.assertRaises(subprocess.CalledProcessError):
                    real_git(root, "config", "--get", "user.name")
                self.assertEqual(f.validate_merge(api, pr, run, root), current)
                with self.assertRaises(subprocess.CalledProcessError):
                    real_git(root, "config", "--get", "user.name")
            self.assertEqual(len(seen), 5)
            self.assertEqual([command[-1] for command in seen[1:4]], [current] * 3)
            self.assertEqual(seen[-1], [sys.executable, "-S", "-c", f.PHOTO_VALIDATION])
            self.assertTrue(all(command[1] == "-S" for command in seen))
            self.assertFalse(any("build.py" in command for command in seen))
            # A runtime-code change on main requires fresh CI rather than using
            # old tests to bless a new data/code combination.
            (root / "scripts").mkdir(); (root / "scripts/runtime.py").write_text("changed = True\n")
            git("config", "user.name", "Test"); git("config", "user.email", "test@example.invalid")
            git("add", "."); git("commit", "-qm", "runtime changed")
            api.main = git("rev-parse", "HEAD")
            with patch.object(f, "git", side_effect=local_git):
                with self.assertRaisesRegex(ValueError, "fresh CI"):
                    f.validate_merge(api, pr, run, root)

    def test_clean_git_merge_of_individually_valid_stories_still_rejects_same_model_day(self):
        from scripts.photo_issues import load_photo_issues
        from scripts.photo_stories import validate_photo_stories
        source_root = Path(__file__).resolve().parents[1]
        catalog = load_photo_issues(source_root)
        existing = json.loads((source_root / "content/photo_stories.json").read_text())
        first = copy.deepcopy(existing["stories"][0])
        spacer = copy.deepcopy(next(s for s in existing["stories"] if s["model_id"] != first["model_id"]))
        first.pop("motion", None); spacer.pop("motion", None)
        original = {"schema_version": 1, "stories": [first, spacer]}
        main_data = copy.deepcopy(original)
        main_data["stories"][0].update(published_at="2026-11-17T00:00:00+09:00", text="今日は水辺で少し休んだ。")
        proposed = copy.deepcopy(original)
        new = copy.deepcopy(first)
        new.update(id="concurrent-same-model-story", published_at="2026-11-17T12:00:00+09:00", text="帰り道で、温かい飲み物を買った。")
        proposed["stories"].append(new)
        # These accepted source identities/bytes are unchanged on both branches;
        # the regression concerns their newly conflicting publication dates.
        published = [issue for issue in catalog["issues"] if issue["state"] == "published"]
        def validate(data):
            return validate_photo_stories(data, catalog=catalog, published=published,
                                          root=source_root, now=datetime(2026, 11, 20, tzinfo=timezone.utc))
        self.assertEqual(len(validate(main_data)), 2)
        self.assertEqual(len(validate(proposed)), 3)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def git(*args): return f.git(root, *args)
            git("init", "-q"); git("config", "user.name", "Test"); git("config", "user.email", "test@example.invalid")
            path = root / "content/photo_stories.json"; path.parent.mkdir()
            path.write_text(json.dumps(original, indent=2, ensure_ascii=False) + "\n")
            git("add", "."); git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            git("checkout", "-qb", "editor")
            path.write_text(json.dumps(proposed, indent=2, ensure_ascii=False) + "\n")
            git("commit", "-qam", "new story")
            head = git("rev-parse", "HEAD")
            git("checkout", "-qb", "later-main", base)
            path.write_text(json.dumps(main_data, indent=2, ensure_ascii=False) + "\n")
            git("commit", "-qam", "concurrent date update")
            git("merge", "--no-commit", "--no-ff", "--no-edit", head)
            with self.assertRaisesRegex(ValueError, "same JST day"):
                validate(json.loads(path.read_text()))


if __name__ == "__main__":
    unittest.main()
