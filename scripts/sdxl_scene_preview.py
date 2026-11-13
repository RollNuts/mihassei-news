#!/usr/bin/env python3
"""One source-bound SDXL CPU preview; models never load in the controller/Mac."""
from __future__ import annotations

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import gc
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from zipfile import ZipFile

if __package__:
    from . import sdxl_preview_model as model
    from .generated_photos import article_fingerprint, MAX_ARCHIVE_BYTES, MAX_SCENE_BYTES
    from .local_scene_photos import archive_bytes, stable_seed, png_to_jpeg, pending_articles
    from .github_cpu_scenes import (check_main_unchanged, BACKFILL_PRIORITY_PATHS,
                                    backfill_run_allowed, BACKFILL_RUN_PREFIX)
    from .media_policy import jpeg_dimensions, MIN_JPEG_BYTES
    from .news_caster_preview import RemoteStopGate, download, stop_child
    from .news_caster_preview import Budget as ArtifactBudget
    from .compose_caster_preview import binary_command
else:
    import sdxl_preview_model as model
    from generated_photos import article_fingerprint, MAX_ARCHIVE_BYTES, MAX_SCENE_BYTES
    from local_scene_photos import archive_bytes, stable_seed, png_to_jpeg, pending_articles
    from github_cpu_scenes import (check_main_unchanged, BACKFILL_PRIORITY_PATHS,
                                   backfill_run_allowed, BACKFILL_RUN_PREFIX)
    from media_policy import jpeg_dimensions, MIN_JPEG_BYTES
    from news_caster_preview import RemoteStopGate, download, stop_child
    from news_caster_preview import Budget as ArtifactBudget
    from compose_caster_preview import binary_command

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "RollNuts/mihassei-news"
SLUG = "indoor-beach-friends-exchange-ripples"
SOURCE_SHA = "0e2a45ebc481758adc8779d9f624d3c76c6fdc318c228cd0018ed1e3fed1bf84"
TRUSTED_REFS = {"refs/heads/main", "refs/heads/codex/sdxl-preview-20261114"}
MIN_FREE = 12 * 1024**3
MAX_RSS = 13 * 1024**3
MIN_AVAILABLE = 1024**3
TIMEOUT = 48 * 60
JPEG = SLUG + ".jpg"
PROMPT = (
    "Head-to-mid-thigh portrait: one cute smiling Japanese adult woman, age 27, "
    "naturally full bust, standing in shallow indoor water. Opaque navy bikini "
    "top and bottoms fully visible; white shirt draped only on upper arms, "
    "away from bikini top and hips."
)
PROMPT_2 = (
    "Photorealistic three-quarter fashion portrait at an indoor sandy beach. "
    "One fictional Japanese adult woman age 27, cute smiling face, natural proportions, "
    "navy bikini top visible, white shirt on arms. Soft skylight, realistic skin and water."
)
BRIEF = PROMPT + " " + PROMPT_2
NATIVE_BF16_FLAGS = frozenset(("avx512_bf16", "amx_bf16"))
LEGACY_SDXL_TRIAL = {
    "id":43458459270, "created_at":"2026-11-13T16:01:26Z", "event":"workflow_dispatch",
    "path":".github/workflows/free-cpu-scenes.yml",
    "head_sha":"66d301c674965ca49673ccf3a30c4929abfebff6",
    "display_title":"Free CPU staged-news photos", "run_attempt":1,
}
PREFLIGHT_TRIAL = {
    "id":43507608306, "created_at":"2026-11-13T21:06:04Z", "event":"workflow_dispatch",
    "path":".github/workflows/free-cpu-scenes.yml",
    "head_sha":"10b3e6cb1e12f5cfb2659fb4ae5ca65ad9891b86",
    "display_title":BACKFILL_RUN_PREFIX + SLUG, "run_attempt":1,
    "status":"completed", "conclusion":"failure",
}
PREFLIGHT_ARTIFACT_ID = 14077650200
PREFLIGHT_ZIP_BYTES = 1531
PREFLIGHT_ZIP_SHA = "36f67881c73e3614eb1e8af5669d85fda87b3dfd9da8cbacc277c9232b60abc6"
PREFLIGHT_RECEIPT_BYTES = 3221
PREFLIGHT_RECEIPT_SHA = "cc949ee38d4b4e183a43bfbcf88d4dac814e86294bfc03e8359e9498754d26f5"


