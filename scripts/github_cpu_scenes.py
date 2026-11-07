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
    from .cpu_photo_model import MODEL, REVISION, INFERENCE, ORIGIN, load_pipeline
    from .generated_photos import ROOT, article_fingerprint, generated_manifest, usable_generated_scene
    from .local_scene_photos import (
        load_articles, pending_articles, png_to_jpeg, scene_description,
        stable_seed, write_generated,
    )
except ImportError:
    from cpu_photo_model import MODEL, REVISION, INFERENCE, ORIGIN, load_pipeline
    from generated_photos import ROOT, article_fingerprint, generated_manifest, usable_generated_scene
    from local_scene_photos import (
        load_articles, pending_articles, png_to_jpeg, scene_description,
        stable_seed, write_generated,
    )

MAX_CPU_BATCH = 3
MIN_RUNNER_FREE_BYTES = 1_000_000_000
CLIP_TOKEN_LIMIT = 77
TV_PILOT_SLUG = "variety-700-guests-only-introductions"
# These compact scenes retain the conspicuous object and action from the
# published scene direction. They avoid spending the small CLIP budget on
# repeated photography adjectives before the article's joke.
CPU_SCENES = {
    "sleep-advice-livestream-until-two-am": (
        "A tired Japanese woman presses a plain concealer stick below one eye "
        "at a desk beside a lit ring light and a smartphone on a tripod. "
        "She wears a plain sweatshirt in a small home studio."
    ),
    "five-star-reviews-of-returned-humidifiers": (
        "A white tabletop humidifier stands half-unpacked amid opened cardboard "
        "return boxes blocking a tiny electronics shop storeroom doorway. "
        "A solemn employee squeezes past the cartons."
    ),
    "midterm-prediction-notebook-full-of-holes": (
        "A battered open paper notebook has a large ragged circular hole "
        "through its page, revealing the dark desk underneath, with a pink "
        "eraser and gray eraser crumbs alongside. Overhead close-up."
    ),
    "sweet-potato-chorus-starts-too-late": (
        "A whole Japanese sweet potato with rough deep-purple skin lies "
        "beside a cut-open sweet potato showing dense golden-yellow flesh "
        "inside its purple peel. Tight documentary food close-up on an ordinary white plate."
    ),
    "historical-lookalike-contest-dog-wins": (
        "A small dog wearing a large winner's ribbon carries a microphone "
        "beside a low podium. Two solemn fictional adults with theatrical "
        "fake beards kneel to coax the dog into a winner's photograph."
    ),
    "stolen-doorbell-returned-by-parcel": (
        "Close-up of ONE upright brass handbell with a long wooden handle on "
        "top and a wide flared metal rim at the bottom. The handbell stands "
        "inside an open plain brown cardboard box."
    ),
    "family-dinner-gacha-243-rejections": (
        "A red gumball vending machine dominates the frame: a transparent "
        "spherical globe filled with colorful plastic capsules, a silver coin "
        "slot and a large turning handle. It stands on a wooden dining table "
        "beside three empty plates."
    ),
    "volleyball-team-lunch-plate-merch": (
        "Casual overhead news photograph of ONE round white ceramic plate with a yellow "
        "measuring tape across it. A blue-yellow volleyball sits beside the "
        "plate, cropped at the frame edge. Ordinary blue surface."
    ),
    "film-village-all-stars-no-extras": (
        "ONE solitary tired film director carrying two shopping bags walks "
        "across a completely empty historical movie street. A tripod camera "
        "stands at the curb pointed at him. Empty sidewalks and empty storefronts; "
        "no other people anywhere in the frame."
    ),
    "sale-discount-savings-receipt-framed": (
        "Close-up still life in a home hallway: ONE black picture frame contains "
        "a very long narrow white cash-register paper strip with one red band. "
        "Sealed cardboard appliance boxes and shopping bags tower around the frame. "
        "No people."
    ),
    "office-face-to-face-day-video-call-echo": (
        "Foreground close-up of ONE large computer monitor clearly showing a "
        "three-by-three video-call grid of fictional coworkers' faces. Behind it "
        "is an otherwise empty Japanese open-plan office with rows of unused desks "
        "and several closed meeting-booth doors."
    ),
}
# These source snapshots bind reviewed compact directions to their article
# versions. A later body or scene edit must use its current direction rather
# than silently repeating an old compact scene with the unchanged stable seed.
CPU_SCENE_SOURCE_HASHES = {
    "sleep-advice-livestream-until-two-am": "e9f39c0ae20625806fcd70f392a8fc966b91b44e9720e03900c40a3b93fbaa54",
    "five-star-reviews-of-returned-humidifiers": "146c413d5edb2298e0d923589c18ae800ed4f5390b4c37a1410a2002155ee963",
    "midterm-prediction-notebook-full-of-holes": "605ceb59eb496cf65bf4927489aaa8a9fc2dc157e82c14a7a29ca2bc720f58bc",
    TV_PILOT_SLUG: "46177d0fec4d9a01ec699d414b6f378a2e10a70f99ca9b96cfe5350c991c159d",
    "sweet-potato-chorus-starts-too-late": "77213239def3618b2c997b3349ba4f95ba036604ebc312f084379698caef5008",
    "historical-lookalike-contest-dog-wins": "0f1b07544adeeed645722c686eb1b770d4ce2bd3172f2fdb6103d65a8f2f6478",
    "stolen-doorbell-returned-by-parcel": "55fd540d2d3a54f47b9efb4187c9008f253dc517f518d491bf6fc4da1c591ae2",
    "family-dinner-gacha-243-rejections": "fb8057560a4f891020ea12c1c12f76249404db37335b5523d319b3c3087d46f8",
    "volleyball-team-lunch-plate-merch": "8e2ae39d15cc90145a733319fafdfc4c31c6c8b1111e8fc7db8d695ae79093c6",
    "film-village-all-stars-no-extras": "2a0c728d342f075fa811a72a0130015cee19e8c6ff7c83c602523d1127132ab3",
    "sale-discount-savings-receipt-framed": "6a13b26639368d2de4db02f73a0f26de8f1c3772ee9e39030ed47939fb3d15b5",
    "office-face-to-face-day-video-call-echo": "634c2087a7f3abd01f3e093a12908177a2463697d675beae374df576b1423a37",
}
CPU_NEGATIVES = {
    "film-village-all-stars-no-extras": (
        "crowd, spectators, background people, pedestrians, multiple people, "
        "busy street, market crowd"
    ),
    "sale-discount-savings-receipt-framed": (
        "people, human figure, poster, collage, bulletin board, wall papers, "
        "magazine cover, flyer"
    ),
}
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


