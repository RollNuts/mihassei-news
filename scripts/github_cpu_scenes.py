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
import re
import shutil
import sys
import tempfile
from pathlib import Path

try:
    from .generated_photos import ROOT, generated_manifest, usable_generated_scene
    from .local_scene_photos import (
        load_articles, pending_articles, png_to_jpeg, scene_description,
        stable_seed, write_generated,
    )
except ImportError:
    from generated_photos import ROOT, generated_manifest, usable_generated_scene
    from local_scene_photos import (
        load_articles, pending_articles, png_to_jpeg, scene_description,
        stable_seed, write_generated,
    )

MODEL = "SimianLuo/LCM_Dreamshaper_v7"
# Pin the reviewed model snapshot; do not silently pull another model version.
REVISION = "a85df6a8bd976cdd08b4fd8f3b73f229c9e54df5"
MAX_CPU_BATCH = 3
MIN_RUNNER_FREE_BYTES = 1_000_000_000
CLIP_TOKEN_LIMIT = 77
# These compact scenes retain the conspicuous object and action from the
# published scene direction. They avoid spending the small CLIP budget on
# repeated photography adjectives before the article's joke.
CPU_SCENES = {
    "sweet-potato-chorus-starts-too-late": (
        "Whole steamed Japanese sweet potatoes with dark purple skin fill a "
        "plain plate. One is cut open, exposing dense yellow flesh. Behind "
        "them, blurred adults wait with potatoes while a referee holds a whistle."
    ),
    "historical-lookalike-contest-dog-wins": (
        "A small dog wearing a large winner's ribbon carries a microphone "
        "beside a low podium. Two solemn fictional adults with theatrical "
        "fake beards kneel to coax the dog into a winner's photograph."
    ),
    "stolen-doorbell-returned-by-parcel": (
        "One polished brass counter service bell with a round top push button "
        "sits inside an open plain cardboard parcel on a clean counter. "
        "Tight close-up, the bell fills the frame."
    ),
    "family-dinner-gacha-243-rejections": (
        "A bright red toy capsule lottery machine stands on a Japanese "
        "dining table among empty dinner plates and colorful capsules. "
        "An exhausted parent holds a capsule; two adults sit with crossed arms."
    ),
    "volleyball-team-lunch-plate-merch": (
        "A serious team publicist measures a large white dinner plate with "
        "a tape measure. The plate dominates the foreground. Two volleyball "
        "players eat lunch beside an indoor volleyball net."
    ),
}
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


def prompt_parts(article: dict) -> tuple[str, list[str]]:
    """Keep a complete primary action; optional sentences are scene context."""
    if "700人" in article.get("title", ""):
        # Show the punchline (everyone already left), not anonymous grim crowds.
        return (
            "Bright, colorful, sharp photoreal staged Japanese TV comedy scene. "
            "One tired host holds a microphone on a cheerful neon-lit game-show "
            "stage, HUNDREDS OF EMPTY CHAIRS behind him, an unused enormous red "
            "game buzzer on a trolley. Two confused stagehands, confetti and "
            "powerful joyful professional studio lights. Wide shot, fictional "
            "actors, no captions, logos or words."
        ), []
    if article.get("slug") in CPU_SCENES:
        return CPU_SCENES[article["slug"]], []
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", scene_description(article)) if s.strip()]
    # A short opening such as "Photorealistic Japanese comedy." is a style
    # instruction, not the visual action that must survive token fitting.
    while len(sentences) > 1 and len(sentences[0].split()) <= 10 and any(
            word in sentences[0].lower() for word in ("photoreal", "photograph", "comedy", "lighting")):
        sentences.pop(0)
    if not sentences:
        raise ValueError("No complete visual scene for CPU generation")
    return sentences[0], sentences[1:]


def wrap_prompt(core: str, context: list[str] | None = None, *, minimal: bool = False) -> str:
    prefix = "DSLR color photograph. " if minimal else "Contemporary DSLR color photograph in natural daylight. "
    return prefix + " ".join([core, *(context or [])]) + " Plain unmarked props."


def cpu_prompt(article: dict) -> str:
    """Offline preview of a compact, article-specific primary scene."""
    core, _ = prompt_parts(article)
    # Preserve the already-tested 75-token TV pilot, including its lighting.
    return core if "700人" in article.get("title", "") else wrap_prompt(core)