def atomic_json(path, value):
    stage = path.with_name(path.name + ".tmp")
    if path.is_symlink() or stage.is_symlink():
        raise ValueError("Unsafe preview metadata path")
    with stage.open("w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write("\n"); output.flush(); os.fsync(output.fileno())
    os.replace(stage, path)


def runner_gate(env=None, *, system=None, python_version=None):
    env = os.environ if env is None else env
    required = {"GITHUB_ACTIONS":"true", "GITHUB_REPOSITORY":REPOSITORY,
                "GITHUB_EVENT_NAME":"workflow_dispatch",
                "RUNNER_ENVIRONMENT":"github-hosted", "RUNNER_OS":"Linux",
                "SDXL_PUBLIC_REPOSITORY":"true", "PREVIEW_MODEL":"sdxl",
                "VERIPSA_ENABLE_FREE_CPU":"1", "SCENE_SLUG":SLUG, "SCENE_LIMIT":"1",
                "RETRY_HELD":"1", "SCENE_PUBLISH":"0", "BACKFILL_SCENE":"", "BACKFILL_REFERENCE":"0"}
    ref = env.get("GITHUB_REF")
    if (any(env.get(key) != value for key, value in required.items()) or ref not in TRUSTED_REFS
            or env.get("GITHUB_WORKFLOW_REF") != f"{REPOSITORY}/.github/workflows/free-cpu-scenes.yml@{ref}"
            or not re.fullmatch(r"[0-9a-f]{40}", env.get("GITHUB_SHA", ""))
            or (system or platform.system()) != "Linux"
            or (python_version or sys.version_info[:2]) != (3, 11)):
        raise RuntimeError("SDXL requires exact manual inputs on the public repository's trusted GitHub CPU ref")
    if any(Path(p).exists() for p in ("/dev/nvidia0", "/dev/kfd")):
        raise RuntimeError("GPU devices are forbidden")
    temporary = Path(env.get("RUNNER_TEMP", ""))
    if not temporary.is_absolute() or not temporary.is_dir() or temporary.is_symlink():
        raise ValueError("A disposable RUNNER_TEMP is required")
    return temporary.resolve()


def candidate_article(articles):
    matches = [a for a in articles if a.get("slug") == SLUG]
    source = matches[0] if len(matches) == 1 else None
    if source is None or article_fingerprint(source) != SOURCE_SHA:
        raise ValueError("Source article changed; SDXL portrait preview stopped")
    candidate = copy.deepcopy(source)
    candidate["scene_prompt"] = BRIEF
    if article_fingerprint(candidate) == SOURCE_SHA:
        raise ValueError("Preview must have its own portrait-scene fingerprint")
    return candidate


def reject_adopted_photo(manifest, reviews, approved):
    review = reviews.get("reviews", {}).get(SLUG, {})
    generation = manifest.get("images", {}).get(SLUG, {})
    if ((review.get("decision") == "accepted" and review.get("article_sha256") == SOURCE_SHA)
            or (generation.get("article_sha256") == SOURCE_SHA and generation.get("accepted") is True)
            or SLUG in approved.get("approved", {})):
        raise ValueError("Latest main already has an adopted photo; another preview is not needed")


def fresh_source():
    for args in (("fetch", "--quiet", "origin", "main"),):
        subprocess.run(["git", *args], cwd=ROOT, check=True, timeout=30, capture_output=True)
    def read(path):
        return json.loads(subprocess.check_output(["git", "show", "FETCH_HEAD:" + path],
                                                  cwd=ROOT, text=True, timeout=30))
    candidate_article(read("content/articles.json"))
    reject_adopted_photo(read("assets/generated/manifest.json"), read("content/visual_reviews.json"),
                        read("content/approved_photos.json"))
    return subprocess.check_output(["git", "rev-parse", "FETCH_HEAD"], cwd=ROOT, text=True, timeout=10).strip()


def native_bf16_features(path=Path("/proc/cpuinfo")):
    """Performance evidence only; PyTorch also has generic CPU BF16 dispatch."""
    try:
        blocks = re.split(r"\n[ \t]*\n", path.read_text(encoding="utf-8").strip())
        identifiers, flag_sets = set(), []
        for block in blocks:
            ids = re.findall(r"^processor[ \t]*:[ \t]*(\d+)[ \t]*$", block, re.M)
            flags = re.findall(r"^flags[ \t]*:[ \t]*(.*)$", block, re.M)
            if len(ids) != 1 or len(flags) != 1 or ids[0] in identifiers or not flags[0].split():
                raise ValueError("Incomplete or duplicate logical CPU report")
            identifiers.add(ids[0]); flag_sets.append(set(flags[0].split()))
        common = set.intersection(*flag_sets)
        return {"cpu_count":len(flag_sets), "common_native_flags":sorted(common & NATIVE_BF16_FLAGS)}
    except (OSError, UnicodeError, ValueError) as error:
        return {"cpu_count":None, "common_native_flags":[], "observation_error":type(error).__name__}


def validate_preflight_archive(data):
    if len(data) != PREFLIGHT_ZIP_BYTES or hashlib.sha256(data).hexdigest() != PREFLIGHT_ZIP_SHA:
        raise ValueError("Metadata-only trial artifact differs from its pinned ZIP")
    with ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if (len(members) != 1 or members[0].filename != "receipt.json"
                or members[0].file_size != PREFLIGHT_RECEIPT_BYTES or members[0].flag_bits & 1):
            raise ValueError("Metadata-only trial must contain only its bounded receipt, no JPEG")
        raw = archive.read(members[0])
    if hashlib.sha256(raw).hexdigest() != PREFLIGHT_RECEIPT_SHA:
        raise ValueError("Metadata-only trial receipt differs from its pinned SHA")
    receipt = json.loads(raw)
    lifecycle = ("download_started", "models_downloaded", "model_import_started",
                 "model_execution_started", "model_generation_completed")
    if (receipt.get("run_id") != str(PREFLIGHT_TRIAL["id"]) or receipt.get("status") != "failed"
            or receipt.get("publication") != "preview_only" or receipt.get("article_slug") != SLUG
            or receipt.get("source_article_sha256") != SOURCE_SHA
            or receipt.get("model") != model.MODEL or receipt.get("model_revision") != model.REVISION
            or any(receipt.get(key) is not False for key in lifecycle)
            or receipt.get("verified_files") != {} or "worker" in receipt
            or any(key.startswith("jpeg_") for key in receipt)):
        raise ValueError("Technical continuation requires a failed preflight with no model lifecycle or JPEG")
    return {"artifact_id":PREFLIGHT_ARTIFACT_ID, "artifact_sha256":PREFLIGHT_ZIP_SHA,
            "receipt_sha256":PREFLIGHT_RECEIPT_SHA, "model_lifecycle":{key:False for key in lifecycle},
            "jpeg_members":[]}


def verify_preflight_trial():
    temporary = runner_gate()  # Metadata ZIP is fetched only on the trusted hosted CPU runner.
    with tempfile.TemporaryDirectory(prefix="sdxl-preflight-proof-", dir=temporary) as directory:
        work = Path(directory); archive = work / "metadata.zip"
        budget = ArtifactBudget(work, timeout=30, remote=RemoteStopGate())
        binary_command(["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{PREFLIGHT_ARTIFACT_ID}/zip"],
                       archive, budget, max_bytes=PREFLIGHT_ZIP_BYTES)
        return validate_preflight_archive(archive.read_bytes())


def check_daily_quota(*, now=None):
    current = int(os.environ["GITHUB_RUN_ID"])
    attempt = int(os.environ["GITHUB_RUN_ATTEMPT"])
    if attempt != 1:
        raise RuntimeError("SDXL workflow reruns are forbidden; one attempt per story and JST day")
    requested = os.environ.get("USER_REQUESTED_RETRY_AFTER", "")
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ValueError("SDXL quota requires a timezone-qualified clock")
    today = clock.astimezone(timezone(timedelta(hours=9))).date().isoformat()
    continuation = requested == str(PREFLIGHT_TRIAL["id"])
    if requested and (requested not in {str(LEGACY_SDXL_TRIAL["id"]), str(PREFLIGHT_TRIAL["id"])}
                      or today != "2026-11-14"):
        raise ValueError("User-requested retry is limited to the fixed failed trial on JST 2026-11-14")
    inventory = json.loads(subprocess.check_output([
        "gh", "api", f"repos/{REPOSITORY}/actions/workflows/free-cpu-scenes.yml/runs?per_page=100"
    ], cwd=ROOT, text=True, timeout=15))["workflow_runs"]
    if not isinstance(inventory, list) or any(not isinstance(run, dict) for run in inventory):
        raise ValueError("Invalid SDXL workflow run inventory")
    classified, legacy, preflight = [], None, None
    for raw in inventory:
        run = dict(raw)
        if run.get("id") == LEGACY_SDXL_TRIAL["id"]:
            if any(run.get(key) != value for key, value in LEGACY_SDXL_TRIAL.items()):
                raise ValueError("Known legacy SDXL trial provenance does not match")
            # Quota-only classification: preserve the actual API title and receipt.
            quota_slug = SLUG
            if requested:
                if legacy is not None or run.get("status") != "completed" or run.get("conclusion") != "failure":
                    raise ValueError("User-requested retry requires one verified completed failed legacy trial")
                quota_slug = SLUG + "-prior-request-" + str(LEGACY_SDXL_TRIAL["id"])
            legacy = {**LEGACY_SDXL_TRIAL, "quota_slug":quota_slug}
            if requested:
                legacy.update(status=run["status"], conclusion=run["conclusion"])
            run["display_title"] = BACKFILL_RUN_PREFIX + quota_slug
        elif continuation and run.get("id") == PREFLIGHT_TRIAL["id"]:
            if preflight is not None or any(run.get(key) != value for key, value in PREFLIGHT_TRIAL.items()):
                raise ValueError("Metadata-only trial provenance does not match its nine pinned fields")
            quota_slug = SLUG + "-preflight-" + requested
            preflight = {**PREFLIGHT_TRIAL, "quota_slug":quota_slug}
            run["display_title"] = BACKFILL_RUN_PREFIX + quota_slug
        classified.append(run)
    if requested and legacy is None:
        raise ValueError("User-requested retry is missing its verified failed legacy trial")
    if continuation and preflight is None:
        raise ValueError("Technical continuation is missing its fixed metadata-only trial")
    matches = [run for run in classified if run.get("id") == current]
    if len(matches) != 1 or matches[0].get("display_title") != BACKFILL_RUN_PREFIX + SLUG:
        raise ValueError("Current SDXL run is absent from the bounded story inventory")
    if not backfill_run_allowed(classified, current, current_run_attempt=attempt, now=clock):
        raise RuntimeError("SDXL daily attempt budget or same-story limit reached; new articles take priority")
    evidence = {"allowed":True, "run_id":current, "run_attempt":attempt, "known_legacy_trial":legacy,
                "user_requested_retry_after":requested}
    if continuation:
        evidence.update(metadata_only_trial=preflight, metadata_only_proof=verify_preflight_trial())
    return evidence


def initial_priority_gate(revision, articles, *, review_parent):
    """Use the existing input scope and publication gate; never repair other work."""
    paths = [path for path in BACKFILL_PRIORITY_PATHS if path.startswith(("content/", "assets/"))]
    changed = subprocess.check_output(["git", "diff", "--no-renames", "--name-only", revision,
                                       "--", *paths], cwd=ROOT, text=True, timeout=15).strip()
    unknown = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "--", *paths],
                                       cwd=ROOT, text=True, timeout=15).strip()
    if changed or unknown:
        raise RuntimeError("Checkout article/image inputs differ from fresh main; rebase before preview")
    if __package__:
        from .visual_review import _publication_row, _attention
    else:
        from visual_review import _publication_row, _attention
    others = [article for article in articles if article.get("slug") != SLUG]
    if pending_articles(others, root=ROOT, limit=0):
        raise RuntimeError("Other article images take priority over this portrait preview")
    rows = [_publication_row(article, root=ROOT) for article in others]
    if any(row["status"] == "pending" or _attention(row)
           or row["publication"]["kind"] == "generic_fallback" for row in rows):
        raise RuntimeError("Other image review/publication work takes priority over this portrait preview")
    check_main_unchanged(revision, review_parent=review_parent)


