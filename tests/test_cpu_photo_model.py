"""Offline loader tests: no Torch import, model download, or local inference."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import cpu_photo_model as photo


class CpuPhotoModelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = {name: self.root / name for name in photo.SNAPSHOTS}
        self.hashes = {}
        for key in photo.WEIGHT_SHA256:
            component, relative = key
            path = self.paths[component] / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            data = (component + "/" + relative).encode("utf-8")
            path.write_bytes(data)
            self.hashes[key] = hashlib.sha256(data).hexdigest()

        self.torch = SimpleNamespace(
            float32=object(), set_num_threads=Mock(), set_num_interop_threads=Mock(),
        )
        self.vae, self.checker, self.extractor = object(), object(), object()
        self.pipe = Mock()
        self.pipe.safety_checker = self.checker
        self.pipe.unet.config.time_cond_proj_dim = None
        self.pipe.scheduler.config = {"prediction_type": "epsilon"}
        self.scheduler = object()
        self.vae_class = Mock()
        self.vae_class.from_pretrained.return_value = self.vae
        self.checker_class = Mock()
        self.checker_class.from_pretrained.return_value = self.checker
        self.extractor_class = Mock()
        self.extractor_class.from_pretrained.return_value = self.extractor
        self.pipeline_class = Mock()
        self.pipeline_class.from_pretrained.return_value = self.pipe
        self.scheduler_class = Mock()
        self.scheduler_class.from_config.return_value = self.scheduler
        self.download = Mock(side_effect=self.download_snapshot)
        modules = {
            "torch": self.torch,
            "diffusers": SimpleNamespace(
                AutoencoderKL=self.vae_class, DPMSolverMultistepScheduler=self.scheduler_class,
                StableDiffusionPipeline=self.pipeline_class,
            ),
            "diffusers.pipelines": SimpleNamespace(),
            "diffusers.pipelines.stable_diffusion": SimpleNamespace(
                StableDiffusionSafetyChecker=self.checker_class,
            ),
            "transformers": SimpleNamespace(CLIPImageProcessor=self.extractor_class),
            "huggingface_hub": SimpleNamespace(snapshot_download=self.download),
        }
        for patcher in (
            patch.dict("sys.modules", modules),
            patch.object(photo, "ROOT", self.root),
            patch.object(photo, "WEIGHT_SHA256", self.hashes),
            patch.object(photo.shutil, "disk_usage", return_value=SimpleNamespace(free=20_000_000_000)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def download_snapshot(self, *, repo_id, revision, allow_patterns):
        for name, (repo, expected_revision, patterns) in photo.SNAPSHOTS.items():
            if repo == repo_id:
                self.assertEqual(revision, expected_revision)
                self.assertEqual(allow_patterns, patterns)
                return str(self.paths[name])
        self.fail("Unexpected model repository")

    def assert_no_components_loaded(self):
        for component in (self.vae_class, self.checker_class, self.extractor_class, self.pipeline_class):
            component.from_pretrained.assert_not_called()

    def test_verified_photo_pipeline_retains_safety_and_real_cfg(self):
        with patch.object(photo, "verify_weight", wraps=photo.verify_weight) as verify, \
             patch.object(photo.os, "cpu_count", return_value=32):
            # Even the first deserializer must wait for all four weight checks.
            def load_vae(*args, **kwargs):
                self.assertEqual(verify.call_count, 4)
                return self.vae
            self.vae_class.from_pretrained.side_effect = load_vae
            pipe, torch = photo.load_pipeline()
        self.assertIs(pipe, self.pipe)
        self.assertIs(torch, self.torch)
        self.torch.set_num_threads.assert_called_once_with(4)
        self.torch.set_num_interop_threads.assert_called_once_with(1)
        self.assertEqual(verify.call_count, 4)
        base_args, base_kwargs = self.pipeline_class.from_pretrained.call_args
        self.assertEqual(base_args, (str(self.paths["base"]),))
        self.assertIs(base_kwargs["vae"], self.vae)
        self.assertIs(base_kwargs["safety_checker"], self.checker)
        self.assertIs(base_kwargs["feature_extractor"], self.extractor)
        self.assertTrue(base_kwargs["requires_safety_checker"])
        self.assertIs(base_kwargs["torch_dtype"], self.torch.float32)
        self.assertTrue(base_kwargs["use_safetensors"])
        self.assertTrue(base_kwargs["local_files_only"])
        self.scheduler_class.from_config.assert_called_once_with(
            {"prediction_type": "epsilon"}, algorithm_type="dpmsolver++",
            solver_order=2, solver_type="midpoint", use_karras_sigmas=True,
        )
        self.assertIs(self.pipe.scheduler, self.scheduler)
        self.pipe.to.assert_called_once_with("cpu")
        self.pipe.enable_attention_slicing.assert_called_once_with("auto")
        self.pipe.load_lora_weights.assert_not_called()
        self.pipe.fuse_lora.assert_not_called()
        self.pipe.unload_lora_weights.assert_not_called()

    def test_allowlists_download_no_legacy_unet_duplicate_vae_or_pickle(self):
        photo.load_pipeline()
        self.assertEqual(self.download.call_count, 3)
        calls = {call.kwargs["repo_id"]: call.kwargs for call in self.download.call_args_list}
        self.assertNotIn(photo.LCM_INFERENCE["lora_model"], calls)
        base = calls[photo.MODEL]["allow_patterns"]
        self.assertFalse(any("vae/" in pattern for pattern in base))
        legacy = calls[photo.LEGACY_MODEL]["allow_patterns"]
        self.assertTrue(all(pattern.startswith(("safety_checker/", "feature_extractor/")) for pattern in legacy))
        for call in calls.values():
            self.assertEqual(len(call["revision"]), 40)
            self.assertFalse(any(pattern.endswith((".bin", ".ckpt")) for pattern in call["allow_patterns"]))

    def test_corrupt_model_fails_before_loading_or_further_downloads(self):
        (self.paths["base"] / "unet/diffusion_pytorch_model.safetensors").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            photo.load_pipeline()
        self.assertEqual(self.download.call_count, 1)
        self.assert_no_components_loaded()

    def test_corrupt_safety_weights_cannot_be_silently_disabled(self):
        (self.paths["safety"] / "safety_checker/model.safetensors").write_bytes(b"corrupt checker")
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            photo.load_pipeline()
        self.assert_no_components_loaded()

    def test_missing_weight_fails_before_deserialization(self):
        (self.paths["vae"] / "diffusion_pytorch_model.safetensors").unlink()
        with self.assertRaises(FileNotFoundError):
            photo.load_pipeline()
        self.assert_no_components_loaded()

    def test_low_disk_stops_before_runtime_or_model_download(self):
        with patch.object(photo.shutil, "disk_usage", return_value=SimpleNamespace(free=999_999_999)):
            with self.assertRaisesRegex(RuntimeError, "less than 1GB"):
                photo.load_pipeline()
        self.download.assert_not_called()
        self.torch.set_num_threads.assert_not_called()
        self.assert_no_components_loaded()

    def test_disk_is_rechecked_after_each_snapshot(self):
        usage = SimpleNamespace(free=20_000_000_000)
        with patch.object(photo.shutil, "disk_usage", side_effect=[usage, usage, SimpleNamespace(free=0)]):
            with self.assertRaisesRegex(RuntimeError, "less than 1GB"):
                photo.load_pipeline()
        self.assertEqual(self.download.call_count, 1)
        self.assert_no_components_loaded()

    def test_missing_safety_component_aborts_before_base_load(self):
        self.checker_class.from_pretrained.return_value = None
        with self.assertRaisesRegex(RuntimeError, "requires the existing safety filter"):
            photo.load_pipeline()
        self.pipeline_class.from_pretrained.assert_not_called()

    def test_native_lcm_unet_cannot_disable_cfg_negatives_in_photo_route(self):
        self.pipe.unet.config.time_cond_proj_dim = 256
        with self.assertRaisesRegex(RuntimeError, "CFG negative prompts"):
            photo.load_pipeline()
        self.pipe.load_lora_weights.assert_not_called()


if __name__ == "__main__":
    unittest.main()
