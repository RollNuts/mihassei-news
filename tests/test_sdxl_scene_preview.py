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
            "SDXL_PUBLIC_REPOSITORY":"true", "PREVIEW_MODEL":"sdxl", "VERIPSA_ENABLE_FREE_CPU":"1",
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
        float32="float32",device=lambda name:name,inference_mode=contextlib.nullcontext,
        Generator=lambda **kwargs:SimpleNamespace(manual_seed=lambda seed:seed))


class SdxlPreviewTests(unittest.TestCase):
    def source(self):
        return next(a for a in json.loads((preview.ROOT / "content/articles.json").read_text()) if a["slug"]==preview.SLUG)

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

    def test_candidate_changes_only_scene_keeps_body_and_has_new_fingerprint(self):
        source=self.source();candidate=preview.candidate_article([source])
        self.assertEqual({k:v for k,v in candidate.items() if k!="scene_prompt"},
                         {k:v for k,v in source.items() if k!="scene_prompt"})
        self.assertNotEqual(preview.article_fingerprint(candidate),preview.SOURCE_SHA)
        self.assertEqual(preview.stable_seed(candidate),preview.stable_seed(source))
        self.assertIn("Head-to-mid-thigh",candidate["scene_prompt"])
        self.assertIn("Opaque navy bikini",candidate["scene_prompt"])
        self.assertIn("away from bikini top and hips",candidate["scene_prompt"])
        with self.assertRaises(ValueError):preview.candidate_article([{**source,"paragraphs":["edited"]}])

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

    def test_fixed_f32_phase_order_releases_both_encoder_holders_and_unet_before_decode(self):
        api=tensor_api();events=[];components={}
        class Component:
            def __init__(self,name):self.name=name;self.config=SimpleNamespace(scaling_factor=.13025)
            def to(self,**kwargs):
                if self.name=="unet":
                    self_outer.assertIsNone(pipe.text_encoder);self_outer.assertIsNone(pipe.text_encoder_2)
                    self_outer.assertNotIn("text_encoder",components);self_outer.assertNotIn("text_encoder_2",components)
                if self.name=="vae":
                    self_outer.assertIsNone(pipe.unet);self_outer.assertNotIn("unet",components)
                events.append(self.name+"_float32");return self
            def decode(self,latents,**kwargs):events.append("decode");return [TensorFixture([1.])]
        class Pipe:
            watermark=None
            def encode_prompt(self,**kwargs):
                events.append("encode");self_outer.assertIn("negative_prompt_2",kwargs)
                return tuple(TensorFixture([[1.]]) for _ in range(4))
            def __call__(self,**kwargs):
                events.append("denoise")
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
        self.assertEqual(events,["encode","unet_float32","denoise","vae_float32","decode"])
        self.assertEqual(set(components),{"vae"})
        self.assertEqual(preview.model.INFERENCE["unet_compute_dtype"],"float32")
        self.assertEqual(preview.model.INFERENCE["text_encoder_dtype"],"bfloat16")

    def test_resource_limits_and_exact_download_disk_reservation(self):
        preview.resource_limits(preview.MAX_RSS,preview.MIN_AVAILABLE)
        for rss,available in ((preview.MAX_RSS+1,preview.MIN_AVAILABLE),(0,preview.MIN_AVAILABLE-1)):
            with self.assertRaises(RuntimeError):preview.resource_limits(rss,available)
        need=preview.MIN_FREE+preview.model.TOTAL_DOWNLOAD_BYTES+preview.model.SAFETY_BYTES+64*1024**2
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
            self.assertEqual(len(calls),len(preview.model.FILE_SPECS)+len(preview.model.SAFETY_FILE_SPECS))
            self.assertTrue(all(url.startswith("https://huggingface.co/") and len(options["sha256"])==64 for url,options in calls))
            self.assertFalse(any("Realistic_Vision" in url for url,options in calls))

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
                preview.progress(work,"generation_started",model_import_started=True,model_execution_started=True)
                raise RuntimeError("worker RSS limit")
            fake_budget=SimpleNamespace(check=lambda:None,max_rss=preview.MAX_RSS+1,minimum_available=2*1024**3)
            with patch.dict(os.environ,env),patch.object(preview,"runner_gate",return_value=root.resolve()), \
                 patch.object(preview.RemoteStopGate,"check"),patch.object(preview,"check_disk_reservation"), \
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
            self.assertEqual(receipt["seed"],preview.stable_seed(self.source()))

    def test_photo_adopted_during_generation_keeps_new_jpeg_out_of_the_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);output=root/"preview";env=environment(root)
            def generated(work,budget):
                data=jpeg_fixture();(work/"rendered.jpg").write_bytes(data)
                preview.atomic_json(work/"result.json",{"status":"generated_unreviewed","safety_flags":[False],
                    "sha256":preview.hashlib.sha256(data).hexdigest(),"bytes":len(data)})
                preview.progress(work,"worker_succeeded",model_import_started=True,model_execution_started=True,
                                 model_generation_completed=True,safety_flags=[False])
            fake_budget=SimpleNamespace(check=lambda:None,max_rss=10*1024**3,minimum_available=2*1024**3)
            with patch.dict(os.environ,env),patch.object(preview,"runner_gate",return_value=root.resolve()), \
                 patch.object(preview.RemoteStopGate,"check"),patch.object(preview,"check_disk_reservation"), \
                 patch.object(preview,"fresh_source",side_effect=["a"*40,ValueError("adopted photo")]), \
                 patch.object(preview.subprocess,"check_output",return_value="a"*40+"\n"), \
                 patch.object(preview,"Budget",return_value=fake_budget),patch.object(preview,"download_files"), \
                 patch.object(preview,"run_owned_worker",side_effect=generated):
                with self.assertRaisesRegex(ValueError,"adopted photo"):preview.execute(output)
            receipt=json.loads((output/"receipt.json").read_text())
            self.assertEqual(receipt["status"],"failed")
            self.assertTrue(receipt["model_generation_completed"])
            self.assertFalse((output/preview.JPEG).exists())


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
        for forbidden in ("contents: write","actions: write","secrets.","git push","publish_cpu_scenes.py"):
            self.assertNotIn(forbidden,block)
        artifact=block.split("name: Keep only the SDXL candidate",1)[1]
        paths=[line.strip() for line in artifact.splitlines() if line.strip().startswith("${{ runner.temp }}")]
        self.assertEqual(len(paths),3);self.assertFalse(any("*" in path for path in paths))
        self.assertIn("retention-days: 1",artifact)
        normal=job(document,"render")
        normal_condition=next(line.strip()[4:] for line in normal.splitlines() if line.startswith("    if: "))
        self.assertFalse(applies(normal_condition,values))
        self.assertFalse(applies(normal_condition,{**values,"inputs.preview_model":"unknown"}))
        self.assertTrue(applies(normal_condition,{**values,"inputs.preview_model":"default"}))
        self.assertIn("group: first-party-cpu-scenes",document)


if __name__=="__main__":unittest.main()