def final_priority_gate(before, after):
    if any(not re.fullmatch(r"[0-9a-f]{40}", value) for value in (before, after)):
        raise ValueError("Cannot verify final main priority revisions")
    changed = subprocess.check_output(["git", "diff", "--no-renames", "--name-only", before, after,
                                       "--", *BACKFILL_PRIORITY_PATHS], cwd=ROOT, text=True, timeout=15).strip()
    if changed:
        raise RuntimeError("Main article/image inputs advanced before export; current work takes priority")


def mem_available(proc=Path("/proc")):
    match = re.search(r"^MemAvailable:\s+(\d+) kB$", (proc / "meminfo").read_text(), re.M)
    if not match:
        raise RuntimeError("Cannot verify Linux available memory")
    return int(match[1]) * 1024


def group_rss(group, proc=Path("/proc")):
    total = 0
    for folder in proc.iterdir():
        if not folder.name.isdigit():
            continue
        try:
            fields = (folder / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) != group:
                continue
            match = re.search(r"^VmRSS:\s+(\d+) kB$", (folder / "status").read_text(), re.M)
            if match:
                total += int(match[1]) * 1024
        except FileNotFoundError:
            continue
    return total


def resource_limits(rss, available):
    if rss > MAX_RSS:
        raise RuntimeError("Owned SDXL worker exceeded 13 GiB RSS")
    if available < MIN_AVAILABLE:
        raise RuntimeError("Linux MemAvailable fell below 1 GiB")


