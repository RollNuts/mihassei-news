"""Offline guards for one monitored preview; never import a model framework."""
import contextlib
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import sdxl_scene_preview as preview
from scripts import news_caster_preview as bounded_download
from tests.test_motion_workflow import job, applies
from tests.test_scene_sync_hardening import jpeg as jpeg_fixture


def environment(temporary, ref="refs/heads/main"):
    return {"GITHUB_ACTIONS":"true", "GITHUB_REPOSITORY":preview.REPOSITORY, "GITHUB_REF":ref,
            "GITHUB_WORKFLOW_REF":f"{preview.REPOSITORY}/.github/workflows/free-cpu-scenes.yml@{ref}",
            "GITHUB_SHA":"a"*40, "GITHUB_EVENT_NAME":"workflow_dispatch", "RUNNER_OS":"Linux",
            "RUNNER_ENVIRONMENT":"github-hosted", "RUNNER_TEMP":str(temporary), "GITHUB_RUN_ID":"123",
            "GITHUB_RUN_ATTEMPT":"1",
            "SDXL_PUBLIC_REPOSITORY":"true", "PREVIEW_MODEL":"sdxl", "VERIPSA_ENABLE_FREE_CPU":"1",
            "USER_REQUESTED_RETRY_AFTER":"",
            "SCENE_SLUG":preview.SLUG, "SCENE_LIMIT":"1", "RETRY_HELD":"1", "SCENE_PUBLISH":"0",
            "BACKFILL_SCENE":"", "BACKFILL_REFERENCE":"0"}


class TensorFixture:
    def __init__(self,data,shape=None):
        self.data=data
        self.shape=shape or ((len(data),len(data[0])) if data and isinstance(data[0],list) else (len(data),))
        self.ndim=len(self.shape)
    def flat(self):return [v for row in self.data for v in row] if self.data and isinstance(self.data[0],list) else self.data
    def numel(self):return len(self.flat())
    def to(self,**kwargs):return self
    def __truediv__(self,other):return self
    def __gt__(self,other):return SimpleNamespace(all=lambda:all(v>other for v in self.flat()))


def tensor_api():
    return SimpleNamespace(is_tensor=lambda v:isinstance(v,TensorFixture),
        isfinite=lambda v:SimpleNamespace(all=lambda:all(math.isfinite(x) for x in v.flat())),
        linalg=SimpleNamespace(vector_norm=lambda v,dim:TensorFixture([math.hypot(*row) for row in v.data])),
        float32="float32",bfloat16="bfloat16",device=lambda name:name,inference_mode=contextlib.nullcontext,
        Generator=lambda **kwargs:SimpleNamespace(manual_seed=lambda seed:seed))


