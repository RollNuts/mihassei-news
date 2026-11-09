#!/usr/bin/env python3
"""Optional no-API, no-GPU image generation on GitHub's PUBLIC standard CPU runner.

Does not generate images during ordinary site builds. Downloading public model
WEIGHTS is not calling a hosted text-to-image inference API. No credentials,
paid services, GPU instances, or additional ChatGPT credits are used.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from .cpu_photo_model import MODEL, REVISION, INFERENCE, ORIGIN, load_pipeline
    from .cpu_photo_model import (REFERENCE_ORIGIN, REFERENCE_INFERENCE, REFERENCE_RENDERER,
                                  REFERENCE_RENDERER_VERSION, as_reference_pipeline)
    from .generated_photos import ROOT, article_fingerprint, generated_manifest, usable_generated_scene
    from .local_scene_photos import (
        MAX_ARCHIVE_BYTES, MAX_SCENE_BYTES, MIN_FREE_GIB, StorageLimitError,
        archive_bytes, check_storage, reserved_jpeg_bytes,
        held_scene, load_articles, pending_articles, png_to_jpeg, scene_description,
        stable_seed, write_generated,
    )
except ImportError:
    from cpu_photo_model import MODEL, REVISION, INFERENCE, ORIGIN, load_pipeline
    from cpu_photo_model import (REFERENCE_ORIGIN, REFERENCE_INFERENCE, REFERENCE_RENDERER,
                                 REFERENCE_RENDERER_VERSION, as_reference_pipeline)
    from generated_photos import ROOT, article_fingerprint, generated_manifest, usable_generated_scene
    from local_scene_photos import (
        MAX_ARCHIVE_BYTES, MAX_SCENE_BYTES, MIN_FREE_GIB, StorageLimitError,
        archive_bytes, check_storage, reserved_jpeg_bytes,
        held_scene, load_articles, pending_articles, png_to_jpeg, scene_description,
        stable_seed, write_generated,
    )

MAX_CPU_BATCH = 3
MAX_DAILY_BACKFILLS = 3
BACKFILL_RUN_PREFIX = "Photo backfill: "
# New content and image inputs preempt old-photo trials. Publishing metadata,
# documentation and unrelated site edits do not change this generation queue.
BACKFILL_PRIORITY_PATHS = (
    "content/articles.json", "content/visual_reviews.json", "content/approved_photos.json", "assets/generated",
    "assets/editorial-scenes", "assets/photos", "scripts/github_cpu_scenes.py",
    "scripts/cpu_photo_model.py", "scripts/generated_photos.py",
    "scripts/local_scene_photos.py", "scripts/local_scene_sync.py",
    "scripts/publish_cpu_scenes.py", "scripts/visual_review.py",
    "scripts/editorial_covers.py", "scripts/media_policy.py",
    ".github/workflows/free-cpu-scenes.yml",
)
MIN_RUNNER_FREE_BYTES = 1_000_000_000
CLIP_TOKEN_LIMIT = 77
TV_PILOT_SLUG = "variety-700-guests-only-introductions"
# These compact scenes retain the conspicuous object and action from the
# published scene direction. They avoid spending the small CLIP budget on
# repeated photography adjectives before the article's joke.
CPU_SCENES = {
    "fridge-checkin-five-years-only-opening-times": (
        "Close-up of a white refrigerator door covered with yellow paper notes "
        "beside its large handle. A black smartphone lies face-down on the "
        "kitchen counter in the foreground. Daylight."
    ),
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
    "fridge-checkin-five-years-only-opening-times": "3f677d6bc13705adf10d04e453bb23a6634192c118f75976708d28dfee34897a",
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
    # Remove only complete style phrases. A keyword inside a visual sentence
    # ("Photograph of a man carrying a parcel.") must not erase its subject.
    style_only = re.compile(
        r"(?:(?:candid|photorealistic|photoreal|realistic|documentary|press|"
        r"staged|deadpan|japanese|sports|news|wide|film|still|natural|from a)\s+)*"
        r"(?:photo|photograph|photography|comedy|lighting|photorealistic|photoreal|realistic)\.?",
        flags=re.IGNORECASE,
    )
    while len(sentences) > 1 and len(sentences[0].split()) <= 10 and style_only.fullmatch(sentences[0]):
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


def backfill_run_allowed(runs: list[dict], current_run_id: int, *, current_run_attempt: int,
                         now: datetime | None = None) -> bool:
    """Bound daily attempts and allow each old story only one trial per JST day."""
    if type(current_run_attempt) is not int or current_run_attempt < 1:
        raise ValueError("Backfill requires a positive integer workflow attempt")
    if current_run_attempt != 1:
        return False
    if not isinstance(runs, list) or type(current_run_id) is not int:
        raise ValueError("Invalid backfill workflow run inventory")
    jst = timezone(timedelta(hours=9))
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Backfill quota requires a timezone-qualified clock")
    today, candidates = current.astimezone(jst).date(), []
    if len(runs) >= 100:
        try:
            oldest = min(datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
                         for run in runs)
            if oldest.tzinfo is None or oldest.astimezone(jst).date() >= today:
                raise ValueError("Workflow inventory does not cover the complete JST day")
        except (KeyError, TypeError) as exc:
            raise ValueError("Incomplete workflow run inventory") from exc
    for run in runs:
        if not isinstance(run, dict):
            raise ValueError("Invalid workflow run entry")
        title = run.get("display_title", "")
        if not isinstance(title, str) or not title.startswith(BACKFILL_RUN_PREFIX):
            continue
        identifier = run.get("id")
        created = run.get("created_at")
        if type(identifier) is not int or not isinstance(created, str):
            raise ValueError("Backfill workflow run is missing identity or time")
        timestamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError("Backfill workflow run time must include timezone")
        if timestamp.astimezone(jst).date() == today:
            candidates.append((timestamp, identifier, title[len(BACKFILL_RUN_PREFIX):]))
    if current_run_id not in {identifier for _, identifier, _ in candidates}:
        raise ValueError("Current backfill run is absent from today's workflow inventory")
    ordered = sorted(candidates)
    position = next(index for index, (_, identifier, _) in enumerate(ordered)
                    if identifier == current_run_id)
    slug = ordered[position][2]
    return (position < MAX_DAILY_BACKFILLS
            and all(previous_slug != slug for _, _, previous_slug in ordered[:position]))


def _git_read(*arguments: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), *arguments], check=True,
                              capture_output=True, text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Backfill stopped: cannot verify latest main") from exc


def check_backfill_storage(root: Path | None = None, *, review_parent: Path | None = None) -> None:
    """Reserve a full new JPEG beside the preserved live archive and outbox."""
    source = ROOT if root is None else root
    paths = [source] + ([review_parent] if review_parent is not None else [])
    check_storage(paths, MIN_FREE_GIB)
    if archive_bytes(source) + reserved_jpeg_bytes() + MAX_SCENE_BYTES > MAX_ARCHIVE_BYTES:
        raise StorageLimitError("Live JPEG archive plus backfill candidate would exceed 1.5 GiB")


def check_main_unchanged(revision: str, *, review_parent: Path | None = None) -> None:
    """Yield to changed article/image inputs; never alter checkout contents."""
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise RuntimeError("Backfill stopped: invalid checkout revision")
    check_backfill_storage(review_parent=review_parent)
    remote = _git_read("ls-remote", "--exit-code", "origin", "refs/heads/main").split()
    if (len(remote) != 2 or not re.fullmatch(r"[0-9a-f]{40}", remote[0])
            or remote[1] != "refs/heads/main"):
        raise RuntimeError("Backfill stopped: invalid remote main revision")
    if remote[0] == revision:
        return
    # Fetch the pinned commit's trees, not model/JPEG blobs. Name-only diff with
    # rename detection disabled needs no file contents and changes no worktree.
    _git_read("fetch", "--no-tags", "--depth=1", "--filter=blob:none", "origin", remote[0])
    check_backfill_storage(review_parent=review_parent)
    changed = _git_read("diff", "--no-renames", "--name-only", revision, remote[0],
                        "--", *BACKFILL_PRIORITY_PATHS)
    if changed:
        raise RuntimeError("Backfill stopped: main advanced; current article/image inputs take priority")


def backfill_step_callback(revision: str, *, review_parent: Path | None = None):
    def check(_pipeline, step_index, _timestep, callback_kwargs):
        if (step_index + 1) % 4 == 0:
            check_main_unchanged(revision, review_parent=review_parent)
        return callback_kwargs
    return check


def select_articles(articles: list[dict], *, slug: str = "", limit: int = MAX_CPU_BATCH,
                    retry_held: bool = False, root: Path | None = None,
                    backfill_scene: str = "", review_dir: Path | None = None,
                    backfill_reference: bool = False) -> list[dict]:
    """Shared offline plan; deliberate hold retries cannot replace valid photos."""
    if not 1 <= limit <= MAX_CPU_BATCH:
        raise ValueError("CPU batch must be 1–3 scenes")
    if backfill_reference and not backfill_scene:
        raise ValueError("--backfill-reference requires an explicit --backfill-scene preview")
    if retry_held and not slug:
        raise ValueError("--retry-held requires one explicit --slug")
    if slug and not any(a.get("slug") == slug for a in articles):
        raise ValueError("Unknown published article slug")
    base = ROOT if root is None else root
    if backfill_scene:
        if (not slug or not retry_held or limit != 1 or review_dir is None
                or os.getenv("GITHUB_EVENT_NAME") != "workflow_dispatch"
                or os.getenv("SCENE_PUBLISH") != "0"):
            raise ValueError("Backfill requires an explicit --slug, --retry-held, --limit 1, "
                             "--review-dir and manual workflow_dispatch with publish=false")
        scene = backfill_scene.strip()
        if not 20 <= len(scene) <= 800 or "\x00" in scene:
            raise ValueError("Backfill scene must contain 20–800 characters")
        original = next(a for a in articles if a["slug"] == slug)
        if (original.get("visual_strategy", "cpu-photo") != "illustration"
                and not held_scene(original, root=base)):
            raise ValueError("Backfill preview needs an illustration story or a current visual hold")
        if usable_generated_scene(original, root=base):
            raise ValueError("Backfill cannot replace an existing valid JPEG")
        if backfill_reference:
            reviewed_reference_svg(original, root=base)
        candidate = copy.deepcopy(original)
        candidate.update(scene_prompt=scene, visual_strategy="cpu-photo")
        if article_fingerprint(candidate) == article_fingerprint(original):
            raise ValueError("Backfill needs a different scene direction, not a restamped source")
        validate_review_dir(Path(review_dir), root=base)
        try:
            from .visual_review import _publication_row, _attention
        except ImportError:
            from visual_review import _publication_row, _attention
        if pending_articles(articles, root=base, limit=0):
            raise ValueError("New article image generation takes priority over backfill")
        rows = [_publication_row(article, root=base) for article in articles]
        if any(row["status"] == "pending" or _attention(row)
               or row["publication"]["kind"] == "generic_fallback" for row in rows):
            raise ValueError("Current image review or coverage work takes priority over backfill")
        check_backfill_storage(base, review_parent=Path(review_dir).parent)
        return [candidate]
    candidates = pending_articles(articles, root=base, slug=slug, force=retry_held, limit=0)
    return [a for a in candidates if not usable_generated_scene(a, root=base)][:limit]


def reviewed_reference_svg(article: dict, *, root: Path | None = None) -> str:
    """Use only the original article's current, self-contained approved SVG."""
    try:
        from .editorial_covers import reviewed_story_scene
    except ImportError:
        from editorial_covers import reviewed_story_scene
    svg = reviewed_story_scene(article, root=ROOT if root is None else root)
    if svg is None:
        raise ValueError("Reference preview requires the original article's reviewed SVG")
    return svg