class Budget:
    def __init__(self, work, remote, *, revision):
        self.work, self.remote = work, remote
        self.deadline = time.monotonic() + TIMEOUT
        self.max_rss, self.minimum_available = 0, None
        self.revision = revision
        self.next_priority_check = time.monotonic() + 30
        self.priority_future, self.priority_pool = None, None

    def priority_check(self):
        now = time.monotonic()
        if self.priority_future is not None:
            if self.priority_future.done():
                self.priority_future.result()
                self.priority_future = None
            elif now >= self.priority_started + 30:
                raise RuntimeError("Main priority could not be verified within 30 seconds")
        if now >= self.next_priority_check and self.priority_future is None:
            if self.priority_pool is None:
                self.priority_pool = ThreadPoolExecutor(max_workers=1)
            self.priority_started = now
            self.next_priority_check = now + 30
            self.priority_future = self.priority_pool.submit(check_main_unchanged, self.revision,
                                                              review_parent=self.work)

    def finish_priority(self):
        if self.priority_future is not None:
            self.priority_future.result(timeout=max(0, self.priority_started + 30 - time.monotonic()))
            self.priority_future = None

    def close(self):
        if self.priority_pool is not None:
            self.priority_pool.shutdown(wait=False, cancel_futures=True)

    def check(self, group=None):
        if time.monotonic() >= self.deadline:
            raise RuntimeError("SDXL exceeded its 48-minute controller deadline")
        self.remote.check()
        if shutil.disk_usage(self.work).free < MIN_FREE:
            raise RuntimeError("Less than 12 GiB free; SDXL stopped")
        if archive_bytes(ROOT) + MAX_SCENE_BYTES > MAX_ARCHIVE_BYTES:
            raise RuntimeError("Live JPEG archive reached its 1.5 GiB cap")
        available = mem_available()
        rss = group_rss(group) if group is not None else 0
        self.max_rss = max(self.max_rss, rss)
        self.minimum_available = min(self.minimum_available or available, available)
        resource_limits(rss, available)
        self.priority_check()


