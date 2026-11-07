"""Pinned photoreal SD1.5 + LCM-LoRA loader for the free GitHub CPU runner.

Importing this module is offline and uses only the standard library. The
caller must retain the public-runner/zero-cost gate before calling the loader.
Weights are fetched as data, verified, then loaded locally as safetensors.
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path


LEGACY_MODEL = "SimianLuo/LCM_Dreamshaper_v7"
MODEL = "SG161222/Realistic_Vision_V5.1_noVAE"
REVISION = "1e9f017a7b1eaefb63a1900ea6c5953d2739fd21"
INFERENCE = {
    "base_model": MODEL,
    "base_revision": REVISION,
    "lora_model": "latent-consistency/lcm-lora-sdv1-5",
    "lora_revision": "cf2fced511dbe7e26c8d1d397e728fbab875db4b",
    "vae_model": "stabilityai/sd-vae-ft-mse",
    "vae_revision": "31f26fdeee1355a5c34592e401dd41e45d25a493",
    "safety_model": LEGACY_MODEL,
    "safety_revision": "a85df6a8bd976cdd08b4fd8f3b73f229c9e54df5",
    "scheduler": "LCMScheduler",
    "num_inference_steps": 6,
    "guidance_scale": 2.0,
    "width": 800,
    "height": 448,
}

ROOT = Path(__file__).resolve().parents[1]
MIN_RUNNER_FREE_BYTES = 1_000_000_000

# Official repository LFS SHA256s at the exact commits above. Download neither
# the monolithic checkpoint nor the base repo's VAE: its author recommends the
# separately pinned MSE VAE to improve reconstruction and avoid artifacts.
WEIGHT_SHA256 = {
    ("base", "unet/diffusion_pytorch_model.safetensors"):
        "4e0868e8fcae7d4ea8f8cdd3051704b3b47d741dc8e8629552d1a07f6efb8e32",
    ("base", "text_encoder/model.safetensors"):
        "c1ef1f8a6d40562315dbb7a5224876f07fc7a8b46cc0a383c4f5b1227ddfeb58",
    ("vae", "diffusion_pytorch_model.safetensors"):
        "a1d993488569e928462932c8c38a0760b874d166399b14414135bd9c42df5815",
    ("lora", "pytorch_lora_weights.safetensors"):
        "8f90d840e075ff588a58e22c6586e2ae9a6f7922996ee6649a7f01072333afe4",
    ("safety", "safety_checker/model.safetensors"):
        "fb351a5ded815c3ff744968ad9c6b218d071b9d313d04f35e813b84b4c0ffde8",
}
SNAPSHOTS = {
    "base": (MODEL, REVISION, [
        "model_index.json", "scheduler/*.json", "tokenizer/*.json",
        "tokenizer/merges.txt", "text_encoder/config.json",
        "text_encoder/model.safetensors", "unet/config.json",
        "unet/diffusion_pytorch_model.safetensors",
    ]),
    "vae": (INFERENCE["vae_model"], INFERENCE["vae_revision"], [
        "config.json", "diffusion_pytorch_model.safetensors",
    ]),
    "lora": (INFERENCE["lora_model"], INFERENCE["lora_revision"], [
        "pytorch_lora_weights.safetensors",
    ]),
    "safety": (INFERENCE["safety_model"], INFERENCE["safety_revision"], [
        "safety_checker/config.json", "safety_checker/model.safetensors",
        "feature_extractor/preprocessor_config.json",
    ]),
}


def ensure_disk() -> None:
    if shutil.disk_usage(ROOT).free < MIN_RUNNER_FREE_BYTES:
        raise RuntimeError("Runner has less than 1GB free; keeping existing art")


def verify_weight(path: Path, expected: str) -> None:
    """Read through normal HF-cache links, and fail before deserialization."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise ValueError(f"CPU model weight SHA256 mismatch: {path.name}")


def load_pipeline():
    """Load only verified public weights; perform no remote image inference."""
    ensure_disk()
    import torch
    from diffusers import AutoencoderKL, LCMScheduler, StableDiffusionPipeline
    from diffusers.pipelines.stable_diffusion import StableDiffusionSafetyChecker
    from huggingface_hub import snapshot_download
    from transformers import CLIPImageProcessor

    torch.set_num_threads(max(1, min(4, os.cpu_count() or 2)))
    torch.set_num_interop_threads(1)
    snapshots = {}
    for name, (repo, revision, patterns) in SNAPSHOTS.items():
        ensure_disk()
        directory = Path(snapshot_download(
            repo_id=repo, revision=revision, allow_patterns=patterns,
        ))
        ensure_disk()
        for (component, relative), expected in WEIGHT_SHA256.items():
            if component == name:
                verify_weight(directory / relative, expected)
        snapshots[name] = directory

    # Verify every weight before constructing any component. RV's model index
    # has a null checker, so explicitly retain the previously used safety model.
    vae = AutoencoderKL.from_pretrained(
        str(snapshots["vae"]), torch_dtype=torch.float32,
        use_safetensors=True, local_files_only=True,
    )
    checker = StableDiffusionSafetyChecker.from_pretrained(
        str(snapshots["safety"]), subfolder="safety_checker",
        torch_dtype=torch.float32, use_safetensors=True, local_files_only=True,
    )
    extractor = CLIPImageProcessor.from_pretrained(
        str(snapshots["safety"]), subfolder="feature_extractor",
        local_files_only=True,
    )
    if checker is None or extractor is None:
        raise RuntimeError("CPU photo generation requires the existing safety filter")
    pipe = StableDiffusionPipeline.from_pretrained(
        str(snapshots["base"]), vae=vae, safety_checker=checker,
        feature_extractor=extractor, requires_safety_checker=True,
        torch_dtype=torch.float32, use_safetensors=True, local_files_only=True,
    )
    if pipe.safety_checker is None or pipe.unet.config.time_cond_proj_dim is not None:
        raise RuntimeError("CPU photo pipeline must retain safety and CFG negative prompts")
    pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
    pipe.to("cpu")
    pipe.load_lora_weights(
        str(snapshots["lora"]), weight_name="pytorch_lora_weights.safetensors",
        use_safetensors=True, local_files_only=True,
    )
    # Fusing changes the actual base weights. Removing adapter modules afterward
    # preserves those fused weights and avoids keeping a second adapter active.
    pipe.fuse_lora(safe_fusing=True)
    pipe.unload_lora_weights()
    pipe.enable_attention_slicing("auto")
    pipe.set_progress_bar_config(disable=True)
    return pipe, torch
