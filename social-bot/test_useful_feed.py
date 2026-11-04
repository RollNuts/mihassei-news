import copy
import importlib.util
import json
import tempfile
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("useful_feed", HERE / "useful_feed.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class UsefulFeedTests(unittest.TestCase):
    def setUp(self):
        # Test candidates are independent of the live queue. An old manuscript
        # must not become a quality-approved fixture just because it was sent.
        p = {
            "id": "xexp-useful-20261104-cancel-window",
            "kind": "conversation_example",
            "delivery": "automatic",
            "trend_targeted": False,
            "publish_at": "2026-11-04T10:00:00Z",
            "checked_at": "2026-11-04T00:00:00Z",
            "valid_until": "2026-11-04T14:59:00Z",
            "text": "創作・会話例。\n客『安いプラン、料金の次に何を見ます？』\n店員『解約方法と受付時間です。このプラン、入会はWebで24時間ですが、解約は平日昼間の電話だけ』\n客『月額より昼休みのほうが高いな』",
            "basis": "架空の窓口。入会と解約の手間の非対称を場面にした。実在サービスの仕様ではない。",
            "sources": [],
        }
        p["editorial_review"] = {
            "decision": "approved",
            "text_sha256": m.fingerprint(p["text"]),
            "concrete_anchor": "解約は平日昼間の電話だけ",
            "added_value": "解約方法と受付時間です",
            "reader_payoff": "料金だけでは選びづらい読者へ、次に比べる項目を解約方法と受付時間の二つに絞り込んだ。",
            "why_now_or_save": "契約前に読めば、解約手段の確認漏れや平日昼間に電話する手間を避ける判断に使える。",
        }
        self.feed = {"schema_version": 1, "enabled": True, "channel_id": m.CHANNEL, "posts": [p]}
        self.state = {"schema_version": 1, "attempts": {}}
        self.now = datetime(2026, 11, 4, 1, tzinfo=timezone.utc)

    def pick(self, live=(), prior=()):
        return m.select(self.feed, self.state, self.now, list(live), prior)

    def selection_reason(self):
        reasons = []
        self.assertIsNone(m.select(self.feed, self.state, self.now, [], reasons=reasons))
        return reasons[0]["reason"]

    def as_source_commentary(self):
        p = self.feed["posts"][0]
        p.update(kind="source_commentary", source_date="2026-11-04",
                 sources=["https://example.com/terms"],
                 text="公開仕様（11/4）：入会はWebで24時間、解約受付は平日10～16時。月額の次は『解約方法と受付時間』を確認。昼間に電話できない人は、Webで解約できるプランと比べたい。",
                 basis="テスト用の架空資料。受付条件が資料の事実、確認項目と比較先への提案は筆者の解釈。")
        p["editorial_review"].update(
            text_sha256=m.fingerprint(p["text"]),
            concrete_anchor="解約受付は平日10～16時",
            added_value="月額の次は『解約方法と受付時間』を確認",
        )
        return p

    def test_valid_is_eligible(self): self.assertIsNotNone(self.pick())
    def test_disabled_is_noop(self):
        self.feed["enabled"] = False
        self.assertIsNone(self.pick())
    def test_no_catchup(self):
        self.now = datetime(2026, 11, 5, 1, tzinfo=timezone.utc)
        self.assertIsNone(self.pick())
    def test_no_late_submit(self):
        self.now = datetime(2026, 11, 4, 9, 50, tzinfo=timezone.utc)
        self.assertIsNone(self.pick())
    def test_queue_cap(self): self.assertIsNone(self.pick([{"status": "scheduled"}] * 10))
    def test_exact_copy(self): self.assertIsNone(self.pick([{"text": self.feed["posts"][0]["text"], "status": "sent"}]))
    def test_legacy_copy(self): self.assertIsNone(self.pick(prior=[self.feed["posts"][0]["text"]]))
    def test_pending_intent_never_retried(self):
        p = self.feed["posts"][0]
        self.state["attempts"][p["id"]] = {"day_jst": "2026-11-04", "text_sha256": m.fingerprint(p["text"])}
        self.assertIsNone(self.pick())
    def test_second_id_same_day_rejected(self):
        p = copy.deepcopy(self.feed["posts"][0]); p["id"] += "-other"; p["text"] = "これは完全に別の長さが十分なテスト原稿です。"
        self.feed["posts"].append(p)
        with self.assertRaises(ValueError): self.pick()
    def test_trend_rejected(self):
        self.feed["posts"][0]["trend_targeted"] = True
        with self.assertRaises(ValueError): self.pick()
    def test_crisis_topic_cannot_enter_automatic_lane(self):
        p = self.feed["posts"][0]
        p["text"] = "地震の被害について今すぐ考えるべきことをまとめました。"
        p["editorial_review"]["text_sha256"] = m.fingerprint(p["text"])
        with self.assertRaisesRegex(ValueError, "Crisis-topic automatic X posts are forbidden"):
            m.validate(self.feed, self.state)

    def test_attempted_legacy_payload_is_not_rewritten_by_new_crisis_guard(self):
        p = self.feed["posts"][0]
        p["text"] = "地震についての過去の送信済み原稿です。"
        p["editorial_review"]["text_sha256"] = m.fingerprint(p["text"])
        self.state["attempts"][p["id"]] = {
            "day_jst": "2026-11-04",
            "text_sha256": m.fingerprint(p["text"]),
            "payload_sha256": m.payload_hash(p),
        }
        m.validate(self.feed, self.state)
        self.assertIsNone(self.pick())

    def test_market_needs_evidence(self):
        self.feed["posts"][0]["kind"] = "market_close"
        with self.assertRaises(ValueError): self.pick()
    def test_manual_never_sent(self):
        self.feed["posts"][0]["delivery"] = "manual_only"
        self.assertIsNone(self.pick())
    def test_crowding(self):
        self.assertIsNone(self.pick([{"text": "other", "status": "scheduled", "dueAt": "2026-11-04T10:15:00Z"}]))
    def test_no_amending_attempt(self):
        p = self.feed["posts"][0]
        self.state["attempts"][p["id"]] = {"text_sha256": "wrong"}
        with self.assertRaises(ValueError): self.pick()
    def test_oversize_rejected(self):
        self.feed["posts"][0]["text"] = "あ" * 139
        with self.assertRaises(ValueError): self.pick()
    def test_wrong_account(self):
        self.feed["channel_id"] = "other"
        with self.assertRaises(ValueError): self.pick()
    def test_near_duplicate(self):
        text = self.feed["posts"][0]["text"]
        self.assertTrue(m.close_copy(text, text + "。"))
    def test_fake_conversation_unmarked(self):
        self.feed["posts"][0]["text"] = "昨日友人に言われた話です。これは事実とは確認できません。"
        with self.assertRaises(ValueError): self.pick()

    def test_last_free_slot_available_for_useful_original(self):
        self.assertIsNotNone(self.pick([{"status": "scheduled"}] * 9))

    def test_past_sent_posts_do_not_consume_slots(self):
        self.assertIsNotNone(self.pick([{"status": "sent"}] * 20))

    def test_full_queue_has_exact_reason(self):
        reasons = []
        m.select(self.feed, self.state, self.now, [{"status": "scheduled"}] * 10, reasons=reasons)
        self.assertEqual(reasons, [{"reason": "shared_free_queue_full"}])

    def test_crowding_has_exact_reason_without_exposing_other_text(self):
        reasons = []
        m.select(self.feed, self.state, self.now,
                 [{"text": "private other post", "status": "scheduled", "dueAt": "2026-11-04T10:15:00Z"}], reasons=reasons)
        self.assertEqual(reasons[0]["reason"], "within_30_minutes_of_existing_reservation")
        self.assertNotIn("private other post", json.dumps(reasons))

    def test_cutoff_has_exact_reason(self):
        reasons = []
        now = datetime(2026, 11, 4, 9, 50, tzinfo=timezone.utc)
        m.select(self.feed, self.state, now, [], reasons=reasons)
        self.assertEqual(reasons[0]["reason"], "past_cutoff_or_expired")

    def test_selection_does_not_mutate_posts_or_ledger(self):
        feed, state = copy.deepcopy(self.feed), copy.deepcopy(self.state)
        self.pick([{"status": "scheduled"}] * 9)
        self.assertEqual(self.feed, feed)
        self.assertEqual(self.state, state)

    def test_basis_alone_cannot_approve_a_generic_conversation(self):
        p = self.feed["posts"][0]
        p["text"] = "改善するときの会話例。『何を変えた？』『見出しも時間も』まずは1つ変える。結果だけでなく理由も残したい。"
        p["basis"] = "A useful, original, non-private hypothetical conversation."
        del p["editorial_review"]
        self.assertEqual(self.selection_reason(), "editorial_review_missing")
        with self.assertRaisesRegex(ValueError, "editorial_review_missing"):
            m.validate(self.feed, self.state)

    def test_boolean_approval_is_not_a_review(self):
        self.feed["posts"][0]["editorial_review"] = True
        self.assertEqual(self.selection_reason(), "editorial_review_invalid")

    def test_rewritten_text_requires_another_review(self):
        self.feed["posts"][0]["text"] += "。"
        self.assertEqual(self.selection_reason(), "editorial_review_stale")
        with self.assertRaisesRegex(ValueError, "editorial_review_stale"):
            m.validate(self.feed, self.state)

    def test_pending_review_is_not_approval(self):
        self.feed["posts"][0]["editorial_review"]["decision"] = "pending"
        self.assertEqual(self.selection_reason(), "editorial_review_unapproved")

    def test_rejection_can_be_recorded_but_never_selected(self):
        self.feed["posts"][0]["editorial_review"]["decision"] = "rejected"
        m.validate(self.feed, self.state)
        self.assertEqual(self.selection_reason(), "editorial_review_rejected")

    def test_rejected_decision_does_not_follow_changed_text(self):
        self.feed["posts"][0]["editorial_review"]["decision"] = "rejected"
        self.feed["posts"][0]["text"] += "。"
        with self.assertRaisesRegex(ValueError, "editorial_review_stale"):
            m.validate(self.feed, self.state)

    def test_review_quotes_must_appear_in_this_manuscript(self):
        for key in ("concrete_anchor", "added_value"):
            with self.subTest(key=key):
                review = self.feed["posts"][0]["editorial_review"]
                original = review[key]
                review[key] = "本文にないもっともらしい解釈を記入する"
                self.assertEqual(self.selection_reason(), "editorial_review_ungrounded")
                review[key] = original

    def test_one_excerpt_cannot_explain_both_subject_and_added_value(self):
        review = self.feed["posts"][0]["editorial_review"]
        review["concrete_anchor"] = review["added_value"]
        self.assertEqual(self.selection_reason(), "editorial_review_ungrounded")

    def test_bare_praise_cannot_replace_reader_value(self):
        for key in ("reader_payoff", "why_now_or_save"):
            for praise in ("有益", "共感できる", "面白い", "独自性がある", " " * 30):
                with self.subTest(key=key, praise=praise):
                    review = self.feed["posts"][0]["editorial_review"]
                    original = review[key]
                    review[key] = praise
                    self.assertEqual(self.selection_reason(), "editorial_review_incomplete")
                    review[key] = original

    def test_repeated_boilerplate_is_not_two_editorial_reasons(self):
        review = self.feed["posts"][0]["editorial_review"]
        review["why_now_or_save"] = review["reader_payoff"]
        self.assertEqual(self.selection_reason(), "editorial_review_incomplete")

    def test_explainer_requires_a_source_even_with_complete_review(self):
        self.feed["posts"][0]["kind"] = "evergreen_explainer"
        self.assertEqual(self.selection_reason(), "source_evidence_missing")
        with self.assertRaisesRegex(ValueError, "source_evidence_missing"):
            m.validate(self.feed, self.state)

    def test_fictional_conversation_does_not_invent_a_source(self):
        self.assertEqual(self.feed["posts"][0]["sources"], [])
        m.validate(self.feed, self.state)
        self.assertIsNotNone(self.pick())

    def test_sources_must_be_a_list(self):
        for sources in (None, "https://example.com/terms", {"url": "https://example.com/terms"}):
            with self.subTest(sources=sources):
                self.feed["posts"][0]["sources"] = sources
                with self.assertRaisesRegex(ValueError, "Sources must be a list"):
                    m.validate(self.feed, self.state)

    def test_sources_must_be_https(self):
        self.feed["posts"][0]["sources"] = ["http://example.com/terms"]
        with self.assertRaisesRegex(ValueError, "HTTPS sources required"):
            m.validate(self.feed, self.state)

    def test_source_commentary_can_be_selected_with_fresh_evidence(self):
        self.as_source_commentary()
        m.validate(self.feed, self.state)
        self.assertIsNotNone(self.pick())

    def test_source_commentary_cannot_be_sent_without_sources(self):
        self.as_source_commentary()["sources"] = []
        self.assertEqual(self.selection_reason(), "source_evidence_missing")

    def test_source_commentary_requires_real_source_date(self):
        p = self.as_source_commentary()
        for value in (None, "", "2026-2-3", "2026-02-30", "2026-11-05"):
            with self.subTest(value=value):
                p["source_date"] = value
                with self.assertRaises(ValueError):
                    m.validate(self.feed, self.state)

    def test_source_commentary_cannot_use_a_day_old_check(self):
        self.as_source_commentary()["checked_at"] = "2026-11-03T18:00:00Z"
        with self.assertRaisesRegex(ValueError, "check within 12 hours"):
            m.validate(self.feed, self.state)

    def test_new_automatic_manuscript_expires_in_same_jst_day(self):
        self.feed["posts"][0]["valid_until"] = "2026-11-04T15:00:00Z"
        with self.assertRaisesRegex(ValueError, "expire on their publish day in JST"):
            m.validate(self.feed, self.state)

    def test_existing_attempt_without_review_keeps_its_exact_payload(self):
        p = self.feed["posts"][0]
        del p["editorial_review"]
        # Legacy attempts may have valid old metadata. Never retrofit a review
        # or change their expiry just to make a new schema pass.
        p["valid_until"] = "2026-11-05T14:59:00Z"
        self.state["attempts"][p["id"]] = {
            "day_jst": "2026-11-04", "text_sha256": m.fingerprint(p["text"]),
            "payload_sha256": m.payload_hash(p),
        }
        before = copy.deepcopy((self.feed, self.state))
        m.validate(self.feed, self.state)
        self.assertIsNone(self.pick())
        self.assertEqual((self.feed, self.state), before)

    def test_adding_review_to_attempted_payload_is_still_forbidden(self):
        p = self.feed["posts"][0]
        review = p.pop("editorial_review")
        self.state["attempts"][p["id"]] = {
            "text_sha256": m.fingerprint(p["text"]), "payload_sha256": m.payload_hash(p),
        }
        p["editorial_review"] = review
        with self.assertRaisesRegex(ValueError, "Cannot change an attempted post"):
            m.validate(self.feed, self.state)

    def test_new_review_cannot_mutate_after_durable_intent(self):
        p = self.feed["posts"][0]
        self.state["attempts"][p["id"]] = {
            "text_sha256": m.fingerprint(p["text"]), "payload_sha256": m.payload_hash(p),
        }
        p["editorial_review"]["decision"] = "rejected"
        with self.assertRaisesRegex(ValueError, "Cannot change an attempted post"):
            m.validate(self.feed, self.state)

    def test_manual_draft_does_not_require_automatic_approval(self):
        p = self.feed["posts"][0]
        p["delivery"] = "manual_only"
        del p["editorial_review"]
        m.validate(self.feed, self.state)
        self.assertIsNone(self.pick())

    def test_validate_command_rejects_missing_review_before_network(self):
        del self.feed["posts"][0]["editorial_review"]
        with patch.object(m, "read", side_effect=[self.feed, self.state]), \
                patch.object(m.sys, "argv", ["useful_feed.py", "--validate"]), \
                patch.object(m, "persist") as persist:
            with self.assertRaisesRegex(ValueError, "editorial_review_missing"):
                m.main()
            persist.assert_not_called()

    def test_validate_command_does_not_require_buffer_credentials(self):
        with patch.object(m, "read", side_effect=[self.feed, self.state]), \
                patch.object(m.sys, "argv", ["useful_feed.py", "--validate"]), \
                patch.dict(m.os.environ, {}, clear=True), patch("builtins.print") as output:
            m.main()
            output.assert_called_once_with("Validated. No Buffer request.")

class UsefulFeedPersistenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "useful-state.json"
        self.state = {
            "schema_version": 1,
            "attempts": {"test-post": {
                "provider_status": "scheduled", "buffer_id": "receipt-1",
                "due_at": "2026-11-04T03:00:00Z", "sent_at": None,
                "attempted_at": "2026-11-04T01:00:00Z",
                "checked_at": "2026-11-04T02:00:00Z",
            }},
            "last_selection": {
                "checked_at_utc": "2026-11-04T02:00:00Z", "scheduled_count": 9,
                "shared_queue_limit": 10, "selected_post_id": None,
                "reasons": [{"reason": "day_already_attempted"}],
            },
        }
        self.path.write_text(json.dumps(self.state) + "\n", encoding="utf-8")
        path_patch = patch.object(m, "STATE_PATH", self.path)
        path_patch.start()
        self.addCleanup(path_patch.stop)
        # No test may run Git or contact a provider. Simulate a real staged diff.
        def git_result(args, **kwargs):
            return Mock(returncode=1 if args[1] == "diff" else 0)
        run_patch = patch.object(m.subprocess, "run", side_effect=git_result)
        self.run = run_patch.start()
        self.addCleanup(run_patch.stop)

    def test_recheck_changes_neither_file_nor_git(self):
        original = self.path.read_bytes()
        self.state["attempts"]["test-post"]["checked_at"] = "2026-11-04T02:54:00Z"
        self.state["last_selection"]["checked_at_utc"] = "2026-11-04T02:54:01Z"
        unchanged_input = copy.deepcopy(self.state)
        m.persist(self.state)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.state, unchanged_input)
        self.run.assert_not_called()

    def assert_persisted(self):
        m.persist(self.state)
        self.assertEqual(m.read(self.path), self.state)
        self.assertIn(["git", "push", "origin", "HEAD:main"],
                      [call.args[0] for call in self.run.call_args_list])

    def test_delivery_transition_keeps_provider_evidence(self):
        self.state["attempts"]["test-post"].update(
            provider_status="sent", sent_at="2026-11-04T03:00:01Z",
            checked_at="2026-11-04T03:05:00Z")
        self.assert_persisted()

    def test_new_intent_without_receipt_is_durable(self):
        self.state["attempts"]["new-post"] = {
            "provider_status": "intent", "attempted_at": "2026-11-05T01:00:00Z",
            "day_jst": "2026-11-05", "text_sha256": "immutable-text",
        }
        self.assert_persisted()
        self.assertNotIn("buffer_id", m.read(self.path)["attempts"]["new-post"])

    def test_changed_queue_or_selection_is_durable(self):
        for key, value in (
            ("scheduled_count", 10),
            ("reasons", [{"reason": "shared_free_queue_full"}]),
            ("selected_post_id", "next-post"),
        ):
            with self.subTest(key=key):
                self.run.reset_mock()
                self.state["last_selection"][key] = value
                self.assert_persisted()

    def test_only_observation_times_are_ignored(self):
        for key, value in (
            ("buffer_id", "receipt-2"),
            ("due_at", "2026-11-04T03:05:00Z"),
            ("attempted_at", "2026-11-04T01:01:00Z"),
            ("payload_sha256", "immutable-payload"),
        ):
            with self.subTest(key=key):
                self.run.reset_mock()
                self.state["attempts"]["test-post"][key] = value
                self.assert_persisted()

    def test_first_ledger_is_persisted(self):
        self.path.unlink()
        self.assert_persisted()


if __name__ == "__main__": unittest.main()