def check_disk_reservation(path):
    required = MIN_FREE + model.TOTAL_DOWNLOAD_BYTES + model.SAFETY_BYTES + model.LICENSE_TEXT_BYTES + 64 * 1024**2
    if shutil.disk_usage(path).free < required:
        raise RuntimeError("Need 12 GiB reserve plus all pinned SDXL/checker bytes before downloading")


def download_files(work, budget, receipt, output):
    def pinned_file(key, url, destination, spec):
        receipt["download_started"] = True
        atomic_json(output / "receipt.json", receipt)
        record = download(url, destination, budget, size=spec["bytes"], sha256=spec["sha256"])
        receipt["verified_files"][key] = {"bytes":record["bytes"], "sha256":record["sha256"]}
        atomic_json(output / "receipt.json", receipt)
    pinned_file("license/canonical-license.txt", model.LICENSE_TEXT_URL,
                work / "licenses/canonical-license.txt", model.LICENSE_TEXT_FILE_SPEC)
    for component, repo, revision, specs in (
            ("base", model.MODEL, model.REVISION, model.FILE_SPECS),
            ("safety", model.SAFETY_MODEL, model.SAFETY_REVISION, model.SAFETY_FILE_SPECS)):
        for relative, spec in specs.items():
            pinned_file(component + "/" + relative, f"https://huggingface.co/{repo}/resolve/{revision}/{relative}",
                        work / component / relative, spec)
    receipt["models_downloaded"] = True


def progress(work, stage, **values):
    path = work / "progress.json"
    previous = json.loads(path.read_text()) if path.exists() else {}
    phases = [*previous.get("phases", []), stage]
    atomic_json(path, {**previous, **values, "stage":stage, "phases":phases})


def run_owned_worker(work, budget):
    env = {**os.environ, "HF_HUB_OFFLINE":"1", "TRANSFORMERS_OFFLINE":"1",
           "HF_HUB_DISABLE_TELEMETRY":"1", "TOKENIZERS_PARALLELISM":"false",
           "USE_TF":"0", "USE_FLAX":"0",
           "OMP_NUM_THREADS":"4", "MKL_NUM_THREADS":"4", "SDXL_CONTROL_PID":str(os.getpid()),
           "SDXL_CONTROL_NONCE":json.loads((work / "request.json").read_text())["nonce"]}
    env.update(HF_HOME=str(work / "hf-cache"), HF_HUB_CACHE=str(work / "hf-cache/hub"),
               TORCH_HOME=str(work / "torch-cache"), XDG_CACHE_HOME=str(work / "cache"))
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker", str(work)],
                               cwd=ROOT, env=env, start_new_session=True)
    try:
        while process.poll() is None:
            budget.check(process.pid)
            time.sleep(.5)
        budget.check()
        if process.returncode:
            raise RuntimeError("SDXL worker failed with exit " + str(process.returncode))
        return process.returncode
    except BaseException:
        stop_child(process)
        raise


def token_counts(tokenizer, tokenizer_2, negative):
    result = {}
    for key, encoder, text in (("prompt",tokenizer,PROMPT), ("prompt_2",tokenizer_2,PROMPT_2),
                               ("negative_prompt",tokenizer,negative), ("negative_prompt_2",tokenizer_2,negative)):
        result[key] = len(encoder.encode(text, add_special_tokens=True))
        if not 2 <= result[key] <= 77:
            raise ValueError("SDXL " + key + " exceeds its complete 77-token CLIP budget")
    return result


def load_components(base, classes, dtype):
    components = {}
    for name, cls in (("unet",classes["unet"]), ("text_encoder",classes["text_encoder"]),
                      ("text_encoder_2",classes["text_encoder_2"]), ("vae",classes["vae"])):
        components[name] = cls.from_pretrained(str(base), subfolder=name, torch_dtype=dtype,
                                              variant="fp16", low_cpu_mem_usage=True,
                                              use_safetensors=True, local_files_only=True)
    return components


def validate_rgb(rgb, np):
    if (not isinstance(rgb, np.ndarray) or rgb.shape != (1, 576, 1024, 3)
            or not np.isfinite(rgb).all() or rgb.min() < 0 or rgb.max() > 1):
        raise ValueError("SDXL did not return one finite RGB image")


def safety_result(rgb, flags, np):
    validate_rgb(rgb, np)
    if not verified_safety_flags(flags):
        raise ValueError("Existing safety checker rejected or did not verify the SDXL preview")


def verified_safety_flags(flags):
    return isinstance(flags, list) and len(flags) == 1 and type(flags[0]) is bool and flags[0] is False


