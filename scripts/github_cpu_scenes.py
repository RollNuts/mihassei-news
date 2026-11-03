#!/usr/bin/env python3
"""Optional no-API, no-GPU image generation on GitHub's PUBLIC standard CPU runner.

Does not generate images during ordinary site builds. Downloading public model
WEIGHTS is not calling a hosted text-to-image inference API. No credentials,
paid services, GPU instances, or additional ChatGPT credits are used.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
from pathlib import Path

try:
    from .generated_photos import ROOT
    from .local_scene_photos import (
        load_articles, pending_articles, png_to_jpeg, scene_description,
        scene_prompt, stable_seed, write_generated,
    )
except ImportError:
    from generated_photos import ROOT
    from local_scene_photos import (
        load_articles, pending_articles, png_to_jpeg, scene_description,
        scene_prompt, stable_seed, write_generated,
    )

MODEL = "SimianLuo/LCM_Dreamshaper_v7"
# Pin the reviewed model snapshot; do not silently pull another model version.
REVISION = "a85df6a8bd976cdd08b4fd8f3b73f229c9e54df5"
MAX_CPU_BATCH = 3
MIN_RUNNER_FREE_BYTES = 1_000_000_000
FILES = [
    "model_index.json", "scheduler/*.json", "tokenizer/*",
    "text_encoder/*.json", "text_encoder/*.safetensors",
    "unet/*.json", "unet/*.safetensors",
    "vae/*.json", "vae/*.safetensors",
    "feature_extractor/*",
    "safety_checker/*.json", "safety_checker/*.safetensors",
]


def permitted_ci() -> bool:
    """Never run a heavy model on a fork, PR, private repo or non-CPU worker."""
    return all((
        os.getenv("GITHUB_ACTIONS") == "true",
        os.getenv("GITHUB_REPOSITORY") == "RollNuts/mihassei-news",
        os.getenv("GITHUB_REF") == "refs/heads/main",
        os.getenv("RUNNER_OS") == "Linux",
        os.getenv("VERIPSA_ENABLE_FREE_CPU") == "1",
        os.getenv("GITHUB_EVENT_NAME") in ("schedule", "workflow_dispatch", "push"),
    ))


def ensure_disk() -> None:
    if shutil.disk_usage(ROOT).free < MIN_RUNNER_FREE_BYTES:
        raise RuntimeError("Runner has less than 1GB free; keeping existing art")


def load_pipeline():
    """Load local weights on CPU; never use HF remote inference/Spaces APIs."""
    import torch
    from diffusers import DiffusionPipeline
    from huggingface_hub import snapshot_download

    torch.set_num_threads(max(1, min(4, os.cpu_count() or 2)))
    torch.set_num_interop_threads(1)
    ensure_disk()
    local_model = snapshot_download(repo_id=MODEL, revision=REVISION,
                                    allow_patterns=FILES)
    ensure_disk()
    pipe = DiffusionPipeline.from_pretrained(
        local_model, torch_dtype=torch.float32, use_safetensors=True,
        local_files_only=True,
    )
    pipe.to("cpu")
    pipe.set_progress_bar_config(disable=True)
    return pipe, torch


def render_one(pipe, torch, article: dict) -> bytes:
    generator = torch.Generator(device="cpu").manual_seed(stable_seed(article))
    # LCM: 4 inference steps; modest pixels fit the free 4-CPU/16GB runner.
    # Upscaling to the publication's standard 1200x675 happens locally.
    with torch.inference_mode():
        result = pipe(
            prompt=scene_prompt(article), num_inference_steps=4,
            guidance_scale=8.0, width=704, height=400,
            generator=generator, output_type="pil",
        )
    if (getattr(result, "nsfw_content_detected", None)
            and any(result.nsfw_content_detected)):
        raise ValueError("Model safety filter rejected this image")
    image = result.images[0] if getattr(result, "images", None) else None
    if image is None or not hasattr(image, "save"):
        raise ValueError("No usable image from CPU generator")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return png_to_jpeg(output.getvalue())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate staged fictional news photos using a free public GitHub CPU runner"
    )
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true",
                        help="Purely offline pending/scene preview; no downloads or writes")
    parser.add_argument("--slug", default="", help="Optional exact published story slug")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= MAX_CPU_BATCH:
        parser.error("CPU batch must be 1–3 scenes")
    articles = load_articles()
    if args.slug and not any(x.get("slug") == args.slug for x in articles):
        parser.error("Unknown published article slug")
    selected = pending_articles(articles, slug=args.slug, limit=args.limit)
    print(f"CPU scenes pending in this batch: {len(selected)}", flush=True)
    if args.dry_run:
        for article in selected:
            print(json.dumps({
                "slug": article["slug"], "scene": scene_description(article),
                "model": MODEL,
            }, ensure_ascii=False), flush=True)
        return 0
    if not permitted_ci():
        raise RuntimeError("CPU model is limited to opt-in public main GitHub Action runs")
    if not selected:
        return 0
    pipe, torch = load_pipeline()
    successes = 0
    for index, article in enumerate(selected, 1):
        ensure_disk()
        print(f"[{index}/{len(selected)}] CPU staging: {article['slug']}", flush=True)
        try:
            data = render_one(pipe, torch, article)
            write_generated(article, data, MODEL, origin="github-cpu-lcm")
            successes += 1
            print(f"Saved verified first-party JPEG ({len(data)} bytes)", flush=True)
        except (RuntimeError, ValueError, OSError) as exc:
            # Keep the usual article's SVG; never let a failed image stop news.
            print(f"Skipped scene {article['slug']}: {exc}", file=sys.stderr, flush=True)
    if not successes:
        raise RuntimeError("No scenes passed verification; keeping all current images")
    print(f"Generated {successes}/{len(selected)} verifiable images", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError) as error:
        print("CPU scene fallback stopped safely:", error, file=sys.stderr)
        sys.exit(1)