class SdxlPreviewTests(unittest.TestCase):
    def source(self):
        return next(a for a in json.loads((preview.ROOT / "content/articles.json").read_text()) if a["slug"]==preview.SLUG)

    def output_fixture(self):
        class RGB:
            shape=(1,576,1024,3);finite=True
            def min(self):return 0
            def max(self):return 1
            def __getitem__(self,key):return self
            def __mul__(self,value):return self
            def round(self):return self
            def astype(self,value):return self
        np=SimpleNamespace(ndarray=RGB,isfinite=lambda value:SimpleNamespace(all=lambda:value.finite))
        image=SimpleNamespace(save=lambda output,**options:output.write(b"synthetic PNG fixture"))
        return RGB(),np,SimpleNamespace(fromarray=lambda *args,**options:image)

    def test_safe_numeric_quality_failure_preserves_unreviewed_jpeg_and_warning(self):
        rgb,np,image_type=self.output_fixture();jpeg=jpeg_fixture()
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory)
            def quality(image):
                self.assertEqual((work/"rendered.jpg").read_bytes(),jpeg)
                self.assertTrue(json.loads((work/"progress.json").read_text())["safety_checked"])
                raise ValueError(preview.QUALITY_FAILURE)
            with patch.object(preview,"png_to_jpeg",return_value=jpeg):
                preview.save_unreviewed_output(rgb,[False],np,image_type,work,quality)
            result=json.loads((work/"result.json").read_text());worker=json.loads((work/"progress.json").read_text())
            self.assertEqual(result["status"],"generated_unreviewed")
            self.assertEqual(result["quality_warning"],preview.QUALITY_FAILURE)
            self.assertEqual(worker["quality_warning"],result["quality_warning"])
            self.assertEqual(result["safety_flags"],[False]);self.assertTrue(worker["safety_checked"])
            self.assertEqual(result["sha256"],preview.hashlib.sha256(jpeg).hexdigest())
            self.assertEqual(worker["stage"],"worker_succeeded")

    def test_unsafe_or_nonfinite_pixels_never_reach_jpeg_and_other_errors_still_fail(self):
        for flags,finite in (([True],True),(None,True),([False],False)):
            rgb,np,image_type=self.output_fixture();rgb.finite=finite
            with tempfile.TemporaryDirectory() as directory,patch.object(preview,"png_to_jpeg") as encode:
                work=Path(directory)
                with self.assertRaises(ValueError):preview.save_unreviewed_output(rgb,flags,np,image_type,work,Mock())
                encode.assert_not_called();self.assertFalse((work/"rendered.jpg").exists())
                self.assertFalse(json.loads((work/"progress.json").read_text())["safety_checked"])
        for error in (RuntimeError("runtime failure"),ValueError("unexpected quality failure")):
            rgb,np,image_type=self.output_fixture()
            with tempfile.TemporaryDirectory() as directory,patch.object(preview,"png_to_jpeg",return_value=jpeg_fixture()):
                work=Path(directory)
                with self.assertRaises(type(error)):
                    preview.save_unreviewed_output(rgb,[False],np,image_type,work,Mock(side_effect=error))
                self.assertFalse((work/"result.json").exists())
        rgb,np,image_type=self.output_fixture()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(preview,"png_to_jpeg",return_value=b"x"*(preview.MAX_SCENE_BYTES+1)):
            work=Path(directory)
            with self.assertRaisesRegex(ValueError,"JPEG size"):
                preview.save_unreviewed_output(rgb,[False],np,image_type,work,Mock())
            self.assertFalse((work/"rendered.jpg").exists())

    def test_default_plan_never_downloads_spawns_or_imports_models(self):
        with patch.object(preview,"download") as download,patch.object(preview.subprocess,"Popen") as child, \
             patch.object(preview,"worker") as worker,contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(preview.main([]),0)
        download.assert_not_called();child.assert_not_called();worker.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["publication"],"preview_only")

    def test_controller_import_and_plan_work_with_all_model_frameworks_forbidden(self):
        code="""import builtins
original=builtins.__import__
def guarded(name,*args,**kwargs):
    if name.split('.')[0] in {'torch','diffusers','transformers','numpy','PIL'}:
        raise AssertionError('Framework imported by stdlib controller: '+name)
    return original(name,*args,**kwargs)
builtins.__import__=guarded
from scripts import sdxl_scene_preview
sdxl_scene_preview.main([])
"""
        result=subprocess.run([sys.executable,"-c",code],cwd=preview.ROOT,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_mac_or_wrong_input_stops_before_output_download_or_child(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);env=environment(root)
            for ref in preview.TRUSTED_REFS:
                self.assertEqual(preview.runner_gate(environment(root,ref),system="Linux",python_version=(3,11)),root.resolve())
            for change in ({"GITHUB_REPOSITORY":"someone/fork"},{"SDXL_PUBLIC_REPOSITORY":"false"},
                           {"GITHUB_EVENT_NAME":"pull_request"},{"RUNNER_ENVIRONMENT":"self-hosted"},
                           {"GITHUB_REF":"refs/heads/other"},{"GITHUB_WORKFLOW_REF":"other/file.yml@refs/heads/main"},
                           {"GITHUB_SHA":"main"},{"SCENE_SLUG":"other"},{"SCENE_LIMIT":"2"},
                           {"RETRY_HELD":"0"},{"SCENE_PUBLISH":"1"},{"BACKFILL_SCENE":"new scene"},
                           {"BACKFILL_REFERENCE":"1"},{"PREVIEW_MODEL":"default"}):
                with self.subTest(change=change),self.assertRaises((ValueError,RuntimeError)):
                    preview.runner_gate({**env,**change},system="Linux",python_version=(3,11))
            with self.assertRaises(RuntimeError):preview.runner_gate(env,system="Darwin",python_version=(3,11))
            with patch.object(preview,"runner_gate",side_effect=RuntimeError("not a GitHub CPU runner")), \
                 patch.object(preview,"download") as download,patch.object(preview,"run_owned_worker") as child:
                with self.assertRaises(RuntimeError):preview.execute(root/"output")
                self.assertFalse((root/"output").exists())
            download.assert_not_called();child.assert_not_called()

    def test_cpu_performance_observation_uses_exact_common_flags_without_gating_bf16(self):
        def report(first,second):
            return f"processor: 0\nflags: {first}\n\nprocessor: 1\nflags: {second}\n"
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"cpuinfo"
            for text,expected in ((report("sse avx512_bf16 amx_bf16","amx_bf16 sse"),["amx_bf16"]),
                                  (report("avx512_bf16 sse","sse avx512_bf16"),["avx512_bf16"])):
                path.write_text(text);data=preview.native_bf16_features(path)
                self.assertEqual(data,{"cpu_count":2,"common_native_flags":expected})
            for text in (report("avx512_bf16_extra","avx512_bf16_extra"),
                         report("avx512_bf16","amx_bf16"),report("avx2","avx2")):
                path.write_text(text)
                self.assertEqual(preview.native_bf16_features(path)["common_native_flags"],[])
            for text in ("", "processor: 0\n", report("avx512_bf16","avx512_bf16").replace("processor: 1","processor: 0")):
                path.write_text(text)
                self.assertIn("observation_error",preview.native_bf16_features(path))

    def test_non_native_cpu_can_prepare_bf16_preview_without_importing_or_downloading_models(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);output=root/"preview"
            fake_budget=SimpleNamespace(check=lambda:None,close=lambda:None,max_rss=0,minimum_available=2*1024**3)
            with patch.dict(os.environ,environment(root)),patch.object(preview,"runner_gate",return_value=root.resolve()), \
                 patch.object(preview.RemoteStopGate,"check"), \
                 patch.object(preview,"native_bf16_features",return_value={"cpu_count":4,"common_native_flags":[]}), \
                 patch.object(preview,"check_daily_quota",return_value={"allowed":True}) as quota, \
                 patch.object(preview,"check_disk_reservation"),patch.object(preview,"initial_priority_gate"), \
                 patch.object(preview,"fresh_source",return_value="a"*40), \
                 patch.object(preview.subprocess,"check_output",return_value="a"*40+"\n"), \
                 patch.object(preview,"Budget",return_value=fake_budget), \
                 patch.object(preview,"download_files") as download,patch.object(preview,"run_owned_worker") as child:
                preview.execute(output,check_only=True)
            quota.assert_called_once();download.assert_not_called();child.assert_not_called()
            record=json.loads((output/"receipt.json").read_text())
            self.assertEqual(record["native_bf16_features"]["common_native_flags"],[])
            self.assertEqual(record["status"],"ready_unexecuted")
            self.assertFalse(record["inference"]["native_bf16_required"])
            self.assertFalse(record["download_started"]);self.assertFalse(record["model_import_started"])

    def test_quota_counts_immutable_legacy_trial_and_existing_story_and_daily_limits(self):
        legacy=dict(preview.LEGACY_SDXL_TRIAL)
        current={"id":123,"created_at":"2026-11-13T17:00:00Z",
                 "display_title":preview.BACKFILL_RUN_PREFIX+preview.SLUG}
        runs=[legacy,current];original=[dict(run) for run in runs]
        with patch.dict(os.environ,{"GITHUB_RUN_ID":"123","GITHUB_RUN_ATTEMPT":"1"}), \
             patch.object(preview.subprocess,"check_output",return_value="inventory") as api, \
             patch.object(preview.json,"loads",return_value={"workflow_runs":runs}):
            with self.assertRaisesRegex(RuntimeError,"same-story"):
                preview.check_daily_quota(now=preview.datetime.fromisoformat("2026-11-13T20:00:00+00:00"))
            self.assertEqual(runs,original)
            # The JST date changes at 15:00 UTC, not at UTC midnight.
            current["created_at"]="2026-11-14T16:00:00Z"
            next_day=preview.datetime.fromisoformat("2026-11-14T17:00:00+00:00")
            evidence=preview.check_daily_quota(now=next_day)
            self.assertEqual(evidence["known_legacy_trial"],{**preview.LEGACY_SDXL_TRIAL,"quota_slug":preview.SLUG})
            self.assertEqual(legacy,preview.LEGACY_SDXL_TRIAL)
            for index in range(4):
                runs.append({"id":200+index,"created_at":f"2026-11-14T15:0{index}:00Z",
                             "display_title":preview.BACKFILL_RUN_PREFIX+f"other-{index}"})
            with self.assertRaisesRegex(RuntimeError,"daily attempt budget"):
                preview.check_daily_quota(now=next_day)
            self.assertEqual(api.call_args.args[0],["gh","api",
                f"repos/{preview.REPOSITORY}/actions/workflows/free-cpu-scenes.yml/runs?per_page=100"])
            self.assertEqual(api.call_args.kwargs["timeout"],15)

    def test_quota_rejects_reruns_unverifiable_legacy_and_missing_current_inventory(self):
        current={"id":123,"created_at":"2026-11-14T16:00:00Z",
                 "display_title":preview.BACKFILL_RUN_PREFIX+preview.SLUG}
        with patch.dict(os.environ,{"GITHUB_RUN_ID":"123","GITHUB_RUN_ATTEMPT":"2"}), \
             patch.object(preview.subprocess,"check_output") as api:
            with self.assertRaisesRegex(RuntimeError,"reruns"):preview.check_daily_quota()
            api.assert_not_called()
        for inventory in ([{**preview.LEGACY_SDXL_TRIAL,"head_sha":"f"*40},current],
                          [{**preview.LEGACY_SDXL_TRIAL,"created_at":"2026-11-14T16:01:26Z"},current],
                          [],[{**current,"display_title":"Free CPU staged-news photos"}]):
            with self.subTest(inventory=inventory), \
                 patch.dict(os.environ,{"GITHUB_RUN_ID":"123","GITHUB_RUN_ATTEMPT":"1"}), \
                 patch.object(preview.subprocess,"check_output",return_value=json.dumps({"workflow_runs":inventory})):
                with self.assertRaises(ValueError):preview.check_daily_quota()

    def test_user_requested_retry_allows_one_new_request_without_erasing_old_global_budget(self):
        legacy={**preview.LEGACY_SDXL_TRIAL,"status":"completed","conclusion":"failure"}
        current={"id":123,"created_at":"2026-11-13T21:01:00Z",
                 "display_title":preview.BACKFILL_RUN_PREFIX+preview.SLUG}
        runs=[legacy,current];original=[dict(run) for run in runs]
        now=preview.datetime.fromisoformat("2026-11-13T21:02:00+00:00")
        with patch.dict(os.environ,{"GITHUB_RUN_ID":"123","GITHUB_RUN_ATTEMPT":"1",
                                   "USER_REQUESTED_RETRY_AFTER":str(legacy["id"])}), \
             patch.object(preview.subprocess,"check_output",return_value="inventory"), \
             patch.object(preview.json,"loads",return_value={"workflow_runs":runs}):
            evidence=preview.check_daily_quota(now=now)
            self.assertEqual(evidence["user_requested_retry_after"],str(legacy["id"]))
            self.assertNotEqual(evidence["known_legacy_trial"]["quota_slug"],preview.SLUG)
            self.assertEqual(evidence["known_legacy_trial"]["conclusion"],"failure")
            self.assertEqual(runs,original)
            with patch.dict(os.environ,{"USER_REQUESTED_RETRY_AFTER":""}), \
                 self.assertRaisesRegex(RuntimeError,"same-story"):
                preview.check_daily_quota(now=now)
            prior={**current,"id":122,"created_at":"2026-11-13T20:59:00Z"}
            runs.append(prior)
            with self.assertRaisesRegex(RuntimeError,"same-story"):preview.check_daily_quota(now=now)
            runs.remove(prior)
            # Old failure + two other stories + this request still occupy four slots.
            for index in range(2):
                runs.append({"id":200+index,"created_at":f"2026-11-13T18:0{index}:00Z",
                             "display_title":preview.BACKFILL_RUN_PREFIX+f"other-{index}"})
            self.assertTrue(preview.check_daily_quota(now=now)["allowed"])
            runs.append({"id":202,"created_at":"2026-11-13T18:02:00Z",
                         "display_title":preview.BACKFILL_RUN_PREFIX+"third-other"})
            with self.assertRaisesRegex(RuntimeError,"daily attempt budget"):preview.check_daily_quota(now=now)
            self.assertEqual(legacy,original[0])

    def test_user_requested_retry_rejects_wrong_id_day_attempt_or_unverified_failure(self):
        legacy={**preview.LEGACY_SDXL_TRIAL,"status":"completed","conclusion":"failure"}
        current={"id":123,"created_at":"2026-11-13T21:01:00Z",
                 "display_title":preview.BACKFILL_RUN_PREFIX+preview.SLUG}
        env={"GITHUB_RUN_ID":"123","GITHUB_RUN_ATTEMPT":"1",
             "USER_REQUESTED_RETRY_AFTER":str(legacy["id"])}
        now=preview.datetime.fromisoformat("2026-11-13T21:02:00+00:00")
        for changes,clock in (({"USER_REQUESTED_RETRY_AFTER":"43458459271"},now),
                              ({},preview.datetime.fromisoformat("2026-11-13T14:59:59+00:00")),
                              ({},preview.datetime.fromisoformat("2026-11-14T15:00:00+00:00")),
                              ({"GITHUB_RUN_ATTEMPT":"2"},now)):
            with self.subTest(changes=changes,clock=clock),patch.dict(os.environ,{**env,**changes}), \
                 patch.object(preview.subprocess,"check_output") as api:
                with self.assertRaises((RuntimeError,ValueError)):preview.check_daily_quota(now=clock)
                api.assert_not_called()
        for inventory in ([current],[legacy,legacy,current],
                          [{**legacy,"status":"in_progress"},current],
                          [{**legacy,"conclusion":"success"},current],
                          [{**legacy,"conclusion":"cancelled"},current],
                          [{**legacy,"head_sha":"b"*40},current]):
            with self.subTest(inventory=inventory),patch.dict(os.environ,env), \
                 patch.object(preview.subprocess,"check_output",return_value=json.dumps({"workflow_runs":inventory})):
                with self.assertRaises(ValueError):preview.check_daily_quota(now=now)

    def test_exhausted_quota_fails_before_any_download_or_model_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);output=root/"preview"
            with patch.dict(os.environ,environment(root)),patch.object(preview,"runner_gate",return_value=root.resolve()), \
                 patch.object(preview.RemoteStopGate,"check"), \
                 patch.object(preview,"native_bf16_features",return_value={"cpu_count":4,"common_native_flags":["avx512_bf16"]}), \
                 patch.object(preview,"check_daily_quota",side_effect=RuntimeError("same-story limit reached")), \
                 patch.object(preview,"download_files") as download,patch.object(preview,"run_owned_worker") as child:
                with self.assertRaisesRegex(RuntimeError,"same-story"):preview.execute(output)
            download.assert_not_called();child.assert_not_called()
            record=json.loads((output/"receipt.json").read_text())
            self.assertEqual(record["status"],"failed")
            self.assertFalse(record["download_started"]);self.assertFalse(record["model_import_started"])

    def test_technical_continuation_keeps_both_prior_attempts_in_global_budget_and_cannot_repeat(self):
        legacy={**preview.LEGACY_SDXL_TRIAL,"status":"completed","conclusion":"failure"}
        metadata=dict(preview.PREFLIGHT_TRIAL)
        current={"id":123,"created_at":"2026-11-13T21:10:00Z",
                 "display_title":preview.BACKFILL_RUN_PREFIX+preview.SLUG}
        runs=[legacy,metadata,current];original=[dict(run) for run in runs]
        now=preview.datetime.fromisoformat("2026-11-13T21:11:00+00:00")
        with patch.dict(os.environ,{"GITHUB_RUN_ID":"123","GITHUB_RUN_ATTEMPT":"1",
                                   "USER_REQUESTED_RETRY_AFTER":str(metadata["id"])}), \
             patch.object(preview.subprocess,"check_output",return_value="inventory"), \
             patch.object(preview.json,"loads",return_value={"workflow_runs":runs}), \
             patch.object(preview,"verify_preflight_trial",return_value={"receipt_sha256":preview.PREFLIGHT_RECEIPT_SHA}) as proof:
            evidence=preview.check_daily_quota(now=now)
            self.assertEqual(runs,original);proof.assert_called_once()
            self.assertEqual(evidence["metadata_only_proof"]["receipt_sha256"],preview.PREFLIGHT_RECEIPT_SHA)
            for prior in (evidence["known_legacy_trial"],evidence["metadata_only_trial"]):
                self.assertNotEqual(prior["quota_slug"],preview.SLUG)
            for old_input in ("",str(legacy["id"])):
                with patch.dict(os.environ,{"USER_REQUESTED_RETRY_AFTER":old_input}), \
                     self.assertRaisesRegex(RuntimeError,"same-story"):preview.check_daily_quota(now=now)
            prior={**current,"id":122,"created_at":"2026-11-13T21:09:00Z"};runs.append(prior)
            with self.assertRaisesRegex(RuntimeError,"same-story"):preview.check_daily_quota(now=now)
            runs.remove(prior)
            runs.append({"id":200,"created_at":"2026-11-13T18:00:00Z",
                         "display_title":preview.BACKFILL_RUN_PREFIX+"other-story"})
            self.assertTrue(preview.check_daily_quota(now=now)["allowed"])
            runs.append({"id":201,"created_at":"2026-11-13T19:00:00Z",
                         "display_title":preview.BACKFILL_RUN_PREFIX+"second-other"})
            with self.assertRaisesRegex(RuntimeError,"daily attempt budget"):preview.check_daily_quota(now=now)

    def test_technical_continuation_requires_nine_exact_fields_and_verified_receipt(self):
        legacy={**preview.LEGACY_SDXL_TRIAL,"status":"completed","conclusion":"failure"}
        current={"id":123,"created_at":"2026-11-13T21:10:00Z",
                 "display_title":preview.BACKFILL_RUN_PREFIX+preview.SLUG}
        env={"GITHUB_RUN_ID":"123","GITHUB_RUN_ATTEMPT":"1",
             "USER_REQUESTED_RETRY_AFTER":str(preview.PREFLIGHT_TRIAL["id"])}
        now=preview.datetime.fromisoformat("2026-11-13T21:11:00+00:00")
        for field in preview.PREFLIGHT_TRIAL:
            changed={**preview.PREFLIGHT_TRIAL,field:"changed"}
            with self.subTest(field=field),patch.dict(os.environ,env), \
                 patch.object(preview.subprocess,"check_output",return_value=json.dumps({"workflow_runs":[legacy,changed,current]})), \
                 patch.object(preview,"verify_preflight_trial") as proof:
                with self.assertRaises(ValueError):preview.check_daily_quota(now=now)
                proof.assert_not_called()
        with patch.dict(os.environ,env),patch.object(preview.subprocess,"check_output",
                return_value=json.dumps({"workflow_runs":[legacy,preview.PREFLIGHT_TRIAL,current]})), \
             patch.object(preview,"verify_preflight_trial",side_effect=ValueError("receipt mismatch")):
            with self.assertRaisesRegex(ValueError,"receipt mismatch"):preview.check_daily_quota(now=now)

    def test_metadata_proof_checks_both_hashes_and_rejects_lifecycle_activity_or_extra_jpeg(self):
        fields=("download_started","models_downloaded","model_import_started",
                "model_execution_started","model_generation_completed")
        receipt={"run_id":str(preview.PREFLIGHT_TRIAL["id"]),"status":"failed","publication":"preview_only",
                 "article_slug":preview.SLUG,"source_article_sha256":preview.SOURCE_SHA,
                 "model":preview.model.MODEL,"model_revision":preview.model.REVISION,"verified_files":{},
                 **{key:False for key in fields}}
        def packed(value,extra=False):
            raw=json.dumps(value).encode();output=io.BytesIO()
            with preview.ZipFile(output,"w") as archive:
                archive.writestr("receipt.json",raw)
                if extra:archive.writestr("candidate.jpg",b"not an allowed image")
            return raw,output.getvalue()
        def validate(value,*,extra=False,wrong_inner=False,wrong_zip=False):
            raw,data=packed(value,extra)
            with patch.object(preview,"PREFLIGHT_ZIP_BYTES",len(data)), \
                 patch.object(preview,"PREFLIGHT_ZIP_SHA","0"*64 if wrong_zip else preview.hashlib.sha256(data).hexdigest()), \
                 patch.object(preview,"PREFLIGHT_RECEIPT_BYTES",len(raw)), \
                 patch.object(preview,"PREFLIGHT_RECEIPT_SHA","0"*64 if wrong_inner else preview.hashlib.sha256(raw).hexdigest()):
                return preview.validate_preflight_archive(data)
        self.assertEqual(validate(receipt)["model_lifecycle"],{key:False for key in fields})
        for options in ({"extra":True},{"wrong_inner":True},{"wrong_zip":True}):
            with self.subTest(options=options),self.assertRaises(ValueError):validate(receipt,**options)
        for field in fields:
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,"no model lifecycle"):
                validate({**receipt,field:True})

    def test_metadata_artifact_fetch_is_hosted_only_and_uses_fixed_bounded_endpoint(self):
        with patch.object(preview,"runner_gate",side_effect=RuntimeError("not hosted")), \
             patch.object(preview,"binary_command") as fetch:
            with self.assertRaisesRegex(RuntimeError,"not hosted"):preview.verify_preflight_trial()
            fetch.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            def fixture(argv,target,budget,**options):target.write_bytes(b"bounded metadata fixture")
            with patch.object(preview,"runner_gate",return_value=Path(directory)), \
                 patch.object(preview,"binary_command",side_effect=fixture) as fetch, \
                 patch.object(preview,"validate_preflight_archive",return_value={"verified":True}) as verify:
                self.assertEqual(preview.verify_preflight_trial(),{"verified":True})
            self.assertEqual(fetch.call_args.args[0],["gh","api",
                f"repos/{preview.REPOSITORY}/actions/artifacts/{preview.PREFLIGHT_ARTIFACT_ID}/zip"])
            self.assertEqual(fetch.call_args.kwargs["max_bytes"],1531)
            verify.assert_called_once_with(b"bounded metadata fixture")

    def test_final_face_revision_keeps_three_prior_attempts_and_allows_only_fourth_slot(self):
        legacy={**preview.LEGACY_SDXL_TRIAL,"status":"completed","conclusion":"failure"}
        current={"id":123,"created_at":"2026-11-13T21:40:00Z",
                 "display_title":preview.BACKFILL_RUN_PREFIX+preview.SLUG}
        runs=[legacy,dict(preview.PREFLIGHT_TRIAL),dict(preview.REJECTED_TRIAL),current]
        original=[dict(run) for run in runs]
        now=preview.datetime.fromisoformat("2026-11-13T21:41:00+00:00")
        env={"GITHUB_RUN_ID":"123","GITHUB_RUN_ATTEMPT":"1",
             "USER_REQUESTED_RETRY_AFTER":str(preview.REJECTED_TRIAL["id"])}
        with patch.dict(os.environ,env),patch.object(preview.subprocess,"check_output",return_value="inventory"), \
             patch.object(preview.json,"loads",return_value={"workflow_runs":runs}), \
             patch.object(preview,"verify_preflight_trial",return_value={"verified":True}) as preflight, \
             patch.object(preview,"verify_rejected_trial",return_value={"decision":"user_rejected_quality"}) as rejected:
            evidence=preview.check_daily_quota(now=now)
            self.assertEqual(runs,original);preflight.assert_called_once();rejected.assert_called_once()
            for key in ("known_legacy_trial","metadata_only_trial","user_rejected_trial"):
                self.assertNotEqual(evidence[key]["quota_slug"],preview.SLUG)
            for old_input in ("",str(legacy["id"]),str(preview.PREFLIGHT_TRIAL["id"])):
                with patch.dict(os.environ,{"USER_REQUESTED_RETRY_AFTER":old_input}),self.assertRaises(RuntimeError):
                    preview.check_daily_quota(now=now)
            for title in (preview.SLUG,"another-story"):
                extra={"id":122,"created_at":"2026-11-13T21:35:00Z",
                       "display_title":preview.BACKFILL_RUN_PREFIX+title}
                runs.append(extra)
                with self.assertRaisesRegex(RuntimeError,"daily attempt budget"):preview.check_daily_quota(now=now)
                runs.remove(extra)
            for changes,clock in (({"GITHUB_RUN_ATTEMPT":"2"},now),
                                  ({},preview.datetime.fromisoformat("2026-11-14T15:00:00+00:00"))):
                with patch.dict(os.environ,{**env,**changes}),self.assertRaises((RuntimeError,ValueError)):
                    preview.check_daily_quota(now=clock)

    def test_face_revision_requires_exact_successful_predecessor_and_pinned_quality_proof(self):
        legacy={**preview.LEGACY_SDXL_TRIAL,"status":"completed","conclusion":"failure"}
        current={"id":123,"created_at":"2026-11-13T21:40:00Z",
                 "display_title":preview.BACKFILL_RUN_PREFIX+preview.SLUG}
        env={"GITHUB_RUN_ID":"123","GITHUB_RUN_ATTEMPT":"1",
             "USER_REQUESTED_RETRY_AFTER":str(preview.REJECTED_TRIAL["id"])}
        now=preview.datetime.fromisoformat("2026-11-13T21:41:00+00:00")
        for field in preview.REJECTED_TRIAL:
            inventory=[legacy,preview.PREFLIGHT_TRIAL,{**preview.REJECTED_TRIAL,field:"changed"},current]
            with self.subTest(field=field),patch.dict(os.environ,env), \
                 patch.object(preview.subprocess,"check_output",return_value=json.dumps({"workflow_runs":inventory})), \
                 patch.object(preview,"verify_rejected_trial") as proof:
                with self.assertRaises(ValueError):preview.check_daily_quota(now=now)
                proof.assert_not_called()
        with patch.dict(os.environ,env),patch.object(preview.subprocess,"check_output",
                return_value=json.dumps({"workflow_runs":[legacy,preview.PREFLIGHT_TRIAL,preview.REJECTED_TRIAL,current]})), \
             patch.object(preview,"verify_preflight_trial",return_value={"verified":True}), \
             patch.object(preview,"verify_rejected_trial",side_effect=ValueError("predecessor hash mismatch")):
            with self.assertRaisesRegex(ValueError,"hash mismatch"):preview.check_daily_quota(now=now)

    def test_fixed_save_loss_proof_alone_allows_fifth_and_never_sixth_or_default_override(self):
        legacy={**preview.LEGACY_SDXL_TRIAL,"status":"completed","conclusion":"failure"}
        current={"id":123,"created_at":"2026-11-13T22:10:00Z",
                 "display_title":preview.BACKFILL_RUN_PREFIX+preview.SLUG}
        runs=[legacy,dict(preview.PREFLIGHT_TRIAL),dict(preview.REJECTED_TRIAL),dict(preview.SAVE_LOSS_TRIAL),current]
        original=[dict(run) for run in runs];now=preview.datetime.fromisoformat("2026-11-13T22:11:00+00:00")
        env={"GITHUB_RUN_ID":"123","GITHUB_RUN_ATTEMPT":"1",
             "USER_REQUESTED_RETRY_AFTER":str(preview.SAVE_LOSS_TRIAL["id"])}
        order=[];actual=preview.backfill_run_allowed
        def quota(*args,**options):order.append("limit-"+str(options.get("daily_limit",4)));return actual(*args,**options)
        def proof():order.append("fixed-proof");return {"verified":True}
        with patch.dict(os.environ,env),patch.object(preview.subprocess,"check_output",return_value="inventory"), \
             patch.object(preview.json,"loads",return_value={"workflow_runs":runs}), \
             patch.object(preview,"verify_save_loss_trial",side_effect=proof) as verify, \
             patch.object(preview,"verify_preflight_trial",return_value={"verified":True}), \
             patch.object(preview,"verify_rejected_trial",return_value={"verified":True}), \
             patch.object(preview,"backfill_run_allowed",side_effect=quota):
            self.assertEqual(preview.check_daily_quota(now=now)["daily_limit"],5)
            self.assertEqual(order,["fixed-proof","limit-5"]);self.assertEqual(runs,original)
            for old in ("",str(preview.REJECTED_TRIAL["id"])):
                with patch.dict(os.environ,{"USER_REQUESTED_RETRY_AFTER":old}),self.assertRaises(RuntimeError):
                    preview.check_daily_quota(now=now)
            verify.side_effect=ValueError("unverified save-loss proof")
            with self.assertRaisesRegex(ValueError,"unverified"):preview.check_daily_quota(now=now)
            verify.side_effect=proof
            runs.append({**current,"id":122,"created_at":"2026-11-13T22:09:00Z"})
            with self.assertRaises(RuntimeError):preview.check_daily_quota(now=now)
            with patch.dict(os.environ,{"GITHUB_RUN_ATTEMPT":"2"}),self.assertRaises(RuntimeError):
                preview.check_daily_quota(now=now)
            with self.assertRaises(ValueError):
                preview.check_daily_quota(now=preview.datetime.fromisoformat("2026-11-14T15:00:00+00:00"))
        classified=[{**run,"display_title":preview.BACKFILL_RUN_PREFIX+str(run["id"])} for run in original]
        self.assertFalse(actual(classified,123,current_run_attempt=1,now=now))
        self.assertTrue(actual(classified,123,current_run_attempt=1,now=now,daily_limit=5))
        for limit in (True,3,6,5.0):
            with self.assertRaises(ValueError):actual(classified,123,current_run_attempt=1,now=now,daily_limit=limit)

    def test_save_loss_proof_is_exact_quality_failure_without_any_jpeg(self):
        article=preview.candidate_article([self.source()])
        receipt={"run_id":str(preview.SAVE_LOSS_TRIAL["id"]),"status":"failed","publication":"preview_only",
                 "source_article_sha256":preview.SOURCE_SHA,"candidate_article_sha256":preview.article_fingerprint(article),
                 "prompt":preview.PROMPT,"prompt_2":preview.PROMPT_2,"inference":preview.model.INFERENCE,
                 "seed":1832143180332967231,"model_generation_completed":True,"failure":"SDXL worker failed with exit 1",
                 "worker":{"stage":"worker_failed","failure_type":"ValueError","failure":preview.QUALITY_FAILURE,
                           "model_generation_completed":True,"safety_flags":[False],"safety_checked":False}}
        def validate(value,extra=False):
            files={"receipt.json":json.dumps(value).encode(),"articles.json":json.dumps([article]).encode()}
            stream=io.BytesIO()
            with preview.ZipFile(stream,"w") as archive:
                for name,raw in files.items():archive.writestr(name,raw)
                if extra:archive.writestr("candidate.jpg",b"not allowed")
            data=stream.getvalue();specs={name:(len(raw),preview.hashlib.sha256(raw).hexdigest()) for name,raw in files.items()}
            with patch.object(preview,"SAVE_LOSS_FILES",specs),patch.object(preview,"SAVE_LOSS_ZIP_BYTES",len(data)), \
                 patch.object(preview,"SAVE_LOSS_ZIP_SHA",preview.hashlib.sha256(data).hexdigest()):
                return preview.validate_save_loss_archive(data)
        self.assertEqual(validate(receipt)["failure"],preview.QUALITY_FAILURE)
        for changed in ({**receipt,"worker":{**receipt["worker"],"failure":"different exception"}},
                        {**receipt,"worker":{**receipt["worker"],"safety_flags":[True]}},
                        {**receipt,"model_generation_completed":False},{**receipt,"seed":5839915426731334261}):
            with self.assertRaises(ValueError):validate(changed)
        with self.assertRaisesRegex(ValueError,"no JPEG"):validate(receipt,extra=True)
    def test_rejected_predecessor_proof_binds_original_candidate_jpeg_receipt_and_safety(self):
        with self.assertRaisesRegex(ValueError,"pinned ZIP"):
            preview.validate_rejected_archive(b"0"*preview.REJECTED_ZIP_BYTES)
        article={**self.source(),"scene_prompt":"Previously generated adult pool scene"}
        candidate_sha=preview.article_fingerprint(article);jpeg=jpeg_fixture()
        receipt={"run_id":str(preview.REJECTED_TRIAL["id"]),"status":"generated_unreviewed",
                 "publication":"preview_only","article_slug":preview.SLUG,"source_article_sha256":preview.SOURCE_SHA,
                 "candidate_article_sha256":candidate_sha,"seed":preview.stable_seed(article),
                 "model":preview.model.MODEL,"model_revision":preview.model.REVISION,
                 **{key:True for key in ("download_started","models_downloaded","model_import_started",
                                        "model_execution_started","model_generation_completed")},
                 "worker":{"model_generation_completed":True,"safety_checked":True,"safety_flags":[False]},
                 "jpeg_sha256":preview.hashlib.sha256(jpeg).hexdigest(),"jpeg_bytes":len(jpeg)}
        def validate(value,*,bad_file=False):
            files={"receipt.json":json.dumps(value).encode(),"articles.json":json.dumps([article]).encode(),preview.JPEG:jpeg}
            specs={name:(len(raw),preview.hashlib.sha256(raw).hexdigest()) for name,raw in files.items()}
            if bad_file:specs[preview.JPEG]=(len(jpeg),"0"*64)
            stream=io.BytesIO()
            with preview.ZipFile(stream,"w") as archive:
                for name,raw in files.items():archive.writestr(name,raw)
            data=stream.getvalue()
            with patch.object(preview,"REJECTED_CANDIDATE_SHA",candidate_sha),patch.object(preview,"REJECTED_FILES",specs), \
                 patch.object(preview,"REJECTED_ZIP_BYTES",len(data)), \
                 patch.object(preview,"REJECTED_ZIP_SHA",preview.hashlib.sha256(data).hexdigest()):
                return preview.validate_rejected_archive(data)
        self.assertEqual(validate(receipt)["decision"],"user_rejected_quality")
        for changed in ({**receipt,"candidate_article_sha256":"0"*64},{**receipt,"model_generation_completed":False},
                        {**receipt,"jpeg_sha256":"0"*64},{**receipt,"worker":{**receipt["worker"],"safety_flags":[True]}}):
            with self.assertRaisesRegex(ValueError,"matching candidate"):validate(changed)
        with self.assertRaisesRegex(ValueError,"file SHA mismatch"):validate(receipt,bad_file=True)

    def test_candidate_changes_only_scene_keeps_body_and_has_new_fingerprint(self):
        source=self.source();candidate=preview.candidate_article([source])
        self.assertEqual({k:v for k,v in candidate.items() if k!="scene_prompt"},
                         {k:v for k,v in source.items() if k!="scene_prompt"})
        self.assertNotEqual(preview.article_fingerprint(candidate),preview.SOURCE_SHA)
        self.assertEqual(preview.stable_seed(candidate),preview.stable_seed(source))
        self.assertNotEqual(preview.article_fingerprint(candidate),preview.REJECTED_CANDIDATE_SHA)
        self.assertIn("Head-to-knees",candidate["scene_prompt"])
        self.assertIn("Opaque navy blue bikini top and matching bottoms fully visible",preview.PROMPT)
        self.assertNotIn("shirt",preview.PROMPT);self.assertNotIn("portrait",candidate["scene_prompt"].lower())
        self.assertIn("white shirt draped only on arms",preview.PROMPT_2)
        self.assertIn("Candid photograph, natural light",preview.PROMPT)
        self.assertIn("slight turn toward camera",preview.PROMPT)
        self.assertIn("Three-quarter front view",preview.PROMPT_2)
        self.assertNotIn("turning back",candidate["scene_prompt"])
        for trait in ("rounded face","warm round eyes","small nose","dark brown bob with bangs","closed-mouth smile"):
            self.assertIn(trait,preview.PROMPT_2)
        self.assertTrue(preview.preview_negative_prompt(candidate).startswith(preview.cpu_negative_prompt(candidate)+", "))
        for term in ("outdoor","ocean","horizon","white bikini","cropped hips","nudity","nsfw"):
            self.assertIn(term,preview.preview_negative_prompt(candidate))
        with self.assertRaises(ValueError):preview.candidate_article([{**source,"paragraphs":["edited"]}])

    def test_user_revision_seed_is_deterministic_new_and_does_not_change_legacy_seed(self):
        candidate=preview.candidate_article([self.source()])
        self.assertEqual(preview.stable_seed(candidate),5839915426731334261)
        self.assertEqual(preview.preview_seed(candidate),1832143180332967231)
        self.assertEqual(preview.preview_seed(candidate),preview.preview_seed(dict(candidate)))
        self.assertNotEqual(preview.preview_seed(candidate),preview.stable_seed(candidate))
        with patch.object(preview,"USER_REVISION_ID","a different explicit user revision"):
            self.assertNotEqual(preview.preview_seed(candidate),1832143180332967231)
        self.assertEqual(preview.model.INFERENCE["user_revision_id"],preview.USER_REVISION_ID)
        self.assertEqual(preview.model.INFERENCE["seed_scheme"],preview.SEED_SCHEME)
        self.assertEqual(preview.model.INFERENCE["prompt_version"],2)

    def test_latest_main_adopted_photo_blocks_another_trial(self):
        preview.reject_adopted_photo({}, {"reviews":{preview.SLUG:{"decision":"rejected","article_sha256":preview.SOURCE_SHA}}}, {})
        for manifest,reviews,approved in (
                ({},{"reviews":{preview.SLUG:{"decision":"accepted","article_sha256":preview.SOURCE_SHA}}},{}),
                ({},{},{"approved":{preview.SLUG:{"source":"already reviewed"}}})):
            with self.assertRaisesRegex(ValueError,"adopted photo"):
                preview.reject_adopted_photo(manifest,reviews,approved)

    def test_two_complete_clip_prompts_and_negatives_are_checked_before_generation(self):
        counts=preview.token_counts(SimpleNamespace(encode=lambda *a,**k:list(range(77))),
                                    SimpleNamespace(encode=lambda *a,**k:list(range(77))),"nsfw, nudity")
        self.assertEqual(set(counts),{"prompt","prompt_2","negative_prompt","negative_prompt_2"})
        for index in (0,1):
            tokenizers=[SimpleNamespace(encode=lambda *a,**k:list(range(40))) for _ in range(2)]
            tokenizers[index]=SimpleNamespace(encode=lambda *a,**k:list(range(78)))
            with self.assertRaisesRegex(ValueError,"77-token"):
                preview.token_counts(*tokenizers,"nsfw, nudity")

    def test_low_memory_component_load_is_unet_first_bf16_local_safetensors_only(self):
        calls=[]
        def loader(name):
            def load(path,**options):calls.append((name,options));return name
            return SimpleNamespace(from_pretrained=load)
        classes={name:loader(name) for name in ("unet","text_encoder","text_encoder_2","vae")}
        components=preview.load_components(Path("scratch/base"),classes,"bf16 sentinel")
        self.assertEqual(list(components),["unet","text_encoder","text_encoder_2","vae"])
        for name,options in calls:
            self.assertEqual(options["torch_dtype"],"bf16 sentinel")
            self.assertTrue(options["low_cpu_mem_usage"]);self.assertTrue(options["local_files_only"])
            self.assertTrue(options["use_safetensors"]);self.assertEqual(options["variant"],"fp16")

    def test_safety_missing_flag_nonbool_or_rejected_is_never_accepted(self):
        self.assertTrue(preview.verified_safety_flags([False]))
        for flags in (None,[],[True],[0],[None],[False,False],(False,)):
            with self.subTest(flags=flags):self.assertFalse(preview.verified_safety_flags(flags))

    def test_nonfinite_or_wrong_shape_pixels_cannot_reach_the_jpeg_stage(self):
        class RGB:
            shape=(1,576,1024,3)
            finite=True
            def min(self):return 0
            def max(self):return 1
        np=SimpleNamespace(ndarray=RGB,isfinite=lambda value:SimpleNamespace(all=lambda:value.finite))
        preview.safety_result(RGB(),[False],np)
        nonfinite=RGB();nonfinite.finite=False
        wrongshape=RGB();wrongshape.shape=(2,576,1024,3)
        for image in (None,nonfinite,wrongshape):
            with self.assertRaisesRegex(ValueError,"finite RGB"):
                preview.safety_result(image,[False],np)

    def test_false_checker_flag_cannot_hide_nan_or_zero_clip_projection(self):
        api=tensor_api()
        class Projection:
            def register_forward_hook(self,hook):
                self.hook=hook;self.removed=False
                return SimpleNamespace(remove=lambda:setattr(self,"removed",True))
        class Checker:
            concept_embeds=TensorFixture([[1.,0.],[0.,1.]])
            special_care_embeds=TensorFixture([[1.,1.]])
            concept_embeds_weights=TensorFixture([.2,.3])
            special_care_embeds_weights=TensorFixture([.4])
            def __init__(self,features):self.visual_projection=Projection();self.features=features
            def __call__(self,**kwargs):
                self.visual_projection.hook(None,None,self.features)
                return kwargs["images"],[False]
        pixels=TensorFixture([.2,.3]);rgb=SimpleNamespace(copy=lambda:"synthetic RGB")
        valid=Checker(TensorFixture([[1.,2.]]))
        self.assertEqual(preview.checked_safety_call(valid,pixels,rgb,api),("synthetic RGB",[False]))
        self.assertTrue(valid.visual_projection.removed)
        for features in ([[float("nan"),0.]],[[0.,0.]]):
            checker=Checker(TensorFixture(features))
            with self.assertRaisesRegex(ValueError,"Safety checker"):
                preview.checked_safety_call(checker,pixels,rgb,api)
            self.assertTrue(checker.visual_projection.removed)
        for name in ("concept_embeds","special_care_embeds","concept_embeds_weights","special_care_embeds_weights"):
            checker=Checker(TensorFixture([[1.,2.]]))
            setattr(checker,name,TensorFixture([[float("nan")]]))
            with self.subTest(name=name),self.assertRaises(ValueError):
                preview.checked_safety_call(checker,pixels,rgb,api)
        api.linalg.vector_norm=lambda *a,**k:TensorFixture([float("inf")])
        with self.assertRaisesRegex(ValueError,"norms"):
            preview.require_features(TensorFixture([[1.,2.]]),api,batch=1)

    def test_cpu_bf16_unet_and_embeddings_stay_bf16_until_unet_release_then_f32_decode(self):
        api=tensor_api();events=[];components={}
        class Embedding(TensorFixture):
            def to(self,**kwargs):self_outer.assertEqual(kwargs["dtype"],"bfloat16");return self
        class Component:
            def __init__(self,name):self.name=name;self.dtype="bfloat16";self.config=SimpleNamespace(scaling_factor=.13025)
            def to(self,**kwargs):
                if self.name=="unet":
                    self_outer.fail("BF16 UNet must never be upcast")
                if self.name=="vae":
                    self_outer.assertIsNone(pipe.unet);self_outer.assertNotIn("unet",components)
                events.append(self.name+"_float32");return self
            def decode(self,latents,**kwargs):events.append("decode");return [TensorFixture([1.])]
        class Pipe:
            watermark=None
            def encode_prompt(self,**kwargs):
                events.append("encode");self_outer.assertIn("negative_prompt_2",kwargs)
                return tuple(Embedding([[1.]]) for _ in range(4))
            def __call__(self,**kwargs):
                events.append("denoise")
                self_outer.assertIsNone(pipe.text_encoder);self_outer.assertIsNone(pipe.text_encoder_2)
                self_outer.assertNotIn("text_encoder",components);self_outer.assertNotIn("text_encoder_2",components)
                self_outer.assertEqual(self.unet.dtype,"bfloat16")
                self_outer.assertEqual(kwargs["output_type"],"latent")
                self_outer.assertEqual(kwargs["num_inference_steps"],20)
                self_outer.assertIn("negative_prompt_embeds",kwargs)
                self_outer.assertIn("negative_pooled_prompt_embeds",kwargs)
                return SimpleNamespace(images=TensorFixture([1.],shape=(1,4,72,128)))
        self_outer=self;pipe=Pipe()
        for name in ("unet","text_encoder","text_encoder_2","vae"):
            components[name]=Component(name);setattr(pipe,name,components[name])
        pipe.image_processor=SimpleNamespace(postprocess=lambda *a,**k:"synthetic RGB")
        with tempfile.TemporaryDirectory() as directory:
            result=preview.render_phased(pipe,components,api,"nsfw, nudity",123,Path(directory))
        self.assertEqual(result,"synthetic RGB")
        self.assertEqual(events,["encode","denoise","vae_float32","decode"])
        self.assertEqual(set(components),{"vae"})
        self.assertEqual(preview.model.INFERENCE["unet_compute_dtype"],"bfloat16")
        self.assertEqual(preview.model.INFERENCE["text_encoder_dtype"],"bfloat16")
        self.assertFalse(preview.model.INFERENCE["native_bf16_required"])

    def test_initial_priority_blocks_snapshot_drift_other_pending_or_unreviewed_images(self):
        target=self.source();other={**target,"slug":"other-story"}
        row={"status":"accepted","publication":{"kind":"generated_jpeg"}}
        with patch.object(preview.subprocess,"check_output",return_value=""), \
             patch.object(preview,"pending_articles",return_value=[]) as pending, \
             patch("scripts.visual_review._publication_row",return_value=row), \
             patch("scripts.visual_review._attention",return_value=[]), \
             patch.object(preview,"check_main_unchanged") as current:
            preview.initial_priority_gate("a"*40,[target,other],review_parent=Path("scratch"))
            self.assertEqual(pending.call_args.args[0],[other])
            current.assert_called_once_with("a"*40,review_parent=Path("scratch"))
        for changed,pending_rows,publication in (("content/articles.json",[],row),("",[other],row),
                ("",[],{"status":"pending","publication":{"kind":"editorial_svg"}}),
                ("",[],{"status":"missing","publication":{"kind":"generic_fallback"}})):
            with patch.object(preview.subprocess,"check_output",return_value=changed), \
                 patch.object(preview,"pending_articles",return_value=pending_rows), \
                 patch("scripts.visual_review._publication_row",return_value=publication), \
                 patch("scripts.visual_review._attention",return_value=[]), \
                 patch.object(preview,"check_main_unchanged"):
                with self.assertRaisesRegex(RuntimeError,"priority|rebase"):
                    preview.initial_priority_gate("a"*40,[target,other],review_parent=Path("scratch"))

    def test_priority_lookup_stays_asynchronous_and_changed_main_stops_only_owned_child(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);preview.atomic_json(work/"request.json",{"nonce":"owned"})
            budget=preview.Budget(work,Mock(),revision="a"*40)
            future=Mock();future.done.return_value=False
            pool=Mock();pool.submit.return_value=future
            budget.next_priority_check=0
            with patch.object(preview,"ThreadPoolExecutor",return_value=pool), \
                 patch.object(preview.time,"monotonic",return_value=100):
                budget.priority_check()
            pool.submit.assert_called_once_with(preview.check_main_unchanged,"a"*40,review_parent=work)
            future.result.assert_not_called()
            with patch.object(preview.time,"monotonic",return_value=131):
                with self.assertRaisesRegex(RuntimeError,"within 30 seconds"):budget.priority_check()
            # A docs-only successful lookup is allowed; a priority-input change
            # propagates to the same owned-process stop path as memory failures.
            future.done.return_value=True;future.result.return_value=None
            budget.next_priority_check=200
            with patch.object(preview.time,"monotonic",return_value=110):budget.priority_check()
            changed=Mock();changed.done.return_value=True;changed.result.side_effect=RuntimeError("main advanced")
            budget.priority_future=changed
            child=Mock(pid=123);child.poll.return_value=None
            with patch.object(preview.subprocess,"Popen",return_value=child),patch.object(preview,"stop_child") as stop, \
                 patch.object(preview.shutil,"disk_usage",return_value=SimpleNamespace(free=preview.MIN_FREE+1)), \
                 patch.object(preview,"archive_bytes",return_value=0),patch.object(preview,"mem_available",return_value=2*1024**3), \
                 patch.object(preview,"group_rss",return_value=1024),patch.object(preview.time,"monotonic",return_value=120):
                with self.assertRaisesRegex(RuntimeError,"main advanced"):preview.run_owned_worker(work,budget)
            stop.assert_called_once_with(child)
            self.assertEqual(budget.max_rss,1024)
            budget.close()

    def test_final_priority_boundary_allows_docs_only_but_rejects_new_article_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/"content").mkdir();(root/"docs").mkdir()
            def git(*args):
                return subprocess.check_output(["git",*args],cwd=root,text=True).strip()
            def commit():
                git("add",".");git("-c","user.name=Fixture","-c","user.email=fixture@example.invalid",
                                     "commit","--quiet","-m","priority fixture")
                return git("rev-parse","HEAD")
            git("init","--quiet")
            (root/"content/articles.json").write_text("[]\n");before=commit()
            (root/"docs/note.md").write_text("documentation only\n");docs=commit()
            with patch.object(preview,"ROOT",root):preview.final_priority_gate(before,docs)
            (root/"content/articles.json").write_text('[{"slug":"new-story"}]\n');article=commit()
            with patch.object(preview,"ROOT",root),self.assertRaisesRegex(RuntimeError,"before export"):
                preview.final_priority_gate(before,article)

    def test_resource_limits_and_exact_download_disk_reservation(self):
        preview.resource_limits(preview.MAX_RSS,preview.MIN_AVAILABLE)
        for rss,available in ((preview.MAX_RSS+1,preview.MIN_AVAILABLE),(0,preview.MIN_AVAILABLE-1)):
            with self.assertRaises(RuntimeError):preview.resource_limits(rss,available)
        need=preview.MIN_FREE+preview.model.TOTAL_DOWNLOAD_BYTES+preview.model.SAFETY_BYTES+preview.model.LICENSE_TEXT_BYTES+64*1024**2
        with patch.object(preview.shutil,"disk_usage",return_value=SimpleNamespace(free=need-1)):
            with self.assertRaisesRegex(RuntimeError,"reserve"):
                preview.check_disk_reservation(Path("scratch"))

    def test_proc_monitor_counts_only_owned_group_and_reads_available_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/"meminfo").write_text("MemAvailable: 2048000 kB\n")
            for pid,group,rss in ((100,100,1234),(101,100,5678),(200,200,999999)):
                folder=root/str(pid);folder.mkdir()
                (folder/"stat").write_text(f"{pid} (python worker) S 1 {group} 1 0 0")
                (folder/"status").write_text(f"VmRSS: {rss} kB\n")
            self.assertEqual(preview.group_rss(100,root),(1234+5678)*1024)
            self.assertEqual(preview.mem_available(root),2048000*1024)

    def test_owned_child_is_stopped_on_memory_remote_stop_or_deadline_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);preview.atomic_json(work/"request.json",{"nonce":"owned"})
            for reason in ("13 GiB RSS","CPU stopped","48-minute deadline"):
                child=Mock(pid=123);child.poll.return_value=None
                budget=Mock();budget.check.side_effect=RuntimeError(reason)
                with patch.object(preview.subprocess,"Popen",return_value=child) as spawn, \
                     patch.object(preview,"stop_child") as stop:
                    with self.assertRaisesRegex(RuntimeError,reason):preview.run_owned_worker(work,budget)
                stop.assert_called_once_with(child)
                self.assertTrue(spawn.call_args.kwargs["start_new_session"])
                self.assertEqual(spawn.call_args.kwargs["env"]["HF_HUB_OFFLINE"],"1")

    def test_hash_and_size_specs_are_always_passed_to_bounded_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);receipt={"verified_files":{},"models_downloaded":False}
            calls=[]
            def download(url,target,budget,**options):
                calls.append((url,options));return {"bytes":options["size"],"sha256":options["sha256"]}
            with patch.object(preview,"download",side_effect=download):
                preview.download_files(root,Mock(),receipt,root)
            self.assertTrue(receipt["models_downloaded"])
            self.assertEqual(len(calls),len(preview.model.FILE_SPECS)+len(preview.model.SAFETY_FILE_SPECS)+1)
            self.assertTrue(all(url.startswith("https://huggingface.co/") and len(options["sha256"])==64 for url,options in calls))
            self.assertFalse(any("Realistic_Vision" in url for url,options in calls))
            self.assertEqual(calls[0][0],preview.model.LICENSE_TEXT_URL)
            self.assertEqual(receipt["verified_files"]["license/canonical-license.txt"],preview.model.LICENSE_TEXT_FILE_SPEC)

    def test_selected_photo_finetune_keeps_profile_and_binds_both_license_sources(self):
        data=preview.model
        self.assertIn("juggernaut",data.MODEL.lower())
        self.assertEqual(data.REVISION,"cf419233522daa0b9ea36c3aff98fa2cab1fb0fb")
        self.assertEqual(data.LICENSE_SHA256,data.FILE_SPECS["README.md"]["sha256"])
        self.assertEqual(data.MODEL_CARD_URL,data.LICENSE_URL)
        self.assertEqual(data.LICENSE_TEXT_FILE_SPEC,{"bytes":data.LICENSE_TEXT_BYTES,"sha256":data.LICENSE_TEXT_SHA256})
        self.assertIn(data.LICENSE_TEXT_REVISION,data.LICENSE_TEXT_URL)
        self.assertEqual(data.INFERENCE["num_inference_steps"],20)
        self.assertEqual(data.INFERENCE["guidance_scale"],6.0)
        self.assertEqual(data.INFERENCE["unet_compute_dtype"],"bfloat16")

    def test_pinned_hash_rejects_corrupt_bytes_before_a_model_can_load(self):
        with tempfile.TemporaryDirectory() as directory:
            payload=b"test fixture, never model weights"
            response=io.BytesIO(payload);response.url="https://huggingface.co/test/resolve/"+"a"*40+"/weight"
            with patch.object(bounded_download,"urlopen",return_value=response):
                with self.assertRaisesRegex(ValueError,"hash verification"):
                    preview.download(response.url,Path(directory)/"weight",Mock(),size=len(payload),sha256="0"*64)

    def test_failed_worker_keeps_truthful_started_phase_and_no_candidate_jpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);output=root/"preview";env=environment(root)
            def fail(work,budget):
                request=json.loads((work/"request.json").read_text())
                self.assertEqual(request["seed"],preview.preview_seed(self.source()))
                self.assertEqual(request["user_revision_id"],preview.USER_REVISION_ID)
                self.assertEqual(request["seed_scheme"],preview.SEED_SCHEME)
                preview.progress(work,"generation_started",model_import_started=True,model_execution_started=True)
                raise RuntimeError("worker RSS limit")
            fake_budget=SimpleNamespace(check=lambda:None,close=lambda:None,max_rss=preview.MAX_RSS+1,minimum_available=2*1024**3)
            with patch.dict(os.environ,env),patch.object(preview,"runner_gate",return_value=root.resolve()), \
                 patch.object(preview.RemoteStopGate,"check"),patch.object(preview,"check_disk_reservation"), \
                 patch.object(preview,"native_bf16_features",return_value={"cpu_count":4,"common_native_flags":["avx512_bf16"]}), \
                 patch.object(preview,"check_daily_quota",return_value={"allowed":True}), \
                 patch.object(preview,"initial_priority_gate"), \
                 patch.object(preview,"fresh_source",return_value="a"*40), \
                 patch.object(preview.subprocess,"check_output",return_value="a"*40+"\n"), \
                 patch.object(preview,"Budget",return_value=fake_budget),patch.object(preview,"download_files"), \
                 patch.object(preview,"run_owned_worker",side_effect=fail):
                with self.assertRaisesRegex(RuntimeError,"RSS"):preview.execute(output)
            receipt=json.loads((output/"receipt.json").read_text())
            self.assertEqual(receipt["status"],"failed")
            self.assertTrue(receipt["model_import_started"]);self.assertTrue(receipt["model_execution_started"])
            self.assertFalse(receipt["model_generation_completed"])
            self.assertFalse((output/preview.JPEG).exists())
            self.assertEqual(receipt["seed"],preview.preview_seed(self.source()))

    def test_photo_adopted_during_generation_keeps_new_jpeg_out_of_the_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);output=root/"preview";env=environment(root)
            def generated(work,budget):
                data=jpeg_fixture();(work/"rendered.jpg").write_bytes(data)
                preview.atomic_json(work/"result.json",{"status":"generated_unreviewed","safety_flags":[False],
                    "sha256":preview.hashlib.sha256(data).hexdigest(),"bytes":len(data)})
                preview.progress(work,"worker_succeeded",model_import_started=True,model_execution_started=True,
                                 model_generation_completed=True,safety_flags=[False])
            fake_budget=SimpleNamespace(check=lambda:None,finish_priority=lambda:None,close=lambda:None,
                                        max_rss=10*1024**3,minimum_available=2*1024**3)
            with patch.dict(os.environ,env),patch.object(preview,"runner_gate",return_value=root.resolve()), \
                 patch.object(preview.RemoteStopGate,"check"),patch.object(preview,"check_disk_reservation"), \
                 patch.object(preview,"native_bf16_features",return_value={"cpu_count":4,"common_native_flags":["avx512_bf16"]}), \
                 patch.object(preview,"check_daily_quota",return_value={"allowed":True}), \
                 patch.object(preview,"initial_priority_gate"), \
                 patch.object(preview,"fresh_source",side_effect=["a"*40,ValueError("adopted photo")]), \
                 patch.object(preview.subprocess,"check_output",return_value="a"*40+"\n"), \
                 patch.object(preview,"Budget",return_value=fake_budget),patch.object(preview,"download_files"), \
                 patch.object(preview,"run_owned_worker",side_effect=generated):
                with self.assertRaisesRegex(ValueError,"adopted photo"):preview.execute(output)
            receipt=json.loads((output/"receipt.json").read_text())
            self.assertEqual(receipt["status"],"failed")
            self.assertTrue(receipt["model_generation_completed"])
            self.assertFalse((output/preview.JPEG).exists())

    def test_parent_exports_safe_quality_warning_as_unreviewed_without_accepting(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);output=root/"preview";rgb,np,image_type=self.output_fixture();jpeg=jpeg_fixture()
            def generated(work,budget):
                preview.progress(work,"safety_load",model_import_started=True,model_execution_started=True,
                                 model_generation_completed=True)
                preview.save_unreviewed_output(rgb,[False],np,image_type,work,
                                               Mock(side_effect=ValueError(preview.QUALITY_FAILURE)))
            fake_budget=SimpleNamespace(check=lambda:None,finish_priority=lambda:None,close=lambda:None,
                                        max_rss=10*1024**3,minimum_available=2*1024**3)
            with patch.dict(os.environ,environment(root)),patch.object(preview,"runner_gate",return_value=root.resolve()), \
                 patch.object(preview.RemoteStopGate,"check"),patch.object(preview,"check_disk_reservation"), \
                 patch.object(preview,"native_bf16_features",return_value={"cpu_count":4,"common_native_flags":[]}), \
                 patch.object(preview,"check_daily_quota",return_value={"allowed":True}), \
                 patch.object(preview,"initial_priority_gate"),patch.object(preview,"final_priority_gate"), \
                 patch.object(preview,"fresh_source",return_value="a"*40), \
                 patch.object(preview.subprocess,"check_output",return_value="a"*40+"\n"), \
                 patch.object(preview,"Budget",return_value=fake_budget),patch.object(preview,"download_files"), \
                 patch.object(preview,"png_to_jpeg",return_value=jpeg), \
                 patch.object(preview,"run_owned_worker",side_effect=generated):
                preview.execute(output)
            receipt=json.loads((output/"receipt.json").read_text())
            self.assertEqual(receipt["publication"],"preview_only")
            self.assertEqual(receipt["status"],"generated_unreviewed")
            self.assertEqual(receipt["quality_warning"],preview.QUALITY_FAILURE)
            self.assertEqual(receipt["worker"]["quality_warning"],receipt["quality_warning"])
            self.assertTrue(receipt["worker"]["safety_checked"])
            self.assertEqual((output/preview.JPEG).read_bytes(),jpeg)
            self.assertNotIn("accepted",receipt)