def require_features(value, torch, *, batch=None):
    if (not torch.is_tensor(value) or value.ndim != 2 or not 1 <= value.numel() <= 65536
            or (batch is not None and value.shape[0] != batch) or not torch.isfinite(value).all()):
        raise ValueError("Safety checker features must be finite bounded rows")
    norms = torch.linalg.vector_norm(value, dim=-1)
    if not torch.isfinite(norms).all() or not (norms > 0).all():
        raise ValueError("Safety checker feature norms must be finite and nonzero")


def checked_safety_call(checker, pixels, rgb, torch):
    for name in ("concept_embeds", "special_care_embeds"):
        require_features(getattr(checker, name, None), torch)
    for name in ("concept_embeds_weights", "special_care_embeds_weights"):
        value = getattr(checker, name, None)
        if (not torch.is_tensor(value) or not 1 <= value.numel() <= 65536
                or not torch.isfinite(value).all()):
            raise ValueError("Safety checker concept weights must be finite bounded arrays")
    if not torch.isfinite(pixels).all():
        raise ValueError("Safety checker normalized pixels are not finite")
    observed = []
    def projection_checked(module, inputs, output):
        require_features(output, torch, batch=1)
        observed.append(True)
    handle = checker.visual_projection.register_forward_hook(projection_checked)
    try:
        result = checker(images=rgb.copy(), clip_input=pixels)
    finally:
        handle.remove()
    if len(observed) != 1:
        raise ValueError("Safety checker did not verify exactly one CLIP projection")
    return result


def render_phased(pipe, components, torch, negative, seed, work):
    """Keep CPU BF16 UNet/embeddings; release UNet before explicit F32 decode."""
    settings = model.INFERENCE
    with torch.inference_mode():
        progress(work, "prompt_encoding", pipeline_loaded=True, model_execution_started=True)
        encoded = pipe.encode_prompt(prompt=PROMPT, prompt_2=PROMPT_2, device=torch.device("cpu"),
                                     num_images_per_prompt=1, do_classifier_free_guidance=True,
                                     negative_prompt=negative, negative_prompt_2=negative)
        for name in ("text_encoder", "text_encoder_2"):
            setattr(pipe, name, None)
            components.pop(name)
        gc.collect()
        if pipe.unet.dtype != torch.bfloat16:
            raise ValueError("UNet must retain BF16 weights; no F32 fallback")
        progress(work, "unet_bfloat16", text_encoders_released=True)
        positive, negative_embeds, pooled, negative_pooled = [value.to(dtype=torch.bfloat16) for value in encoded]
        progress(work, "denoising_started", unet_compute_dtype="bfloat16")
        latents = pipe(prompt_embeds=positive, negative_prompt_embeds=negative_embeds,
                       pooled_prompt_embeds=pooled, negative_pooled_prompt_embeds=negative_pooled,
                       width=settings["width"], height=settings["height"],
                       num_inference_steps=settings["num_inference_steps"], guidance_scale=settings["guidance_scale"],
                       num_images_per_prompt=1, generator=torch.Generator(device="cpu").manual_seed(seed),
                       output_type="latent").images
        if (not torch.is_tensor(latents) or tuple(latents.shape) != (1,4,72,128)
                or not torch.isfinite(latents).all()):
            raise ValueError("SDXL denoising returned invalid/nonfinite latents")
        pipe.unet = None
        components.pop("unet")
        gc.collect()
        progress(work, "vae_float32_decode", unet_released=True, vae_decode_dtype="float32")
        pipe.vae.to(dtype=torch.float32)
        if (getattr(pipe.vae.config,"latents_mean",None) is not None
                or getattr(pipe.vae.config,"latents_std",None) is not None):
            raise ValueError("Unexpected latent normalization for the pinned SDXL base VAE")
        image = pipe.vae.decode(latents.to(dtype=torch.float32) / pipe.vae.config.scaling_factor,
                               return_dict=False)[0]
        if pipe.watermark is not None:
            image = pipe.watermark.apply_watermark(image)
        return pipe.image_processor.postprocess(image, output_type="np")


def worker(work):
    temporary = runner_gate()
    work = work.resolve()
    if not work.is_relative_to(temporary) or work.is_symlink():
        raise ValueError("Worker scratch must be inside RUNNER_TEMP")
    request = json.loads((work / "request.json").read_text())
    if (os.environ.get("SDXL_CONTROL_PID") != str(os.getppid())
            or os.environ.get("SDXL_CONTROL_NONCE") != request.get("nonce")
            or not request.get("nonce") or os.getpgrp() != os.getpid()):
        raise RuntimeError("Worker requires its owning stdlib controller")
    candidate = request["candidate"]
    if (candidate.get("slug") != SLUG or candidate.get("scene_prompt") != BRIEF
            or article_fingerprint(candidate) != request["candidate_article_sha256"]):
        raise ValueError("Worker candidate differs from its source-bound portrait brief")
    try:
        render_worker(work, request, candidate)
    except BaseException as error:
        progress(work, "worker_failed", failure_type=type(error).__name__, failure=str(error)[:300])
        raise


