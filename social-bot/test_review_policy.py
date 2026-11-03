#!/usr/bin/env python3
"""No-network regression suite: do not publish unreviewed/rejected manga."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
from review_policy import load_manifest, select_next, verify_one, EditorialHold
from ip.audit import payload_hash


def chapter(n):
    return {
        "id":f"nejimachi-{n:03d}-test",
        "episode":n,
        "series":"nejimachi",
        "kind":"comic",
        "text":f"完全なオリジナル漫画・第{n}話"
    }


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory()
        self.root=Path(self.dir.name)
        self.episodes=[chapter(i) for i in (1,2,3)]
        self.manifest={
            "schema_version":1,
            "owner_approval_required":True,
            "approved":{},
            "rejected":{}
        }
        for post in self.episodes:
            (self.root/(post["id"]+".png")).write_bytes(b"image-bytes:"+post["id"].encode())

    def tearDown(self):
        self.dir.cleanup()

    def approve(self,post):
        self.manifest["approved"][post["id"]]={
            "approved_by":"owner",
            "reviewed_at_jst":"2026-11-03",
            "age_target":"adult",
            "post_sha256":payload_hash(post),
            "image_sha256":hashlib.sha256((self.root/(post["id"]+".png")).read_bytes()).hexdigest(),
        }

    def test_missing_manifest_fails_closed(self):
        with self.assertRaises(EditorialHold):
            load_manifest(self.root/"does-not-exist.json")

    def test_approval_must_have_owner_and_hashes(self):
        p=self.episodes[0]
        self.approve(p)
        self.assertTrue(verify_one(p,self.manifest["approved"][p["id"]],self.root))
        for key in ("approved_by","image_sha256","post_sha256","age_target"):
            saved=self.manifest["approved"][p["id"]][key]
            self.manifest["approved"][p["id"]][key]="wrong"
            with self.subTest(field=key),self.assertRaises(EditorialHold):
                verify_one(p,self.manifest["approved"][p["id"]],self.root)
            self.manifest["approved"][p["id"]][key]=saved

    def test_new_image_invalidates_approval(self):
        p=self.episodes[0];self.approve(p)
        (self.root/(p["id"]+".png")).write_bytes(b"REGENERATED; NEVER SILENTLY ACCEPT")
        with self.assertRaisesRegex(EditorialHold,"artwork differs"):
            verify_one(p,self.manifest["approved"][p["id"]],self.root)

    def test_content_edit_invalidates_approval(self):
        p=self.episodes[0];self.approve(p)
        other=dict(p,text="manuscript changed silently")
        with self.assertRaisesRegex(EditorialHold,"differs"):
            verify_one(other,self.manifest["approved"][p["id"]],self.root)

    def test_no_approval_no_queue_even_when_global_switch_would_be_on(self):
        with self.assertRaisesRegex(EditorialHold,"no human-reviewed"):
            select_next(self.episodes,{},[],self.manifest,self.root)

    def test_rejected_episode_never_queued_even_if_manually_approved(self):
        self.manifest["rejected"][self.episodes[0]["id"]]={"reason":"derivative"}
        with self.assertRaisesRegex(EditorialHold,"rejected"):
            select_next(self.episodes,{},[],self.manifest,self.root)

    def test_archived_prior_episode_blocks_later_chapters(self):
        p=self.episodes[0];self.approve(self.episodes[1]);self.approve(self.episodes[2])
        submitted={p["id"]:{"buffer_id":"old-id"}}
        with self.assertRaisesRegex(EditorialHold,"possible draft"):
            select_next(self.episodes,submitted,[],self.manifest,self.root)

    def test_existing_sent_and_scheduled_allow_only_next_approved(self):
        first,second,third=self.episodes
        self.approve(third)
        submitted={
            first["id"]:{"buffer_id":"old-1"},
            second["id"]:{"buffer_id":"old-2"},
        }
        live=[
            {"id":"old-1","status":"sent","text":first["text"]},
            {"id":"old-2","status":"scheduled","text":second["text"]},
        ]
        selected=select_next(self.episodes,submitted,live,self.manifest,self.root,max_new=2,max_queue=3)
        self.assertEqual([p["id"] for p in selected],[third["id"]])

    def test_unapproved_gap_never_skips_to_later_episode(self):
        first,second,third=self.episodes
        self.approve(first);self.approve(third)
        with self.assertRaisesRegex(EditorialHold,"no human-reviewed"):
            select_next(self.episodes,{},[],self.manifest,self.root)

    def test_queue_cap_kept(self):
        for p in self.episodes:self.approve(p)
        live=[{"id":f"external-{i}","status":"scheduled","text":"unrelated"} for i in range(3)]
        self.assertEqual(select_next(self.episodes,{},live,self.manifest,self.root),[])


if __name__=="__main__":
    unittest.main(verbosity=2)
