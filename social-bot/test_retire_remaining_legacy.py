import copy
import importlib.util
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("retire_remaining", HERE / "retire_remaining_legacy.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class RemainingLegacyRetirementTests(unittest.TestCase):
    def setUp(self):
        self.request = copy.deepcopy(m.read(HERE / "remaining-legacy-retirement.json"))
        self.inventory = m.read(HERE / "experiments" / "posts.json")
        self.ledger = m.read(HERE / "experiments" / "state.json")
        self.now = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)
        posts = {p["id"]: p for p in self.inventory["experiments"]}
        self.remote = {}
        for target in self.request["targets"]:
            post = posts[target["post_id"]]
            self.remote[target["buffer_id"]] = {
                "id": target["buffer_id"],
                "channelId": m.CHANNEL,
                "text": m.published_text(post),
                "status": "scheduled",
                "dueAt": "2026-11-05T00:00:00Z",
                "sentAt": None,
                "allowedActions": ["movePostToDraft"],
                "assets": [],
            }
        self.events = []
        self.saved = []

    def fetch(self, bid):
        self.events.append(("read", bid))
        return copy.deepcopy(self.remote.get(bid))

    def move(self, bid, text):
        self.events.append(("edit", bid))
        self.assertEqual(text, self.remote[bid]["text"])
        self.remote[bid]["status"] = "draft"
        self.remote[bid]["dueAt"] = None
        return {"id": bid, "status": "draft"}

    def persist(self, request):
        self.events.append(("persist", None))
        self.saved.append(copy.deepcopy(request))

    def perform(self, move=None):
        m.process(
            self.request, self.inventory, self.ledger,
            self.fetch, move or self.move, self.persist, lambda: self.now,
        )

    def test_checked_in_request_matches_exact_remaining_four(self):
        targets = m.validate(self.request, self.inventory, self.ledger)
        self.assertEqual([t["post_id"] for t in targets], list(m.TARGET_IDS))

    def test_scheduled_unchanged_posts_move_to_draft_once(self):
        self.perform()
        self.assertEqual(sum(event[0] == "edit" for event in self.events), 4)
        self.assertFalse(self.request["enabled"])
        self.assertTrue(all(
            self.request["results"][pid]["state"] == "verified_draft"
            for pid in m.TARGET_IDS
        ))
        # Each mutation must have a durable intent snapshot before edit.
        first_edit = next(i for i, event in enumerate(self.events) if event[0] == "edit")
        self.assertIn(("persist", None), self.events[:first_edit])

    def test_sent_and_draft_posts_are_never_mutated(self):
        first, second = self.request["targets"][:2]
        self.remote[first["buffer_id"]].update(status="sent", sentAt="2026-11-04T09:50:00Z")
        self.remote[second["buffer_id"]].update(status="draft", dueAt=None)
        self.perform()
        edited = {bid for action, bid in self.events if action == "edit"}
        self.assertNotIn(first["buffer_id"], edited)
        self.assertNotIn(second["buffer_id"], edited)
        self.assertEqual(self.request["results"][first["post_id"]]["reason"], "already_published")
        self.assertEqual(self.request["results"][second["post_id"]]["reason"], "already_draft")

    def test_changed_text_or_account_fails_closed(self):
        first, second = self.request["targets"][:2]
        self.remote[first["buffer_id"]]["text"] += " changed"
        self.remote[second["buffer_id"]]["channelId"] = "other-channel"
        self.perform()
        edited = {bid for action, bid in self.events if action == "edit"}
        self.assertNotIn(first["buffer_id"], edited)
        self.assertNotIn(second["buffer_id"], edited)
        self.assertEqual(self.request["results"][first["post_id"]]["reason"], "text_changed")
        self.assertEqual(self.request["results"][second["post_id"]]["reason"], "identity_mismatch")

    def test_past_or_imminent_scheduled_post_is_not_raced(self):
        first = self.request["targets"][0]
        self.remote[first["buffer_id"]]["dueAt"] = "2026-11-04T10:01:00Z"
        self.perform()
        edited = {bid for action, bid in self.events if action == "edit"}
        self.assertNotIn(first["buffer_id"], edited)
        self.assertEqual(
            self.request["results"][first["post_id"]]["reason"],
            "past_or_too_close_to_publication",
        )

    def test_ambiguous_edit_is_recorded_and_never_retried(self):
        first = self.request["targets"][0]
        calls = []

        def timeout(bid, text):
            calls.append(bid)
            raise TimeoutError("response lost")

        self.perform(move=timeout)
        self.assertEqual(calls, [first["buffer_id"], *[
            target["buffer_id"] for target in self.request["targets"][1:]
        ]])
        # All ambiguous outcomes are terminal for this one-time request.
        snapshot = copy.deepcopy(self.request)
        self.events.clear()
        self.perform(move=timeout)
        self.assertEqual(self.request, snapshot)
        self.assertFalse(self.events)


    def test_persist_request_commits_when_staged_diff_has_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            social = root / "social-bot"
            social.mkdir()
            target = social / "remaining-legacy-retirement.json"
            calls = []

            def fake_run(args, cwd=None, check=False):
                calls.append(list(args))
                if args[:4] == ["git", "diff", "--cached", "--quiet"]:
                    return SimpleNamespace(returncode=1)
                return SimpleNamespace(returncode=0)

            with patch.object(m, "HERE", social), \
                 patch.object(m, "REQUEST_PATH", target), \
                 patch.object(m.subprocess, "run", side_effect=fake_run):
                m.persist_request({"schema_version": 1, "enabled": False, "results": {}})

            commands = [" ".join(args[:2]) for args in calls]
            self.assertIn("git commit", commands)
            self.assertIn("git push", commands)

    def test_persist_request_skips_commit_when_nothing_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            social = root / "social-bot"
            social.mkdir()
            target = social / "remaining-legacy-retirement.json"
            calls = []

            def fake_run(args, cwd=None, check=False):
                calls.append(list(args))
                if args[:4] == ["git", "diff", "--cached", "--quiet"]:
                    return SimpleNamespace(returncode=0)
                return SimpleNamespace(returncode=0)

            with patch.object(m, "HERE", social), \
                 patch.object(m, "REQUEST_PATH", target), \
                 patch.object(m.subprocess, "run", side_effect=fake_run):
                m.persist_request({"schema_version": 1, "enabled": False, "results": {}})

            self.assertFalse(any(args[:2] == ["git", "commit"] for args in calls))
            self.assertFalse(any(args[:2] == ["git", "push"] for args in calls))


if __name__ == "__main__":
    unittest.main()