def render_worker(work, request, candidate):
    runner_gate()
    native = native_bf16_features()
    progress(work, "cpu_bf16_observed", native_bf16_features=native)
    progress(work, "import_started", model_import_started=True)
    import torch
    import numpy as np
    from PIL import Image
    from diffusers import AutoencoderKL, UNet2DConditionModel, StableDiffusionXLPipeline, DPMSolverMultistepScheduler
    from diffusers.pipelines.stable_diffusion import StableDiffusionSafetyChecker
    from transformers import CLIPTextModel, CLIPTextModelWithProjection, CLIPTokenizer, CLIPImageProcessor
    from importlib.metadata import version
    torch.set_num_threads(4); torch.set_num_interop_threads(1)
    base, safety = work / "base", work / "safety"
    tokenizer = CLIPTokenizer.from_pretrained(str(base), subfolder="tokenizer", local_files_only=True)
    tokenizer_2 = CLIPTokenizer.from_pretrained(str(base), subfolder="tokenizer_2", local_files_only=True)
    if __package__:
        from .github_cpu_scenes import cpu_negative_prompt, check_quality
    else:
        from github_cpu_scenes import cpu_negative_prompt, check_quality
    negative = cpu_negative_prompt(candidate)
    counts = token_counts(tokenizer, tokenizer_2, negative)
    progress(work, "tokens_verified", token_counts=counts, negative_prompt=negative,
             packages={name:version(name) for name in ("torch","diffusers","transformers","accelerate","safetensors")})
    components = load_components(base, {"unet":UNet2DConditionModel, "vae":AutoencoderKL,
                                        "text_encoder":CLIPTextModel, "text_encoder_2":CLIPTextModelWithProjection}, torch.bfloat16)
    settings = model.INFERENCE
    scheduler = DPMSolverMultistepScheduler.from_pretrained(
        str(base), subfolder="scheduler", local_files_only=True,
        **{key:settings[key] for key in ("algorithm_type","solver_order","solver_type","use_karras_sigmas")})
    pipe = StableDiffusionXLPipeline(**components, tokenizer=tokenizer, tokenizer_2=tokenizer_2, scheduler=scheduler)
    pipe.to("cpu"); pipe.set_progress_bar_config(disable=True)
    rgb = render_phased(pipe, components, torch, negative, request["seed"], work)
    validate_rgb(rgb, np)
    progress(work, "safety_load", model_generation_completed=True)
    del pipe, components, scheduler, tokenizer, tokenizer_2
    gc.collect()
    checker = StableDiffusionSafetyChecker.from_pretrained(
        str(safety), subfolder="safety_checker", torch_dtype=torch.float32, low_cpu_mem_usage=True,
        use_safetensors=True, local_files_only=True).to("cpu")
    extractor = CLIPImageProcessor.from_pretrained(str(safety), subfolder="feature_extractor", local_files_only=True)
    images = [Image.fromarray((rgb[0] * 255).round().astype("uint8"), mode="RGB")]
    pixels = extractor(images=images, return_tensors="pt").pixel_values.to(device="cpu", dtype=torch.float32)
    with torch.inference_mode():
        checked, flags = checked_safety_call(checker, pixels, rgb, torch)
    observed = flags if isinstance(flags, list) and all(type(flag) is bool for flag in flags) else None
    progress(work, "safety_checked", safety_flags=observed, safety_checked=False)
    safety_result(checked, flags, np)
    image = Image.fromarray((checked[0] * 255).round().astype("uint8"), mode="RGB")
    check_quality(image)
    png = io.BytesIO(); image.save(png, format="PNG")
    jpeg = png_to_jpeg(png.getvalue())
    (work / "rendered.jpg").write_bytes(jpeg)
    progress(work, "worker_succeeded", safety_flags=flags, safety_checked=True)
    atomic_json(work / "result.json", {"status":"generated_unreviewed", "safety_flags":flags,
                                       "sha256":hashlib.sha256(jpeg).hexdigest(), "bytes":len(jpeg)})


