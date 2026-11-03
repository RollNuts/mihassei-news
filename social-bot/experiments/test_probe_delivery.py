#!/usr/bin/env python3
import hashlib
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from probe_delivery import build, reconcile, x_link, normalize_buffer_impressions

class BufferReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.post={"id":"xexp-test","sequence":1,"format":"surreal_text",
                   "text":"架空のテスト文章。実際の投稿ではありません。"}
        self.rec={"buffer_id":"buf-1"}
        self.remote={"id":"buf-1","text":self.post["text"],
                     "channelId":"x-1","status":"sent",
                     "externalLink":"https://x.com/Veripsa/status/123?src=buffer",
                     "sentAt":"2026-11-03T12:00:00Z","assets":[]}
    def test_sent_has_only_provider_permalink(self):
        row=reconcile(self.post,self.rec,self.remote,"x-1")
        self.assertEqual(row["buffer_status"],"sent")
        self.assertEqual(row["buffer_external_link"],
                         "https://x.com/Veripsa/status/123")
        self.remote["status"]="scheduled"
        row=reconcile(self.post,self.rec,self.remote,"x-1")
        self.assertNotIn("buffer_external_link",row)
    def test_cross_account_or_mutated_body_is_unverified(self):
        self.remote["channelId"]="different-x"
        self.assertEqual(reconcile(self.post,self.rec,self.remote,"x-1")
                         ["buffer_status"],"unverified")
        self.remote["channelId"]="x-1"
        self.remote["text"]="hijacked"
        self.assertEqual(reconcile(self.post,self.rec,self.remote,"x-1")
                         ["buffer_status"],"unverified")
    def test_invalid_url_rejected(self):
        for url in ("http://x.com/a/status/1",
                    "https://evil.com/a/status/1",
                    "https://x.com.evil.org/a/status/1",
                    "https://x.com/example"):
            self.assertIsNone(x_link(url))
    def test_provider_metric_requires_genuine_count_and_timestamp(self):
        sent="2026-11-03T10:55:00Z"
        snapshot=normalize_buffer_impressions({
            "metricsUpdatedAt":"2026-11-04T12:00:00Z",
            "metrics":[{"type":"impressions","value":42,"unit":"count"}]},sent)
        self.assertEqual(snapshot["impressions"],42)
        self.assertIsNone(snapshot["window_hours"])
        self.assertEqual(snapshot["provider"],"buffer_experimental_personal_api")
        self.assertIsNone(normalize_buffer_impressions({
            "metricsUpdatedAt":"2026-11-03T09:00:00Z",
            "metrics":[{"type":"impressions","value":99,"unit":"count"}]},sent))
        self.assertIsNone(normalize_buffer_impressions({
            "metricsUpdatedAt":"2026-11-04T12:00:00Z",
            "metrics":[{"type":"impressions","value":-1,"unit":"count"}]},sent))
        self.assertIsNone(normalize_buffer_impressions({
            "metricsUpdatedAt":"2026-11-04T12:00:00Z",
            "metrics":[{"type":"likes","value":99,"unit":"count"}]},sent))

    def test_initial_zero_is_not_used_as_a_real_observation(self):
        sent="2026-11-03T10:55:16Z"
        early={"metricsUpdatedAt":"2026-11-03T10:55:25Z",
               "metrics":[{"type":"impressions","value":0,"unit":"count"}]}
        self.assertIsNone(normalize_buffer_impressions(early,sent))
        early["metrics"][0]["value"]=500
        self.assertIsNone(normalize_buffer_impressions(early,sent))
        early["metricsUpdatedAt"]="2026-11-04T00:00:00Z"
        sampled=normalize_buffer_impressions(early,sent)
        self.assertEqual(sampled["impressions"],500)
        self.assertIsNone(sampled["window_hours"])

    def test_missing_metric_scope_does_not_turn_sent_into_error(self):
        inv={"schema_version":1,"experiments":[self.post]}
        import hashlib
        state={"schema_version":1,"queued":{
            self.post["id"]:{"buffer_id":"buf-1",
                "text_sha256":hashlib.sha256(self.post["text"].encode()).hexdigest()}}}
        def inaccessible(_):
            raise RuntimeError("insightsRead scope unavailable")
        report=build(inv,state,"x-1",lambda _:self.remote,inaccessible)
        row=report["items"][0]
        self.assertEqual(row["buffer_status"],"sent")
        self.assertFalse(row["buffer_metrics_available"])
        self.assertNotIn("buffer_latest_impressions",row)

    def test_missing_remote_never_means_sent(self):
        inv={"schema_version":1,"experiments":[self.post]}
        state={"schema_version":1,"queued":{
            self.post["id"]:{"buffer_id":"buf-1",
                             "text_sha256":hashlib.sha256(self.post["text"].encode()).hexdigest()}}}
        # Pure read normalizes a missing provider response without posting.
        result=build(inv,state,"x-1",lambda id:None)
        self.assertEqual(result["items"][0]["buffer_status"],"unverified")
        self.assertEqual(len(result["recent_unverified_experiment_ids"]),1)
    def test_image_attachment_is_audited_not_assumed(self):
        self.post["visual"]="ufo"
        row=reconcile(self.post,self.rec,self.remote,"x-1")
        self.assertEqual(row["media_review_needed"],"buffer_image_asset_missing")
        self.remote["assets"]=[{"id":"asset1","mimeType":"image/png"}]
        self.assertEqual(reconcile(self.post,self.rec,self.remote,"x-1")
                         ["buffer_asset_count"],1)

if __name__=="__main__":unittest.main(verbosity=2)
