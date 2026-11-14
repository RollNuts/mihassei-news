"""Offline manual-study gates; no model, network or real generation in these tests."""
import contextlib
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import ssd1b_scene_preview as p

NOW = datetime(2026, 11, 14, 3, tzinfo=timezone.utc)


class StudyTests(unittest.TestCase):
    def source(self):
        return next(a for a in json.loads((p.ROOT / 'content/articles.json').read_text()) if a['slug'] == p.SLUG)

    def env(self, temporary):
        ref = 'refs/heads/codex/ssd1b-single-preview-20261114'
        return dict(GITHUB_ACTIONS='true', GITHUB_REPOSITORY=p.REPOSITORY, GITHUB_EVENT_NAME='workflow_dispatch',
            RUNNER_ENVIRONMENT='github-hosted', RUNNER_OS='Linux', SSD1B_PUBLIC_REPOSITORY='true',
            PREVIEW_MODEL='ssd1b', VERIPSA_ENABLE_FREE_CPU='1', SCENE_SLUG=p.SLUG, SCENE_LIMIT='1',
            RETRY_HELD='1', SCENE_PUBLISH='0', BACKFILL_SCENE='', BACKFILL_REFERENCE='0',
            GITHUB_REF=ref, GITHUB_WORKFLOW_REF=p.REPOSITORY+'/.github/workflows/free-cpu-scenes.yml@'+ref,
            GITHUB_SHA='a'*40, RUNNER_TEMP=temporary)

    def run_record(self, number=1, title=None, **updates):
        return dict(id=number, display_title=title or p.RUN_TITLE, run_attempt=1, event='workflow_dispatch',
                    path='.github/workflows/free-cpu-scenes.yml', head_branch='codex/ssd1b-single-preview-20261114', created_at=NOW.isoformat(), **updates)

    def test_only_exact_public_manual_cpu_inputs_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            env=self.env(directory)
            self.assertEqual(p.runner_gate(env, system='Linux', python_version=(3,11)), Path(directory).resolve())
            for key,value in [('GITHUB_EVENT_NAME','push'),('PREVIEW_MODEL','sdxl'),('SCENE_PUBLISH','1'),
                              ('SCENE_LIMIT','2'),('SSD1B_PUBLIC_REPOSITORY','false'),
                              ('RUNNER_ENVIRONMENT','self-hosted'),('GITHUB_REF','refs/pull/1/head')]:
                with self.subTest(key=key), self.assertRaises(RuntimeError):
                    p.runner_gate({**env,key:value},system='Linux',python_version=(3,11))
            with self.assertRaises(RuntimeError):p.runner_gate(env,system='Darwin',python_version=(3,11))

    def test_mac_guard_fails_before_any_write_download_or_model_import(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(p,'runner_gate',side_effect=RuntimeError('Mac')), \
             patch.object(p,'download_files') as download,patch.object(p,'run_owned_worker') as worker:
            target=Path(directory)/'output'
            with self.assertRaises(RuntimeError):p.execute(target)
            self.assertFalse(target.exists());download.assert_not_called();worker.assert_not_called()

    def test_source_and_seed_are_new_study_without_editing_original(self):
        source=self.source();before=copy.deepcopy(source)
        candidate=p.candidate_article([source])
        self.assertEqual(source,before)
        self.assertEqual(p.article_fingerprint(source),p.SOURCE_SHA)
        self.assertNotEqual(p.article_fingerprint(candidate),p.SOURCE_SHA)
        self.assertIn('terracotta bikini',candidate['scene_prompt'])
        self.assertIn('indoor resort pool',candidate['scene_prompt'])
        self.assertEqual(p.preview_seed(candidate),p.preview_seed(candidate))
        seed=p.preview_seed(candidate)
        with patch.object(p,'USER_REVISION_ID','old-juggernaut-profile'):
            self.assertNotEqual(seed,p.preview_seed(candidate))
        bad=copy.deepcopy(source);bad['title']+='changed'
        with self.assertRaises(ValueError):p.candidate_article([bad])

    def quota_pages(self, current=None):
        rows=[*copy.deepcopy(p.PRIOR_COMPLETED_RUNS),current or self.run_record()]
        return [{'total_count':len(rows),'workflow_runs':rows}]

    def test_date_bound_new_request_keeps_old_history_normal_cap_and_one_new_profile(self):
        pages=self.quota_pages();original=copy.deepcopy(pages)
        proof=p.quota_evidence(pages,1,1,NOW)
        self.assertEqual(pages,original)
        self.assertEqual(proof['daily_limit'],4)
        self.assertEqual(proof['observed_daily_attempts'],6)
        self.assertTrue(proof['normal_daily_cap_exceeded'])
        self.assertEqual(proof['normal_cap_exceeded_by'],2)
        self.assertEqual(proof['new_user_authorization']['additional_study_runs'],1)
        self.assertFalse(proof['legacy_grant_reused'])
        for attempt in (2,3):
            with self.assertRaises(RuntimeError):p.quota_evidence(pages,1,attempt,NOW)
        with self.assertRaises(RuntimeError):p.quota_evidence(pages,1,1,NOW.replace(day=15))
        duplicate=self.run_record(2)
        with self.assertRaises(RuntimeError):p.quota_evidence(self.quota_pages(duplicate),1,1,NOW)
        pages[0]['workflow_runs'].append(self.run_record(3,'unrelated manual trial'))
        with self.assertRaises(RuntimeError):p.quota_evidence(pages,1,1,NOW)
        with patch.dict(p.os.environ,{'USER_REQUESTED_RETRY_AFTER':'43514129860'}), \
             patch.object(p.subprocess,'check_output') as api:
            with self.assertRaises(ValueError):p.check_daily_quota(now=NOW)
            api.assert_not_called()

    def test_all_five_prior_run_fields_and_global_profile_uniqueness_are_required(self):
        for field,value in [('status','in_progress'),('conclusion','success'),('created_at',NOW.isoformat()),
                            ('head_sha','f'*40),('run_attempt',2),('display_title','renamed')]:
            pages=self.quota_pages();pages[0]['workflow_runs'][0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):p.quota_evidence(pages,1,1,NOW)
        pages=self.quota_pages();pages[0]['workflow_runs'].pop(0)
        with self.assertRaises(ValueError):p.quota_evidence(pages,1,1,NOW)
        pages=self.quota_pages();old=self.run_record(2);old['created_at']='2026-11-12T03:00:00Z'
        pages[0]['workflow_runs'].append(old)
        with self.assertRaises(RuntimeError):p.quota_evidence(pages,1,1,NOW)

    def test_quota_must_be_complete_and_current_profile_provenance_correct(self):
        pages=self.quota_pages();pages[0]['total_count']=101
        with self.assertRaises(ValueError):p.quota_evidence(pages,1,1,NOW)
        bad=dict(self.run_record(),event='schedule')
        with self.assertRaises(ValueError):p.quota_evidence(self.quota_pages(bad),1,1,NOW)
        pages=self.quota_pages();pages[0]['workflow_runs'].append(copy.deepcopy(pages[0]['workflow_runs'][0]))
        with self.assertRaises(ValueError):p.quota_evidence(pages,1,1,NOW)

    def test_memory_disk_and_archive_limits_are_not_relaxed(self):
        self.assertEqual((p.MAX_RSS,p.MIN_AVAILABLE,p.MIN_FREE,p.TIMEOUT),(13*1024**3,1024**3,12*1024**3,48*60))
        p.resource_limits(p.MAX_RSS,p.MIN_AVAILABLE)
        for rss,available in ((p.MAX_RSS+1,p.MIN_AVAILABLE),(0,p.MIN_AVAILABLE-1)):
            with self.assertRaises(RuntimeError):p.resource_limits(rss,available)
        needed=p.MIN_FREE+p.model.TOTAL_DOWNLOAD_BYTES+p.model.SAFETY_BYTES+p.model.LICENSE_TEXT_BYTES+64*1024**2
        with patch.object(p.shutil,'disk_usage',return_value=SimpleNamespace(free=needed-1)):
            with self.assertRaises(RuntimeError):p.check_disk_reservation(Path('.'))

    def test_f32_component_loading_is_local_and_has_no_hidden_fp16_variant(self):
        classes={name:Mock() for name in ('unet','vae','text_encoder','text_encoder_2')}
        result=p.load_components(Path('/scratch'),classes,'float32')
        self.assertEqual(set(result),set(classes))
        for cls in classes.values():
            call=cls.from_pretrained.call_args.kwargs
            self.assertEqual(call['torch_dtype'],'float32');self.assertTrue(call['local_files_only'])
            self.assertTrue(call['use_safetensors']);self.assertNotIn('variant',call)

    def test_f32_encoding_noise_decode_order_releases_large_components(self):
        class Tensor:
            shape=(1,4,72,128)
            def to(self,**kwargs):self.dtype=kwargs['dtype'];return self
            def __truediv__(self,value):return self
        finite=SimpleNamespace(all=lambda:True)
        torch=SimpleNamespace(float32='float32',inference_mode=contextlib.nullcontext,device=lambda value:value,
            Generator=lambda **kwargs:SimpleNamespace(manual_seed=lambda value:value),
            is_tensor=lambda value:isinstance(value,Tensor),isfinite=lambda value:finite)
        components={name:object() for name in ('text_encoder','text_encoder_2','unet','vae')}
        owner=self
        class Pipe:
            text_encoder=object();text_encoder_2=object();watermark=None
            unet=SimpleNamespace(dtype='float32')
            def encode_prompt(self,**kwargs):
                owner.assertTrue(kwargs['do_classifier_free_guidance'])
                return [Tensor() for _ in range(4)]
            def __call__(self,**kwargs):
                owner.assertIsNone(self.text_encoder);owner.assertIsNone(self.text_encoder_2)
                owner.assertNotIn('text_encoder',components);owner.assertNotIn('text_encoder_2',components)
                owner.assertEqual(kwargs['num_images_per_prompt'],1)
                owner.assertGreater(kwargs['guidance_scale'],1)
                return SimpleNamespace(images=Tensor())
        pipe=Pipe()
        def decode(*args,**kwargs):
            owner.assertIsNone(pipe.unet);owner.assertNotIn('unet',components)
            return (Tensor(),)
        pipe.vae=SimpleNamespace(to=lambda **kwargs:None,decode=decode,config=SimpleNamespace(scaling_factor=1))
        pipe.image_processor=SimpleNamespace(postprocess=lambda *args,**kwargs:'synthetic-rgb')
        with patch.object(p,'progress') as progress:
            result=p.render_phased(pipe,components,torch,'negative',1,Path('/unused'))
        self.assertEqual(result,'synthetic-rgb')
        self.assertEqual([call.args[1] for call in progress.call_args_list],
            ['prompt_encoding','unet_float32','denoising_started','vae_float32_decode'])

    def test_safety_rejection_and_nonfinite_rgb_never_create_jpeg(self):
        class RGB:
            shape=(1,576,1024,3);finite=True
            def min(self):return 0
            def max(self):return 1
        np=SimpleNamespace(ndarray=RGB,isfinite=lambda value:SimpleNamespace(all=lambda:value.finite))
        for flags,finite in (([True],True),(None,True),([0],True),([False],False)):
            with tempfile.TemporaryDirectory() as directory,patch.object(p,'png_to_jpeg') as encode:
                rgb=RGB();rgb.finite=finite;work=Path(directory)
                with self.assertRaises(ValueError):p.save_unreviewed_output(rgb,flags,np,Mock(),work,Mock())
                encode.assert_not_called();self.assertFalse((work/'rendered.jpg').exists())
                self.assertFalse(json.loads((work/'progress.json').read_text())['safety_checked'])
        self.assertTrue(p.verified_safety_flags([False]))

    def test_worker_failure_stops_only_owned_process(self):
        process=SimpleNamespace(pid=123,poll=lambda:None)
        with tempfile.TemporaryDirectory() as directory,patch.object(p.subprocess,'Popen',return_value=process), \
             patch.object(p,'stop_child') as stop:
            work=Path(directory);(work/'request.json').write_text(json.dumps({'nonce':'owned'}))
            budget=Mock();budget.check.side_effect=RuntimeError('new article/resource stop')
            with self.assertRaises(RuntimeError):p.run_owned_worker(work,budget)
            stop.assert_called_once_with(process)

    def execute_fixture(self, temporary, *, flags=None, worker_error=None, final_error=None):
        original=self.source();jpeg=(p.ROOT/'assets/generated'/p.JPEG).read_bytes()
        root=Path(temporary)/'repo';(root/'content').mkdir(parents=True)
        (root/'content/articles.json').write_text(json.dumps([original]))
        output=Path(temporary)/'review'
        remote=Mock();budget=Mock();budget.max_rss=0;budget.minimum_available=p.MIN_AVAILABLE
        def worker(work,_budget):
            (work/'rendered.jpg').write_bytes(jpeg)
            p.atomic_json(work/'result.json',{'status':'generated_unreviewed',
                'safety_flags':[False] if flags is None else flags,'sha256':p.hashlib.sha256(jpeg).hexdigest(),
                'bytes':len(jpeg),'quality_warning':p.QUALITY_FAILURE})
            if worker_error:raise worker_error
        with patch.object(p,'ROOT',root),patch.object(p,'runner_gate',return_value=Path(temporary).resolve()),              patch.object(p,'RemoteStopGate',return_value=remote),patch.object(p,'Budget',return_value=budget),              patch.object(p,'check_daily_quota',return_value={'allowed':True}),              patch.object(p.subprocess,'check_output',return_value='a'*40),              patch.object(p,'check_disk_reservation'),patch.object(p,'initial_priority_gate'),              patch.object(p,'fresh_source',side_effect=['b'*40,'c'*40]),patch.object(p,'download_files'),              patch.object(p,'run_owned_worker',side_effect=worker),              patch.object(p,'final_priority_gate',side_effect=final_error),              patch.dict(p.os.environ,{'GITHUB_SHA':'a'*40,'GITHUB_RUN_ID':'1','GITHUB_WORKFLOW_REF':'trusted'}):
            try:p.execute(output)
            except (RuntimeError,ValueError):pass
        return output,json.loads((output/'receipt.json').read_text()),jpeg

    def test_safe_result_survives_new_main_as_stale_review_with_quality_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            output,receipt,jpeg=self.execute_fixture(directory,final_error=p.StaleReview('new article'))
            self.assertEqual((output/p.JPEG).read_bytes(),jpeg)
            self.assertEqual((receipt['status'],receipt['freshness']),('held_unreviewed','stale'))
            self.assertFalse(receipt['final_priority_clear'])
            self.assertEqual(receipt['source_article_sha256'],p.SOURCE_SHA)
            self.assertEqual(receipt['quality_warning'],p.QUALITY_FAILURE)

    def test_completed_safe_worker_result_survives_stop_race_but_unsafe_never_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            output,receipt,jpeg=self.execute_fixture(directory,worker_error=RuntimeError('stop after complete result'))
            self.assertEqual((output/p.JPEG).read_bytes(),jpeg)
            self.assertEqual((receipt['status'],receipt['freshness']),('held_unreviewed','unverified'))
        with tempfile.TemporaryDirectory() as directory:
            output,receipt,_=self.execute_fixture(directory,flags=[True])
            self.assertFalse((output/p.JPEG).exists());self.assertEqual(receipt['status'],'failed')

    def test_model_pins_and_manual_workflow_never_publish_or_offer_old_model(self):
        model=p.model
        self.assertEqual(model.MODEL,'segmind/SSD-1B')
        self.assertEqual(model.INFERENCE['unet_compute_dtype'],'float32')
        self.assertEqual(model.INFERENCE['text_encoder_dtype'],'float32')
        self.assertGreater(model.INFERENCE['guidance_scale'],1)
        self.assertEqual(model.SAFETY_MODEL,'SimianLuo/LCM_Dreamshaper_v7')
        self.assertEqual(model.SAFETY_REVISION,'a85df6a8bd976cdd08b4fd8f3b73f229c9e54df5')
        self.assertEqual(model.SAFETY_FILE_SPECS['safety_checker/model.safetensors']['sha256'],
                         'fb351a5ded815c3ff744968ad9c6b218d071b9d313d04f35e813b84b4c0ffde8')
        self.assertEqual(model.INFERENCE['scheduler'],'EulerDiscreteScheduler')
        self.assertFalse(any('fp16' in name for name in model.FILE_SPECS))
        for spec in model.FILE_SPECS.values():
            self.assertIs(type(spec['bytes']),int);self.assertRegex(spec['sha256'],r'^[0-9a-f]{64}$')
        workflow=(p.ROOT/'.github/workflows/free-cpu-scenes.yml').read_text()
        self.assertEqual(workflow.count('\nrun-name:'),1)
        self.assertIn("options: ['default', 'ssd1b']",workflow)
        job=workflow.split('  ssd1b-preview:')[1]
        for text in ("github.event_name == 'workflow_dispatch'",'contents: read','actions: read',
                     'persist-credentials: false','retention-days: 1',"inputs.limit == '1'",'!inputs.publish'):
            self.assertIn(text,job)
        self.assertNotIn('publish_cpu_scenes',job)
        self.assertNotIn('user_requested_retry_after',workflow)


if __name__=='__main__':unittest.main()