def render_reference_image(article: dict, *, root: Path | None = None):
    """Rasterize a validated local guide in memory; never write an intermediate."""
    svg = reviewed_reference_svg(article, root=root).encode("utf-8")
    try:
        import cairosvg
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Reference preview requires CairoSVG 2.7.1 and Pillow") from exc
    if cairosvg.__version__ != REFERENCE_RENDERER_VERSION:
        raise RuntimeError("Reference preview requires the pinned CairoSVG 2.7.1 renderer")
    png = cairosvg.svg2png(bytestring=svg, unsafe=False, background_color="#ffffff",
                          output_width=REFERENCE_INFERENCE["width"],
                          output_height=REFERENCE_INFERENCE["height"])
    with Image.open(io.BytesIO(png)) as source:
        source.load()
        if source.size != (REFERENCE_INFERENCE["width"], REFERENCE_INFERENCE["height"]):
            raise ValueError("Reference raster has unexpected dimensions")
        image = source.convert("RGB")
    return image, {
        "source_article_sha256": article_fingerprint(article),
        "init_svg_sha256": hashlib.sha256(svg).hexdigest(),
        "init_rgb_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
        "renderer": REFERENCE_RENDERER,
        "renderer_version": REFERENCE_RENDERER_VERSION,
    }


def validate_review_dir(destination: Path, *, root: Path = ROOT) -> None:
    """A review export never overwrites a pre-existing folder or source tree."""
    if destination.is_symlink() or destination.exists():
        raise RuntimeError("Review directory must be new and not a symlink")
    target, source = destination.resolve(), root.resolve()
    if target == source or target in source.parents or source in target.parents:
        raise RuntimeError("Review directory must be outside the source repository")
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise RuntimeError("Review directory needs an existing non-symlink parent")