def reviewed_source_matches(article: dict, slug: str) -> bool:
    return article.get("slug") == slug and CPU_SCENE_SOURCE_HASHES.get(slug) == article_fingerprint(article)


def prompt_parts(article: dict) -> tuple[str, list[str]]:
    """Keep a complete primary action; optional sentences are scene context."""
    if reviewed_source_matches(article, TV_PILOT_SLUG):
        # Show the punchline (everyone already left), not anonymous grim crowds.
        return (
            "Candid documentary photograph inside a real Japanese television studio. "
            "One tired host holds a microphone on an ordinary game-show stage, "
            "HUNDREDS OF EMPTY CHAIRS behind him, an unused enormous red game buzzer "
            "on a trolley. Two confused stagehands and scattered confetti under normal "
            "broadcast lighting. Wide news shot, realistic skin and materials, no captions, "
            "logos or words."
        ), []
    if article.get("slug") in CPU_SCENES and reviewed_source_matches(article, article["slug"]):
        return CPU_SCENES[article["slug"]], []
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", scene_description(article)) if s.strip()]
    # A short opening such as "Photorealistic Japanese comedy." is a style
    # instruction, not the visual action that must survive token fitting.
    while len(sentences) > 1 and len(sentences[0].split()) <= 10 and any(
            word in sentences[0].lower() for word in
            ("photoreal", "photograph", "documentary", "press photo", "comedy", "lighting")):
        sentences.pop(0)
    if not sentences:
        raise ValueError("No complete visual scene for CPU generation")
    return sentences[0], sentences[1:]


def wrap_prompt(core: str, context: list[str] | None = None, *, minimal: bool = False) -> str:
    prefix = (
        "Documentary press photo. " if minimal
        else "Candid documentary press photograph, natural available light, realistic textures. "
    )
    return prefix + " ".join([core, *(context or [])]) + " Ordinary real-world materials."