def fit_cpu_prompt(article: dict, tokenizer) -> str:
    """Fit whole scene sentences, never silently truncate the primary action."""
    core, context = prompt_parts(article)
    tv_pilot = "700人" in article.get("title", "")
    count = lambda text: len(tokenizer.encode(text, add_special_tokens=True))
    prompt = cpu_prompt(article)
    minimal = False
    if count(prompt) > CLIP_TOKEN_LIMIT and not tv_pilot:
        minimal = True
        prompt = wrap_prompt(core, minimal=True)
    if count(prompt) > CLIP_TOKEN_LIMIT:
        raise ValueError("Visual core exceeds CLIP budget; refusing truncated staging")
    included = []
    omitted = 0
    for sentence in context:
        candidate = wrap_prompt(core, [*included, sentence], minimal=minimal)
        if count(candidate) <= CLIP_TOKEN_LIMIT:
            included.append(sentence)
            prompt = candidate
        else:
            omitted += 1
    if omitted:
        print(f"CPU prompt omitted {omitted} optional context sentence(s); primary action retained", flush=True)
    print(f"CPU prompt CLIP token count: {count(prompt)}/{CLIP_TOKEN_LIMIT}", flush=True)
    return prompt


def select_articles(articles: list[dict], *, slug: str = "", limit: int = MAX_CPU_BATCH,
                    retry_held: bool = False, root: Path | None = None) -> list[dict]:
    """Shared offline plan; deliberate hold retries cannot replace valid photos."""
    if not 1 <= limit <= MAX_CPU_BATCH:
        raise ValueError("CPU batch must be 1–3 scenes")
    if retry_held and not slug:
        raise ValueError("--retry-held requires one explicit --slug")
    if slug and not any(a.get("slug") == slug for a in articles):
        raise ValueError("Unknown published article slug")
    base = ROOT if root is None else root
    candidates = pending_articles(articles, root=base, slug=slug, force=retry_held, limit=0)
    return [a for a in candidates if not usable_generated_scene(a, root=base)][:limit]


def validate_review_dir(destination: Path, *, root: Path = ROOT) -> None:
    """A review export never overwrites a pre-existing folder or source tree."""
    if destination.is_symlink() or destination.exists():
        raise RuntimeError("Review directory must be new and not a symlink")
    target, source = destination.resolve(), root.resolve()
    if target == source or target in source.parents or source in target.parents:
        raise RuntimeError("Review directory must be outside the source repository")
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise RuntimeError("Review directory needs an existing non-symlink parent")