def export_review_bundle(articles: list[dict], destination: Path, *, root: Path = ROOT,
                         source_article_sha256: dict[str, str] | None = None) -> None:
    """Export only genuinely saved successes; exact Python integers stay intact."""
    validate_review_dir(destination, root=root)
    if any(path.is_symlink() for path in (root, root / "assets", root / "assets/generated")):
        raise RuntimeError("Review source must not use symlinked generated-image paths")
    records = generated_manifest(root)
    selected = {}
    for article in articles:
        slug = article["slug"]
        record = records.get(slug)
        if not usable_generated_scene(article, root=root) or not isinstance(record, dict) or record.get("origin") not in ("github-cpu-lcm", ORIGIN, REFERENCE_ORIGIN):
            raise RuntimeError("Unverified CPU image cannot enter a review bundle: " + slug)
        selected[slug] = dict(record)
    if source_article_sha256 is not None:
        if (set(source_article_sha256) != set(selected)
                or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                       or value == article_fingerprint(article)
                       for article in articles
                       for value in [source_article_sha256[article["slug"]]])):
            raise ValueError("Backfill bundle needs the distinct original article fingerprint")
        for slug, fingerprint in source_article_sha256.items():
            if (selected[slug].get("origin") == REFERENCE_ORIGIN
                    and selected[slug].get("source_article_sha256") != fingerprint):
                raise ValueError("Reference bundle cannot change its original guide source")
            selected[slug]["source_article_sha256"] = fingerprint
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