def cpu_prompt(article: dict) -> str:
    """Offline preview of a compact, article-specific primary scene."""
    core, _ = prompt_parts(article)
    # Preserve the reviewed TV-pilot composition while using its documentary rewrite.
    return core if reviewed_source_matches(article, TV_PILOT_SLUG) else wrap_prompt(core)


def cpu_negative_prompt(article: dict) -> str:
    """Short CFG exclusions; native LCM silently ignored the old negative input."""
    slug = article.get("slug")
    extra = CPU_NEGATIVES.get(slug, "") if isinstance(slug, str) and reviewed_source_matches(article, slug) else ""
    core = ("anime, cartoon, illustration, painting, CGI, 3d render, waxy skin, "
            "deformed hands, extra fingers, blurry, written text, logo, "
            "watermark, nudity, nsfw")
    return core + (", " + extra if extra else "")


def compact_core_at_clause_boundaries(core: str, count) -> str:
    """Shorten only trailing comma/semicolon clauses, preserving the primary action."""
    clauses = [part.strip() for part in re.split(r"[,;]\s*", core) if part.strip()]
    if len(clauses) <= 1:
        return core
    kept = [clauses[0]]
    if count(wrap_prompt(kept[0], minimal=True)) > CLIP_TOKEN_LIMIT:
        return core
    for clause in clauses[1:]:
        candidate_core = ", ".join([*kept, clause]).rstrip(".") + "."
        if count(wrap_prompt(candidate_core, minimal=True)) > CLIP_TOKEN_LIMIT:
            break
        kept.append(clause)
    compact = ", ".join(kept).rstrip(".") + "."
    return compact if len(kept) < len(clauses) else core


def fit_cpu_prompt(article: dict, tokenizer) -> str:
    """Fit complete visual clauses without letting CLIP silently truncate the joke."""
    core, context = prompt_parts(article)
    tv_pilot = reviewed_source_matches(article, TV_PILOT_SLUG)
    count = lambda text: len(tokenizer.encode(text, add_special_tokens=True))
    prompt = cpu_prompt(article)
    minimal = False
    if count(prompt) > CLIP_TOKEN_LIMIT and not tv_pilot:
        minimal = True
        prompt = wrap_prompt(core, minimal=True)
    if count(prompt) > CLIP_TOKEN_LIMIT and not tv_pilot:
        compact_core = compact_core_at_clause_boundaries(core, count)
        if compact_core != core:
            core = compact_core
            prompt = wrap_prompt(core, minimal=True)
            print("CPU prompt compacted trailing visual clauses; primary action retained", flush=True)
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
        if not usable_generated_scene(article, root=root) or not isinstance(record, dict) or record.get("origin") not in ("github-cpu-lcm", ORIGIN):
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
    # White ceramics and polished metal can legitimately be almost neutral.
    # Saturation is diagnostic, not a reason to discard a readable product.
    if not (90 <= brightness <= 225 and contrast >= 30):
        raise ValueError("Scene too dark/washed out/unreadable for editorial use")


def render_one(pipe, torch, article: dict) -> bytes:
    seed = stable_seed(article)
    print(f"CPU scene seed: {seed}", flush=True)
    generator = torch.Generator(device="cpu").manual_seed(seed)
    # Full photo-specialist SD1.5 sampling prioritises scene adherence over speed.
    # Publication upscaling to 1200x675 still enforces the 700 KiB limit.
    tokenizer = getattr(pipe, "tokenizer", None)
    prompt = fit_cpu_prompt(article, tokenizer) if tokenizer is not None else cpu_prompt(article)
    negative = cpu_negative_prompt(article)
    if tokenizer is not None and len(tokenizer.encode(negative)) > CLIP_TOKEN_LIMIT:
        raise ValueError("CPU negative prompt exceeds CLIP budget; refusing silent truncation")
    with torch.inference_mode():
        result = pipe(
            prompt=prompt, negative_prompt=negative,
            num_inference_steps=INFERENCE["num_inference_steps"],
            guidance_scale=INFERENCE["guidance_scale"],
            width=INFERENCE["width"], height=INFERENCE["height"],
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
                "model": MODEL, "inference": INFERENCE,
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
                write_generated(article, data, MODEL, root=ROOT, origin=ORIGIN)
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
