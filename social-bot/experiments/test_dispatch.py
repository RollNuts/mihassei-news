#!/usr/bin/env python3
"""Zero-network safety tests for original Buffer-Free X experiments."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dispatch import decide, hash_text, main, validate, HERE, publication_text, hashtag_variant, choose_one_time_visual_launch, utc_provider_due_at

def sample():
    p={"schema_version":1,"experiments":[
        {"id":"xexp-a","sequence":1,"format":"surreal_text","text":"本物の幽霊が面接で落ちた。理由は演技がわざとらしい。"},
        {"id":"xexp-b","sequence":2,"format":"character_moment","text":"ネジは街灯の部品を一つ締めた。街は何も変わらなかった。"},
    ]}
    c={"schema_version":1,"experiment_publishing_enabled":True,"channel_id":"X-ID",
       "max_queue_size":3,"max_new_per_run":2}
    st={"schema_version":1,"queued":{}}
    return p,c,st


class ExperimentsTests(unittest.TestCase):
    def test_configured_original_posts_pass(self):
        a,b,c=sample()
        self.assertEqual(len(validate(a,b,c)),2)


    def test_buffer_create_post_retains_old_id_return_and_can_return_due_at(self):
        # New Buffer field does not break legacy article / comic callers.
        from publish import queue_one
        receipt={"createPost":{"post":{"id":"buf-123",
                   "dueAt":"2026-11-04T09:30:00Z"}}}
        with patch("publish.gql", return_value=receipt) as api:
            value=queue_one("secret","X-ID",{"kind":"text","text":"original joke"})
            self.assertEqual(value,"buf-123")
            self.assertIn("post { id dueAt }",api.call_args.args[1])
        with patch("publish.gql", return_value=receipt):
            obj=queue_one("secret","X-ID",
                          {"kind":"text","text":"another original joke"},
                          return_post=True)
        self.assertEqual(obj["id"],"buf-123")
        self.assertEqual(obj["dueAt"],"2026-11-04T09:30:00Z")
        with self.assertRaisesRegex(ValueError,"boolean"):
            queue_one("secret","X-ID",{"kind":"text","text":"test"},
                      return_post="yes")

    def test_share_now_is_limited_to_article_lane_or_reviewed_images(self):
        from publish import queue_one
        receipt={"createPost":{"post":{"id":"buf-now","dueAt":None}}}
        with patch("publish.gql", return_value=receipt) as api:
            value=queue_one(
                "secret","X-ID",
                {"kind":"article","id":"article-clear-story","text":"https://veripsa.com/articles/clear-story/"},
                mode="shareNow",
            )
        self.assertEqual(value,"buf-now")
        self.assertIn("mode: shareNow", api.call_args.args[1])
        with self.assertRaisesRegex(ValueError,"reserved"):
            queue_one(
                "secret","X-ID",
                {"kind":"text","id":"xexp-old-text","text":"retired original"},
                mode="shareNow",
            )
        with self.assertRaisesRegex(ValueError,"reserved"):
            queue_one(
                "secret","X-ID",
                {"kind":"article","id":"unsafe/article","text":"article text"},
                mode="shareNow",
            )

        with self.assertRaisesRegex(ValueError,"reserved"):
            queue_one(
                "secret","X-ID",
                {"kind":"article","id":"article-clear-story",
                 "text":"https://veripsa.com/articles/other-story/"},
                mode="shareNow",
            )

    def test_optional_buffer_due_at_never_fabricates_a_slot(self):
        self.assertEqual(utc_provider_due_at("2026-11-04T09:30:00Z"),
                         "2026-11-04T09:30:00+00:00")
        self.assertEqual(utc_provider_due_at("2026-11-04T18:30:00+09:00"),
                         "2026-11-04T09:30:00+00:00")
        for invalid in (None,"","not-a-date","2026-11-04T09:30:00",123):
            self.assertIsNone(utc_provider_due_at(invalid))

    def test_actual_inventory_validates(self):
        p=json.loads((HERE/"posts.json").read_text(encoding="utf-8"))
        c=json.loads((HERE/"config.json").read_text(encoding="utf-8"))
        state=json.loads((HERE/"state.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(validate(p,c,state)),10)
        self.assertEqual(c["max_queue_size"],9)
        self.assertEqual(c["max_new_per_run"],7)
        self.assertEqual(c["expected_daily_slots"],7)

    def test_no_spam_limit_override(self):
        a,b,c=sample()
        b["max_queue_size"]=11
        with self.assertRaisesRegex(ValueError,"between 1 and 9"):
            validate(a,b,c)

    def test_refuse_edit_to_queued_post(self):
        a,b,c=sample()
        c["queued"]["xexp-a"]={"buffer_id":"buf-1","text_sha256":hash_text(a["experiments"][0]["text"])}
        a["experiments"][0]["text"]="違う本文で同じIDを使っている"
        with self.assertRaisesRegex(ValueError,"changed"):
            validate(a,b,c)

    def test_different_punctuation_is_not_a_new_original(self):
        posts, config, state = sample()
        posts["experiments"][1]["text"] = posts["experiments"][0]["text"].replace(
            "。", "！").replace("、", " ")
        with self.assertRaisesRegex(ValueError,"Near-duplicate X original"):
            validate(posts,config,state)

    def test_slightly_reworded_copy_is_rejected(self):
        posts, config, state = sample()
        original=("駅前の時計が毎朝の通勤時間を少しだけ遅く表示していた。"
                  "誰も遅刻していないと思っていたら、時計自身が先に帰宅していた。")
        posts["experiments"][0]["text"] = original
        posts["experiments"][1]["text"] = original.replace(
            "駅前の時計", "公園の時計")
        with self.assertRaisesRegex(ValueError,"Near-duplicate X original"):
            validate(posts,config,state)

    def test_genuinely_different_originals_still_pass_guard(self):
        posts, config, state = sample()
        self.assertEqual(len(validate(posts,config,state)),2)
        # Actual live inventory must remain valid without changing old Buffer IDs.
        inventory=json.loads((HERE/"posts.json").read_text(encoding="utf-8"))
        cfg=json.loads((HERE/"config.json").read_text(encoding="utf-8"))
        ledger=json.loads((HERE/"state.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(validate(inventory,cfg,ledger)),30)

    def test_refuse_duplicate_text_or_ids(self):
        a,b,c=sample()
        a["experiments"][1]["text"]=a["experiments"][0]["text"]
        with self.assertRaisesRegex(ValueError,"Duplicated"):
            validate(a,b,c)

    def test_queue_2_when_empty(self):
        a,b,c=sample()
        selected,recovered,scheduled=decide(a["experiments"],c,[],3,2)
        self.assertEqual(len(selected),2)
        self.assertEqual(recovered,[])
        self.assertEqual(scheduled,0)

    def test_never_exceed_total_3(self):
        a,b,c=sample()
        live=[{"id":"someone","status":"scheduled","text":"not ours"}]*2
        selected,_,scheduled=decide(a["experiments"],c,live,3,2)
        self.assertEqual(len(selected),1)
        self.assertEqual(scheduled,2)
        self.assertEqual(decide(a["experiments"],c,live+[{"id":"last","status":"scheduled","text":"other"}],3,2)[0],[])

    def test_reconcile_after_lost_git_commit(self):
        a,b,c=sample()
        same={"id":"buffer-success","status":"scheduled","text":a["experiments"][0]["text"]}
        plan,recovered,queued=decide(a["experiments"],c,[same],3,2)
        self.assertEqual(recovered,["xexp-a"])
        self.assertEqual([x["id"] for x in plan],["xexp-b"])
        self.assertEqual(c["queued"]["xexp-a"]["buffer_id"],"buffer-success")

    def test_sent_original_is_never_reposted(self):
        a,b,c=sample()
        sent={"id":"already-sent","status":"sent","text":a["experiments"][0]["text"]}
        p,rec,_=decide(a["experiments"],c,[sent],3,2)
        self.assertIn("xexp-a",rec)
        self.assertEqual([x["id"] for x in p],["xexp-b"])

    def test_character_moment_is_not_buried_after_all_surreal_posts(self):
        first_three = [
            {"id":f"xexp-s{i}","sequence":i,"format":"surreal_text",
             "text":f"オリジナルのシュールなボケを検証するためのテスト文章{i}"}
            for i in range(1,7)
        ]
        character = {"id":"xexp-c1","sequence":10,"format":"character_moment",
                     "text":"キャラクターの乾いた会話で印象が変わるテスト投稿"}
        state = {"schema_version":1,"queued":{}}
        # The first three original posts are already booked. Fourth slot
        # should sample a different creative format, not just list order.
        for p in first_three[:3]:
            state["queued"][p["id"]] = {
                "buffer_id":p["id"],"text_sha256":hash_text(p["text"]),
                "format":p["format"]}
        plan,_,_=decide([*first_three,character],state,[],3,2)
        self.assertEqual(plan[0]["id"],"xexp-c1")
        self.assertEqual(plan[1]["id"],"xexp-s4")

    def test_interleaving_does_not_modify_existing_queue(self):
        p,c,state=sample()
        state["queued"]["xexp-a"]={
            "buffer_id":"original-buffer-123",
            "text_sha256":hash_text(p["experiments"][0]["text"]),
            "format":"surreal_text"}
        before=copy.deepcopy(state["queued"])
        decide(p["experiments"],state,[],3,2)
        self.assertEqual(state["queued"],before)

    def test_one_hashtag_on_some_new_posts_and_untagged_control(self):
        posts=json.loads((HERE/"posts.json").read_text(encoding="utf-8"))["experiments"]
        by_id={p["id"]:p for p in posts}
        self.assertEqual(publication_text(by_id["xexp-ghost-interview"]),
                         by_id["xexp-ghost-interview"]["text"])
        self.assertEqual(publication_text(by_id["xexp-ufo-ticket"]),
                         by_id["xexp-ufo-ticket"]["text"]+"\n\n#シュールネタ")
        self.assertTrue(publication_text(by_id["xexp-nejimachi-nothing-wrong"]).endswith("#ネジまち"))
        self.assertNotIn("#",publication_text(by_id["xexp-robot-job"]))
        self.assertLessEqual(max(len(publication_text(p)) for p in posts),180)

    def test_irrelevant_or_multiple_hashtags_are_rejected(self):
        p={"id":"xexp-test","sequence":4,"format":"surreal_text",
           "text":"変わった幽霊が現れる不思議な文章",
           "hashtag":"#バズりたい #拡散希望"}
        with self.assertRaisesRegex(ValueError,"Only one relevant"):
            publication_text(p)
        p["hashtag"]="#ネジまち"
        with self.assertRaisesRegex(ValueError,"Only one relevant"):
            publication_text(p)
        p["hashtag"]="#シュールネタ"
        p["text"]="追加の #幽霊 を混ぜた変わった文章"
        with self.assertRaisesRegex(ValueError,"Additional hashtags"):
            publication_text(p)

    def test_tagged_buffer_recovery_uses_final_text(self):
        p={"id":"xexp-test","sequence":4,"format":"surreal_text",
           "text":"宇宙人の窓口はいつも閉まっていたらしい",
           "hashtag":"#シュールネタ"}
        final=publication_text(p)
        state={"schema_version":1,"queued":{}}
        plan,recovered,queued=decide([p],state,[{"id":"live-123","text":final,"status":"scheduled"}],3,2)
        self.assertEqual(plan,[])
        self.assertEqual(recovered,["xexp-test"])
        self.assertEqual(state["queued"]["xexp-test"]["published_text_sha256"],hash_text(final))
        self.assertEqual(state["queued"]["xexp-test"]["hashtag_variant"],"one_relevant_tag")

    def test_old_buffer_posts_cannot_be_retroactively_tagged(self):
        p,c,state=sample()
        state["queued"]["xexp-a"]={
            "buffer_id":"legacy-booked",
            "text_sha256":hash_text(p["experiments"][0]["text"])}
        p["experiments"][0]["hashtag"]="#シュールネタ"
        with self.assertRaisesRegex(ValueError,"already queued legacy"):
            validate(p,c,state)

    def test_tag_change_after_queue_is_rejected_without_touching_api(self):
        p,c,state=sample()
        p["experiments"][0]["hashtag"]="#シュールネタ"
        final=publication_text(p["experiments"][0])
        state["queued"]["xexp-a"]={
            "buffer_id":"already-booked",
            "text_sha256":hash_text(p["experiments"][0]["text"]),
            "published_text_sha256":hash_text(final)}
        validate(p,c,state)
        del p["experiments"][0]["hashtag"]
        with self.assertRaisesRegex(ValueError,"Previously queued hashtag"):
            validate(p,c,state)

    def test_fifth_slot_can_spotlight_new_original_joke(self):
        posts=[
            {"id":"xexp-old-a","sequence":1,"format":"surreal_text",
             "text":"とても古いテストの文","mechanism":"self_reference"},
            {"id":"xexp-old-b","sequence":2,"format":"surreal_text",
             "text":"二番目のテストです","mechanism":"visual_reveal"},
            {"id":"xexp-old-c","sequence":3,"format":"surreal_text",
             "text":"三番目のテストです","mechanism":"literal_wordplay"},
            {"id":"xexp-old-d","sequence":4,"format":"character_moment",
             "text":"キャラが会話するテスト","mechanism":"dry_dialogue"},
            {"id":"xexp-old-e","sequence":5,"format":"surreal_text",
             "text":"もともと順番が先の話","mechanism":"inverted_expectation"},
            {"id":"xexp-feature","sequence":20,"format":"surreal_text",
             "text":"新しい絵が浮かぶおもしろい話","mechanism":"visual_reveal",
             "featured":True},
        ]
        state={"schema_version":1,"queued":{}}
        for p in posts[:3]:
            state["queued"][p["id"]]={
                "buffer_id":p["id"],"text_sha256":hash_text(p["text"]),
                "format":p["format"]}
        plan,_,_=decide(posts,state,[],3,2)
        self.assertEqual([x["id"] for x in plan],
                         ["xexp-old-d","xexp-feature"])
        self.assertEqual(len(state["queued"]),3)

    def test_rotates_mechanisms_when_distinct_alternative_exists(self):
        posts=[
            {"id":"xexp-one","sequence":1,"format":"surreal_text",
             "text":"いちばん目のオリジナル","mechanism":"visual_reveal"},
            {"id":"xexp-two","sequence":2,"format":"surreal_text",
             "text":"ふたつ目のオリジナル","mechanism":"visual_reveal"},
            {"id":"xexp-three","sequence":3,"format":"surreal_text",
             "text":"別の視点のオリジナル","mechanism":"perspective_flip"},
        ]
        state={"schema_version":1,"queued":{}}
        plan,_,_=decide(posts,state,[],3,2)
        self.assertEqual([x["id"] for x in plan],["xexp-one","xexp-three"])

    def test_unknown_mechanism_and_non_boolean_featured_are_rejected(self):
        posts,c,state=sample()
        posts["experiments"][0]["mechanism"]="very_trending_spam"
        with self.assertRaisesRegex(ValueError,"Unsupported comedy mechanism"):
            validate(posts,c,state)
        posts["experiments"][0]["mechanism"]="visual_reveal"
        posts["experiments"][0]["featured"]="true"
        with self.assertRaisesRegex(ValueError,"Featured must be a boolean"):
            validate(posts,c,state)

    def test_first_available_image_is_prioritized_and_has_a_relevant_tag(self):
        inventory=json.loads((HERE/"posts.json").read_text(encoding="utf-8"))
        posts=inventory["experiments"]
        state={"schema_version":1,"queued":{}}
        for p in posts[:3]:
            state["queued"][p["id"]]={"buffer_id":p["id"],
                                      "text_sha256":hash_text(p["text"])}
        planned,_,_=decide(posts,state,[],3,1)
        self.assertEqual(len(planned),1)
        self.assertEqual(planned[0]["id"],"xexp-ufo-ticket")
        self.assertEqual(planned[0]["visual"],"ufo")
        self.assertTrue(publication_text(planned[0]).endswith("#シュールネタ"))

    def test_unapproved_or_comic_visual_rejected(self):
        p,c,state=sample()
        p["experiments"][0]["visual"]="made-up"
        with self.assertRaisesRegex(ValueError,"Unreviewed"):
            validate(p,c,state)
        p["experiments"][0]["visual"]="ufo"
        with self.assertRaisesRegex(ValueError,"Unreviewed"):
            validate(p,c,state)

    def test_images_continue_after_first_without_flooding(self):
        posts=json.loads((HERE/"posts.json").read_text(encoding="utf-8"))["experiments"]
        state={"schema_version":1,"queued":{}}
        for post in posts[:4]:
            state["queued"][post["id"]]={"buffer_id":post["id"],
                "text_sha256":hash_text(post["text"])}
        plan,_,_=decide(posts,state,[],3,2)
        self.assertEqual(plan[0]["id"],"xexp-invisible-nails")
        self.assertFalse(plan[1].get("visual"))
        for post in plan:
            state["queued"][post["id"]]={"buffer_id":post["id"],
                "text_sha256":hash_text(post["text"])}
        seventh,_,_=decide(posts,state,[],3,1)
        self.assertTrue(seventh[0].get("visual"))
        state["queued"][seventh[0]["id"]]={"buffer_id":seventh[0]["id"],
            "text_sha256":hash_text(seventh[0]["text"])}
        eighth,_,_=decide(posts,state,[],3,1)
        self.assertEqual(eighth[0]["format"],"character_moment")

    def test_one_time_image_launch_even_when_buffer_queue_full(self):
        posts=json.loads((HERE/"posts.json").read_text(encoding="utf-8"))["experiments"]
        state={"schema_version":1,"queued":{}}
        for p in posts[:3]:
            state["queued"][p["id"]]={"buffer_id":p["id"],
                                      "text_sha256":hash_text(p["text"])}
        config={"launch_first_visual_now":True}
        selection=choose_one_time_visual_launch(posts,state,config)
        self.assertEqual(selection["id"],"xexp-ufo-ticket")
        state["queued"][selection["id"]]={"buffer_id":"sent-once",
            "image_sha256":"0"*64,"text_sha256":hash_text(selection["text"])}
        self.assertIsNone(choose_one_time_visual_launch(posts,state,config))
        self.assertIsNone(choose_one_time_visual_launch(posts,
                        {"queued":{}},{"launch_first_visual_now":False}))

    def test_unapproved_immediate_config_value_rejected(self):
        p,c,state=sample()
        c["launch_first_visual_now"]="yes"
        with self.assertRaisesRegex(ValueError, "boolean"):
            validate(p,c,state)

    def test_everyday_satire_pilot_only_in_designated_slots(self):
        posts=json.loads((HERE/"posts.json").read_text(encoding="utf-8"))["experiments"]
        state={"schema_version":1,"queued":{}}
        # Five already submitted (including an original visual): next is slot 6.
        for p in posts[:5]:
            state["queued"][p["id"]]={"buffer_id":p["id"],
                                     "text_sha256":hash_text(p["text"])}
        original=copy.deepcopy(state)
        plan,_,_=decide(posts,state,[],3,2)
        self.assertEqual(plan[0]["id"],"xexp-selfcheckout-dialogue")
        self.assertEqual(plan[0]["angle"],"everyday_satire")
        self.assertNotEqual(plan[1].get("angle"),"everyday_satire")
        self.assertEqual(state,original)

    def test_pilot_has_valid_source_texts_and_balanced_tags(self):
        inventory=json.loads((HERE/"posts.json").read_text(encoding="utf-8"))
        cfg=json.loads((HERE/"config.json").read_text(encoding="utf-8"))
        ledger=json.loads((HERE/"state.json").read_text(encoding="utf-8"))
        validate(inventory,cfg,ledger)
        pilots=[p for p in inventory["experiments"] if p.get("angle")=="everyday_satire"]
        self.assertGreaterEqual(len(pilots),6)
        self.assertGreaterEqual(sum(bool(p.get("hashtag")) for p in pilots),3)
        self.assertGreaterEqual(sum(not p.get("hashtag") for p in pilots),3)
        self.assertTrue(all(12 <= len(publication_text(p)) <= 180 for p in pilots))
        p,c,state=sample()
        p["experiments"][0]["angle"]="viral_growth"
        with self.assertRaisesRegex(ValueError,"angle"):
            validate(p,c,state)

    def test_owner_seven_daily_slots_fit_nine_plus_one_free_budget(self):
        src, cfg, state = sample()
        cfg.update(max_queue_size=9,max_new_per_run=7,expected_daily_slots=7)
        validate(src,cfg,state)
        examples = [
            {"id":f"xexp-demo{i}","sequence":i,"format":"surreal_text",
             "text":f"テスト用のオリジナル投稿の本文です。番号{i}"}
            for i in range(1,20)
        ]
        proposed,_,count=decide(examples,state,[],9,7)
        self.assertEqual((len(proposed),count),(7,0))
        remote=[{"id":f"buf-{i}","status":"scheduled",
                 "text":f"無関係の予約{i}"} for i in range(8)]
        proposed,_,_=decide(examples,state,remote,9,7)
        self.assertEqual(len(proposed),1)
        full=remote+[{"id":"article","status":"scheduled","text":"別の記事"}]
        proposed,_,_=decide(examples,state,full,9,7)
        self.assertEqual(proposed,[])
        cfg["max_new_per_run"]=8
        with self.assertRaisesRegex(ValueError,"between 1 and 7"):
            validate(src,cfg,state)
        cfg["max_new_per_run"]=7
        cfg["max_queue_size"]=10
        with self.assertRaisesRegex(ValueError,"between 1 and 9"):
            validate(src,cfg,state)

    def test_buffer_sent_history_paginates_with_safe_cursors(self):
        from publish import list_existing
        responses=[
            {"posts":{"edges":[{"node":{"id":"scheduled-a","text":"投稿A",
                                       "status":"scheduled"}}],
                      "pageInfo":{"hasNextPage":True,"endCursor":"next-page"}}},
            {"posts":{"edges":[{"node":{"id":"sent-b","text":"投稿B",
                                       "status":"sent"}}],
                      "pageInfo":{"hasNextPage":False,"endCursor":None}}},
        ]
        with patch("publish.gql",side_effect=responses) as gql:
            values=list_existing("token","org","channel")
        self.assertEqual([v["id"] for v in values],["scheduled-a","sent-b"])
        self.assertIn('after: "next-page"',gql.call_args_list[1].args[1])
        self.assertIn("dueAt: {start:",gql.call_args_list[0].args[1])

    def test_buffer_unsettled_listing_is_opt_in_for_article_recovery(self):
        from publish import list_existing
        responses = [
            {"posts":{"edges":[{"node":{"id":"sent-a","text":"記事A",
                                         "status":"sent","sentAt":"2026-11-05T01:00:00Z"}}],
                      "pageInfo":{"hasNextPage":False,"endCursor":None}}},
            {"posts":{"edges":[
                {"node":{"id":"approval-b","text":"記事B","status":"needs_approval"}},
                {"node":{"id":"sending-c","text":"記事C","status":"sending"}}
            ],"pageInfo":{"hasNextPage":False,"endCursor":None}}},
        ]
        with patch("publish.gql", side_effect=responses) as api:
            values = list_existing(
                "token","org","channel",
                include_due_at=True, include_unsettled=True,
            )
        self.assertEqual(
            [row["id"] for row in values],
            ["sent-a","approval-b","sending-c"],
        )
        self.assertIn("status: [scheduled, sent]", api.call_args_list[0].args[1])
        self.assertIn("dueAt: {start:", api.call_args_list[0].args[1])
        self.assertIn(
            "status: [draft, error, needs_approval, sending]",
            api.call_args_list[1].args[1],
        )
        self.assertNotIn("dueAt: {start:", api.call_args_list[1].args[1])

    def test_buffer_missing_pagination_cursor_fails_closed(self):
        from publish import list_existing
        result={"posts":{"edges":[],"pageInfo":{"hasNextPage":True,
                                                  "endCursor":None}}}
        with patch("publish.gql",return_value=result):
            with self.assertRaisesRegex(RuntimeError,"cursor invalid"):
                list_existing("token","org","channel")

    def test_state_ledger_prevents_repeat_when_outside_buffer_window(self):
        a,b,c=sample()
        c["queued"]["xexp-a"]={"buffer_id":"past-id","text_sha256":hash_text(a["experiments"][0]["text"])}
        p,_,_=decide(a["experiments"],c,[],3,2)
        self.assertEqual([x["id"] for x in p],["xexp-b"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