def render_one(pipe, torch, article: dict, *, callback_on_step_end=None, reference_image=None) -> bytes:
    profile = REFERENCE_INFERENCE if reference_image is not None else INFERENCE
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
    if reference_image is not None:
        if (getattr(reference_image, "mode", None) != "RGB"
                or getattr(reference_image, "size", None) != (REFERENCE_INFERENCE["width"], REFERENCE_INFERENCE["height"])):
            raise ValueError("Reference image must be RGB at the fixed inference dimensions")
        image_options = {"image": reference_image, "strength": REFERENCE_INFERENCE["strength"]}
    else:
        image_options = {"width": INFERENCE["width"], "height": INFERENCE["height"]}
    with torch.inference_mode():
        callback = ({"callback_on_step_end": callback_on_step_end}
                    if callback_on_step_end is not None else {})
        result = pipe(
            prompt=prompt, negative_prompt=negative,
            num_inference_steps=profile["num_inference_steps"],
            guidance_scale=profile["guidance_scale"],
            generator=generator, output_type="pil", **image_options, **callback,
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
    parser.add_argument("--backfill-scene", default="",
                        help="Manual review-only photo direction for one illustration story; never edits live sources")
    parser.add_argument("--backfill-reference", action="store_true",
                        help="Opt-in: use that original story's reviewed SVG as the manual preview guide")
    args = parser.parse_args(argv)
    articles = load_articles()
    try:
        selected = select_articles(articles, slug=args.slug, limit=args.limit, retry_held=args.retry_held,
                                   backfill_scene=args.backfill_scene, review_dir=args.review_dir,
                                   backfill_reference=args.backfill_reference)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"CPU scenes pending in this batch: {len(selected)}", flush=True)
    if args.dry_run:
        for article in selected:
            print(json.dumps({
                "slug": article["slug"], "scene": scene_description(article), "cpu_prompt": cpu_prompt(article),
                "model": MODEL, "inference": REFERENCE_INFERENCE if args.backfill_reference else INFERENCE,
            }, ensure_ascii=False), flush=True)
        return 0
    if not permitted_ci():
        raise RuntimeError("CPU model is limited to opt-in public main GitHub Action runs")
    if not selected:
        return 0
    if args.backfill_scene and os.getenv("BACKFILL_QUOTA_ALLOWED") != "1":
        raise RuntimeError("Backfill needs the workflow's successful daily-quota check")
    if args.review_dir:
        validate_review_dir(args.review_dir, root=ROOT)
    original_hashes = ({args.slug: article_fingerprint(next(a for a in articles if a["slug"] == args.slug))}
                       if args.backfill_scene else None)
    revision = _git_read("rev-parse", "HEAD") if args.backfill_scene else None
    if revision is not None:
        check_main_unchanged(revision, review_parent=args.review_dir.parent)
    # Backfill candidates never write into the checkout. Only an exported new
    # JPEG with its exact clone and original source fingerprint leaves staging.
    staging = (tempfile.TemporaryDirectory(prefix=".cpu-backfill-", dir=args.review_dir.parent)
               if args.backfill_scene else nullcontext(ROOT))
    successes = []
    with staging as directory:
        generation_root = Path(directory)
        reference_image, reference_metadata = None, None
        if args.backfill_reference:
            original = next(a for a in articles if a["slug"] == args.slug)
            reference_image, reference_metadata = render_reference_image(original, root=ROOT)
        pipe, torch = load_pipeline()
        if args.backfill_reference:
            pipe = as_reference_pipeline(pipe)
        if revision is not None:
            check_main_unchanged(revision, review_parent=args.review_dir.parent)
        try:
            for index, article in enumerate(selected, 1):
                ensure_disk()
                print(f"[{index}/{len(selected)}] CPU staging: {article['slug']}", flush=True)
                try:
                    options = ({"callback_on_step_end": backfill_step_callback(revision, review_parent=args.review_dir.parent)}
                               if revision is not None else {})
                    if args.backfill_reference:
                        options["reference_image"] = reference_image
                    data = render_one(pipe, torch, article, **options)
                    if revision is not None:
                        check_main_unchanged(revision, review_parent=args.review_dir.parent)
                    write_options = {"reference_metadata": reference_metadata} if args.backfill_reference else {}
                    write_generated(article, data, MODEL, root=generation_root,
                                    origin=REFERENCE_ORIGIN if args.backfill_reference else ORIGIN,
                                    **write_options)
                    successes.append(article)
                    print(f"Saved verified first-party JPEG ({len(data)} bytes)", flush=True)
                except (RuntimeError, ValueError, OSError) as exc:
                    # Keep the usual article's SVG; never let a failed image stop news.
                    print(f"Skipped scene {article['slug']}: {exc}", file=sys.stderr, flush=True)
        finally:
            if args.review_dir and successes:
                export_review_bundle(successes, args.review_dir, root=generation_root,
                                     source_article_sha256=original_hashes)
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