def export_review_bundle(articles: list[dict], destination: Path, *, root: Path = ROOT) -> None:
    """Export only genuinely saved successes; exact Python integers stay intact."""
    validate_review_dir(destination, root=root)
    if any(path.is_symlink() for path in (root, root / "assets", root / "assets/generated")):
        raise RuntimeError("Review source must not use symlinked generated-image paths")
    records = generated_manifest(root)
    selected = {}
    for article in articles:
        slug = article["slug"]
        record = records.get(slug)
        if not usable_generated_scene(article, root=root) or not isinstance(record, dict) or record.get("origin") != "github-cpu-lcm":
            raise RuntimeError("Unverified CPU image cannot enter a review bundle: " + slug)
        selected[slug] = record
    if not selected:
        return
    stage = Path(tempfile.mkdtemp(prefix=".cpu-review-", dir=destination.parent))
    try:
        output = stage / "assets/generated"
        output.mkdir(parents=True)
        (stage / "content").mkdir()
        for slug in selected:
            shutil.copyfile(root / "assets/generated" / (slug + ".jpg"), output / (slug + ".jpg"))
        (output / "manifest.json").write_text(json.dumps({"version": 1, "images": selected},
                                                        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (stage / "content/articles.json").write_text(json.dumps(articles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # The initial existence check is repeated after work so a new foreign
        # file cannot be replaced by the final directory rename.
        validate_review_dir(destination, root=root)
        os.rename(stage, destination)
        print(f"Saved CPU review bundle: {destination} ({len(selected)} JPEGs)", flush=True)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def exposure_multiplier(brightness: float) -> float:
    """Bounded auto-exposure; don't mask effectively black/unrecoverable images."""
    if brightness < 30:
        raise ValueError("Scene is severely underexposed; cannot recover photo")
    return min(2.4, max(1.0, 125.0 / brightness))


def normalize_exposure(image):
    """Brighten underexposed local CPU stills before the publication quality gate."""
    from PIL import ImageEnhance, ImageStat

    brightness = ImageStat.Stat(image.convert("L")).mean[0]
    factor = exposure_multiplier(brightness)
    if factor > 1.1:
        image = ImageEnhance.Brightness(image).enhance(factor)
        print(f"CPU auto exposure: initial luminance={brightness:.0f}, "
              f"multiplier={factor:.2f}", flush=True)
    return image


def check_quality(image):
    """Reject dark, washed-out, or near-featureless scenes before site publication.

    Cheap image statistics are a minimum visual gate, not a replacement for
    human review of the joke or realistic anatomy.
    """
    from PIL import ImageStat

    gray = ImageStat.Stat(image.convert("L"))
    saturation = ImageStat.Stat(image.convert("HSV")).mean[1]
    brightness, contrast = gray.mean[0], gray.stddev[0]
    print(f"CPU scene quality: luminance={brightness:.0f}, "
          f"contrast={contrast:.0f}, saturation={saturation:.0f}", flush=True)
    if not (90 <= brightness <= 225 and contrast >= 30 and saturation >= 18):
        raise ValueError("Scene too dark/washed out/unreadable for editorial use")


def render_one(pipe, torch, article: dict) -> bytes:
    generator = torch.Generator(device="cpu").manual_seed(stable_seed(article))
    # LCM: eight CPU-only steps and enough native pixels for a recognisable prop.
    # Publication upscaling to 1200x675 still enforces the 700 KiB limit.
    tokenizer = getattr(pipe, "tokenizer", None)
    prompt = fit_cpu_prompt(article, tokenizer) if tokenizer is not None else cpu_prompt(article)
    with torch.inference_mode():
        result = pipe(
            prompt=prompt, num_inference_steps=8,
            guidance_scale=8.0, width=800, height=448,
            generator=generator, output_type="pil",
        )
    if (getattr(result, "nsfw_content_detected", None)
            and any(result.nsfw_content_detected)):
        raise ValueError("Model safety filter rejected this image")
    image = result.images[0] if getattr(result, "images", None) else None
    if image is None or not hasattr(image, "save"):
        raise ValueError("No usable image from CPU generator")
    image = normalize_exposure(image)
    check_quality(image)
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
    parser.add_argument("--retry-held", action="store_true",
                        help="Deliberately retry one held scene; requires --slug and never replaces a valid photo")
    parser.add_argument("--review-dir", type=Path,
                        help="New directory outside the repository for successful JPEGs and exact source records")
    args = parser.parse_args(argv)
    articles = load_articles()
    try:
        selected = select_articles(articles, slug=args.slug, limit=args.limit, retry_held=args.retry_held)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"CPU scenes pending in this batch: {len(selected)}", flush=True)
    if args.dry_run:
        for article in selected:
            print(json.dumps({
                "slug": article["slug"], "scene": scene_description(article), "cpu_prompt": cpu_prompt(article),
                "model": MODEL,
            }, ensure_ascii=False), flush=True)
        return 0
    if not permitted_ci():
        raise RuntimeError("CPU model is limited to opt-in public main GitHub Action runs")
    if not selected:
        return 0
    if args.review_dir:
        validate_review_dir(args.review_dir, root=ROOT)
    pipe, torch = load_pipeline()
    successes = []
    try:
        for index, article in enumerate(selected, 1):
            ensure_disk()
            print(f"[{index}/{len(selected)}] CPU staging: {article['slug']}", flush=True)
            try:
                data = render_one(pipe, torch, article)
                write_generated(article, data, MODEL, root=ROOT, origin="github-cpu-lcm")
                successes.append(article)
                print(f"Saved verified first-party JPEG ({len(data)} bytes)", flush=True)
            except (RuntimeError, ValueError, OSError) as exc:
                # Keep the usual article's SVG; never let a failed image stop news.
                print(f"Skipped scene {article['slug']}: {exc}", file=sys.stderr, flush=True)
    finally:
        if args.review_dir and successes:
            export_review_bundle(successes, args.review_dir, root=ROOT)
    if not successes:
        raise RuntimeError("No scenes passed verification; keeping all current images")
    print(f"Generated {len(successes)}/{len(selected)} verifiable images", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError) as error:
        print("CPU scene fallback stopped safely:", error, file=sys.stderr)
        sys.exit(1)