class SdxlWorkflowTests(unittest.TestCase):
    def test_only_exact_manual_inputs_and_trusted_refs_select_readonly_preview(self):
        document=(preview.ROOT/".github/workflows/free-cpu-scenes.yml").read_text()
        block=job(document,"sdxl-preview")
        condition=next(line.strip()[4:] for line in block.splitlines() if line.startswith("    if: "))
        values={"github.repository":preview.REPOSITORY,"github.event.repository.private":False,
                "github.event_name":"workflow_dispatch","github.ref":"refs/heads/main",
                "inputs.preview_model":"sdxl","inputs.slug":preview.SLUG,"inputs.limit":"1",
                "inputs.retry_held":True,"inputs.publish":False,"inputs.backfill_scene":"",
                "inputs.backfill_reference":False}
        self.assertTrue(applies(condition,values))
        self.assertTrue(applies(condition,{**values,"github.ref":"refs/heads/codex/sdxl-preview-20261114"}))
        for change in ({"github.repository":"a/fork"},{"github.event.repository.private":True},
                       {"github.event_name":"pull_request"},{"github.event_name":"schedule"},
                       {"github.ref":"refs/heads/other"},{"inputs.preview_model":"default"},
                       {"inputs.slug":"other"},{"inputs.limit":"2"},{"inputs.retry_held":False},
                       {"inputs.publish":True},{"inputs.backfill_scene":"new"},{"inputs.backfill_reference":True}):
            with self.subTest(change=change):self.assertFalse(applies(condition,{**values,**change}))
        self.assertIn("ref: ${{ github.sha }}",block);self.assertIn("persist-credentials: false",block)
        self.assertIn("contents: read",block);self.assertIn("actions: read",block)
        self.assertIn("      user_requested_retry_after:\n",document)
        self.assertIn("      USER_REQUESTED_RETRY_AFTER: ${{ inputs.user_requested_retry_after || '' }}",block)
        for forbidden in ("contents: write","actions: write","secrets.","git push","publish_cpu_scenes.py"):
            self.assertNotIn(forbidden,block)
        artifact=block.split("name: Keep only the SDXL candidate",1)[1]
        paths=[line.strip() for line in artifact.splitlines() if line.strip().startswith("${{ runner.temp }}")]
        self.assertEqual(len(paths),3);self.assertFalse(any("*" in path for path in paths))
        self.assertIn("retention-days: 1",artifact)
        normal=job(document,"render")
        self.assertNotIn("USER_REQUESTED_RETRY_AFTER",normal)
        normal_condition=next(line.strip()[4:] for line in normal.splitlines() if line.startswith("    if: "))
        self.assertFalse(applies(normal_condition,values))
        self.assertFalse(applies(normal_condition,{**values,"inputs.preview_model":"unknown"}))
        self.assertTrue(applies(normal_condition,{**values,"inputs.preview_model":"default"}))
        self.assertIn("group: first-party-cpu-scenes",document)
        named_as_backfill=document.split("run-name: \"${{ ",1)[1].split(" && format",1)[0]
        for scene,model,expected in (("","default",False),("scene","default",True),
                                     ("","sdxl",True),("","unknown",False)):
            self.assertEqual(applies(named_as_backfill,{"inputs.backfill_scene":scene,"inputs.preview_model":model}),expected)


if __name__=="__main__":unittest.main()