def execute(output, *, check_only=False):
    temporary = runner_gate()  # No writes/downloads/imports on a Mac or wrong input.
    output = output.resolve()
    if not output.is_relative_to(temporary) or output == temporary:
        raise ValueError("Preview output must be a dedicated directory in RUNNER_TEMP")
    if output.exists():
        previous = json.loads((output / "receipt.json").read_text())
        if (check_only or previous.get("status") != "ready_unexecuted"
                or previous.get("run_id") != os.environ.get("GITHUB_RUN_ID")
                or {p.name for p in output.iterdir()} != {"receipt.json", "articles.json"}):
            raise ValueError("Refusing to overwrite an existing preview directory")
    else:
        output.mkdir()
    receipt = {"version":1, "publication":"preview_only", "status":"started",
               "run_id":os.environ.get("GITHUB_RUN_ID"), "source_article_sha256":SOURCE_SHA,
               "article_slug":SLUG, "model":model.MODEL, "model_revision":model.REVISION,
               "license":model.LICENSE_URL, "license_sha256":model.LICENSE_SHA256,
               "model_card":model.MODEL_CARD_URL, "model_card_sha256":model.MODEL_CARD_SHA256,
               "license_text_url":model.LICENSE_TEXT_URL, "license_text_revision":model.LICENSE_TEXT_REVISION,
               "license_text_sha256":model.LICENSE_TEXT_SHA256, "inference":model.INFERENCE,
               "prompt":PROMPT, "prompt_2":PROMPT_2, "download_started":False,
               "models_downloaded":False, "model_import_started":False,
               "model_execution_started":False, "model_generation_completed":False, "verified_files":{}}
    started = time.monotonic(); remote = RemoteStopGate(); budget = None
    atomic_json(output / "receipt.json", receipt)
    try:
        remote.check()
        receipt["native_bf16_features"] = native_bf16_features()
        receipt["daily_quota"] = check_daily_quota()
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=10).strip()
        if head != os.environ["GITHUB_SHA"]:
            raise ValueError("Checkout must equal the exact dispatched github.sha")
        receipt["checkout_sha"] = head
        receipt["workflow_ref"] = os.environ["GITHUB_WORKFLOW_REF"]
        check_disk_reservation(temporary)
        articles = json.loads((ROOT / "content/articles.json").read_text())
        candidate = candidate_article(articles)
        receipt.update(candidate_article_sha256=article_fingerprint(candidate), seed=stable_seed(candidate),
                       fresh_main_sha_before=fresh_source())
        initial_priority_gate(receipt["fresh_main_sha_before"], articles, review_parent=temporary)
        receipt["initial_other_priority_clear"] = True
        atomic_json(output / "articles.json", [candidate])
        with tempfile.TemporaryDirectory(prefix="sdxl-preview-", dir=temporary) as directory:
            work = Path(directory); budget = Budget(work, remote, revision=receipt["fresh_main_sha_before"]); budget.check()
            if check_only:
                receipt["status"] = "ready_unexecuted"
                return
            try:
                download_files(work, budget, receipt, output)
                atomic_json(work / "request.json", {"nonce":uuid.uuid4().hex, "candidate":candidate,
                            "candidate_article_sha256":receipt["candidate_article_sha256"], "seed":receipt["seed"]})
                run_owned_worker(work, budget)
                result = json.loads((work / "result.json").read_text())
                image = (work / "rendered.jpg").read_bytes()
                if (result.get("status") != "generated_unreviewed" or not verified_safety_flags(result.get("safety_flags"))
                        or not MIN_JPEG_BYTES <= len(image) <= MAX_SCENE_BYTES
                        or jpeg_dimensions(image) != (1200,675)
                        or result.get("sha256") != hashlib.sha256(image).hexdigest() or result.get("bytes") != len(image)):
                    raise ValueError("Worker output failed safety, source, JPEG or size verification")
                budget.check(); budget.finish_priority(); remote.next_check = 0; remote.check()
                receipt["fresh_main_sha_after"] = fresh_source()
                final_priority_gate(receipt["fresh_main_sha_before"], receipt["fresh_main_sha_after"])
                receipt["final_priority_clear"] = True
                stage = output / (JPEG + ".tmp"); stage.write_bytes(image); os.replace(stage, output / JPEG)
                receipt.update(status="generated_unreviewed", jpeg_sha256=result["sha256"], jpeg_bytes=len(image))
            finally:
                path = work / "progress.json"
                if path.is_file():
                    receipt["worker"] = json.loads(path.read_text())
                    for key in ("model_import_started", "model_execution_started", "model_generation_completed"):
                        receipt[key] = receipt["worker"].get(key, False)
    except BaseException as error:
        (output / JPEG).unlink(missing_ok=True)
        receipt.update(status="failed", failure_type=type(error).__name__, failure=str(error)[:300])
        raise
    finally:
        receipt.update(elapsed_seconds=round(time.monotonic()-started,3), checked_at_utc=datetime.now(timezone.utc).isoformat())
        if budget:
            receipt.update(max_child_rss_bytes=budget.max_rss, minimum_mem_available_bytes=budget.minimum_available)
            budget.close()
        atomic_json(output / "receipt.json", receipt)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true"); mode.add_argument("--check", action="store_true")
    mode.add_argument("--worker", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.worker:
        worker(args.worker); return 0
    if args.run or args.check:
        if args.output is None: parser.error("--run/--check requires --output")
        execute(args.output, check_only=args.check); return 0
    print(json.dumps({"publication":"preview_only", "downloads":"none in plan mode", "model":model.MODEL,
                      "source_article_sha256":SOURCE_SHA, "prompt":PROMPT, "prompt_2":PROMPT_2}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print("SDXL preview stopped:", type(error).__name__, str(error), file=sys.stderr)
        raise SystemExit(1)
