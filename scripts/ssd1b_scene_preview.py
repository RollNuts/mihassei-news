#!/usr/bin/env python3
"""One source-bound SSD1B CPU study; models never load in the controller/Mac."""
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

if __package__:
    from . import ssd1b_preview_model as model
    from .generated_photos import article_fingerprint, MAX_ARCHIVE_BYTES, MAX_SCENE_BYTES
    from .local_scene_photos import archive_bytes, stable_seed, png_to_jpeg, pending_articles
    from .github_cpu_scenes import (check_main_unchanged, BACKFILL_PRIORITY_PATHS,
                                    BACKFILL_RUN_PREFIX, MAX_DAILY_BACKFILLS, cpu_negative_prompt)
    from .media_policy import jpeg_dimensions, MIN_JPEG_BYTES
    from .news_caster_preview import RemoteStopGate, download, stop_child
else:
    import ssd1b_preview_model as model
    from generated_photos import article_fingerprint, MAX_ARCHIVE_BYTES, MAX_SCENE_BYTES
    from local_scene_photos import archive_bytes, stable_seed, png_to_jpeg, pending_articles
    from github_cpu_scenes import (check_main_unchanged, BACKFILL_PRIORITY_PATHS,
                                   BACKFILL_RUN_PREFIX, MAX_DAILY_BACKFILLS, cpu_negative_prompt)
    from media_policy import jpeg_dimensions, MIN_JPEG_BYTES
    from news_caster_preview import RemoteStopGate, download, stop_child

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "RollNuts/mihassei-news"
SLUG = "indoor-beach-friends-exchange-ripples"
SOURCE_SHA = "0e2a45ebc481758adc8779d9f624d3c76c6fdc318c228cd0018ed1e3fed1bf84"
TRUSTED_REFS = {"refs/heads/main", "refs/heads/codex/ssd1b-single-preview-20261114"}
MIN_FREE = 12 * 1024**3
MAX_RSS = 13 * 1024**3
MIN_AVAILABLE = 1024**3
TIMEOUT = 48 * 60
JPEG = SLUG + ".jpg"
USER_REVISION_ID = model.USER_REVISION_ID
SEED_SCHEME = model.SEED_SCHEME
NEGATIVE_SCENE = "outdoors, ocean, hot spring, rock bath"
PROMPT = (
    "A magazine photograph of a fictional Japanese adult woman, age 27, wearing an "
    "opaque terracotta bikini with conventional coverage. A clearly indoor resort pool "
    "in November, visibly blue tiled pool and a deckchair, glass roof. Natural relaxed "
    "pose, soft daylight, realistic skin and hands."
)
PROMPT_2 = (
    "Fictional Japanese adult woman, 27, short dark brown bob with bangs, warm brown "
    "eyes and a relaxed smile. Terracotta bikini, ordinary swimwear. Indoor blue tiled "
    "resort pool, a deckchair, natural daylight. One coherent photograph, no text or logos."
)
BRIEF = PROMPT + " " + PROMPT_2
PROFILE = "ssd1b-f32-single-study-20261114-v1"
RUN_TITLE = "Photo study: " + PROFILE
AUTHORIZATION_ID = "user-requested-new-ssd1b-f32-study-20261114"
AUTHORIZATION_JST_DAY = "2026-11-14"
# Exact GET /actions/runs metadata, read 2026-11-14. Original titles and failures
# remain intact: this authorizes one different-model study, not any old retry.
PRIOR_COMPLETED_RUNS = [{'id': 43458459270,
  'status': 'completed',
  'conclusion': 'failure',
  'created_at': '2026-11-13T16:01:26Z',
  'event': 'workflow_dispatch',
  'path': '.github/workflows/free-cpu-scenes.yml',
  'head_sha': '66d301c674965ca49673ccf3a30c4929abfebff6',
  'head_branch': 'codex/sdxl-preview-20261114',
  'display_title': 'Free CPU staged-news photos',
  'run_attempt': 1},
 {'id': 43507608306,
  'status': 'completed',
  'conclusion': 'failure',
  'created_at': '2026-11-13T21:06:04Z',
  'event': 'workflow_dispatch',
  'path': '.github/workflows/free-cpu-scenes.yml',
  'head_sha': '10b3e6cb1e12f5cfb2659fb4ae5ca65ad9891b86',
  'head_branch': 'codex/sdxl-preview-20261114',
  'display_title': 'Photo backfill: indoor-beach-friends-exchange-ripples',
  'run_attempt': 1},
 {'id': 43509702255,
  'status': 'completed',
  'conclusion': 'success',
  'created_at': '2026-11-13T21:19:47Z',
  'event': 'workflow_dispatch',
  'path': '.github/workflows/free-cpu-scenes.yml',
  'head_sha': '9d08f0e3334433754db8d9ec30fd56d7af76a454',
  'head_branch': 'codex/sdxl-preview-20261114',
  'display_title': 'Photo backfill: indoor-beach-friends-exchange-ripples',
  'run_attempt': 1},
 {'id': 43514129860,
  'status': 'completed',
  'conclusion': 'failure',
  'created_at': '2026-11-13T21:49:49Z',
  'event': 'workflow_dispatch',
  'path': '.github/workflows/free-cpu-scenes.yml',
  'head_sha': 'a1bcf8cbfbd2889862469874de680e563be9bc15',
  'head_branch': 'codex/sdxl-preview-20261114',
  'display_title': 'Photo backfill: indoor-beach-friends-exchange-ripples',
  'run_attempt': 1},
 {'id': 43517761331,
  'status': 'completed',
  'conclusion': 'failure',
  'created_at': '2026-11-13T22:15:23Z',
  'event': 'workflow_dispatch',
  'path': '.github/workflows/free-cpu-scenes.yml',
  'head_sha': 'e20536943bcfcabe10d2571256a03d2f7aa88515',
  'head_branch': 'codex/sdxl-preview-20261114',
  'display_title': 'Photo backfill: indoor-beach-friends-exchange-ripples',
  'run_attempt': 1}]
QUALITY_FAILURE = "Scene too dark/washed out/unreadable for editorial use"


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
                "SSD1B_PUBLIC_REPOSITORY":"true", "PREVIEW_MODEL":"ssd1b",
                "VERIPSA_ENABLE_FREE_CPU":"1", "SCENE_SLUG":SLUG, "SCENE_LIMIT":"1",
                "RETRY_HELD":"1", "SCENE_PUBLISH":"0", "BACKFILL_SCENE":"", "BACKFILL_REFERENCE":"0"}
    ref = env.get("GITHUB_REF")
    if (any(env.get(key) != value for key, value in required.items()) or ref not in TRUSTED_REFS
            or env.get("GITHUB_WORKFLOW_REF") != f"{REPOSITORY}/.github/workflows/free-cpu-scenes.yml@{ref}"
            or not re.fullmatch(r"[0-9a-f]{40}", env.get("GITHUB_SHA", ""))
            or (system or platform.system()) != "Linux"
            or (python_version or sys.version_info[:2]) != (3, 11)):
        raise RuntimeError("SSD1B requires exact manual inputs on the public repository's trusted GitHub CPU ref")
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
        raise ValueError("Source article changed; SSD1B portrait preview stopped")
    candidate = copy.deepcopy(source)
    candidate["scene_prompt"] = BRIEF
    if article_fingerprint(candidate) == SOURCE_SHA:
        raise ValueError("Preview must have its own portrait-scene fingerprint")
    return candidate


def preview_seed(article):
    identity = f"{article['slug']}|{article['title']}|{USER_REVISION_ID}"
    return int.from_bytes(hashlib.sha256(identity.encode("utf-8")).digest()[:8], "big") % 2**63


def preview_negative_prompt(article):
    return cpu_negative_prompt(article) + ", " + NEGATIVE_SCENE


def fresh_source():
    for args in (("fetch", "--quiet", "origin", "main"),):
        subprocess.run(["git", *args], cwd=ROOT, check=True, timeout=30, capture_output=True)
    def read(path):
        return json.loads(subprocess.check_output(["git", "show", "FETCH_HEAD:" + path],
                                                  cwd=ROOT, text=True, timeout=30))
    candidate_article(read("content/articles.json"))
    return subprocess.check_output(["git", "rev-parse", "FETCH_HEAD"], cwd=ROOT, text=True, timeout=10).strip()


def quota_evidence(pages, current, attempt, now):
    """One date/profile-bound new request; normal cap and old history stay visible."""
    if attempt != 1:
        raise RuntimeError("SSD1B workflow reruns are forbidden")
    if now.tzinfo is None or not isinstance(pages, list) or not pages:
        raise ValueError("Cannot verify SSD1B quota inventory/clock")
    day = now.astimezone(timezone(timedelta(hours=9))).date().isoformat()
    if day != AUTHORIZATION_JST_DAY:
        raise RuntimeError("This new-user authorization applies only on JST 2026-11-14")
    if (MAX_DAILY_BACKFILLS != 4 or PROFILE != "ssd1b-f32-single-study-20261114-v1" or model.MODEL != "segmind/SSD-1B"
            or model.REVISION != "60987f37e94cd59c36b1cba832b9f97b57395a10"
            or model.INFERENCE["unet_compute_dtype"] != "float32"):
        raise ValueError("The bounded request does not authorize another model/profile")
    runs = []
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("workflow_runs"), list):
            raise ValueError("Invalid paginated workflow inventory")
        runs.extend(page["workflow_runs"])
    if (any(not isinstance(run, dict) for run in runs)
            or len(runs) < max(page.get("total_count", len(runs)) for page in pages)
            or len({run.get("id") for run in runs}) != len(runs)):
        raise ValueError("Incomplete/duplicate workflow inventory; quota cannot be proved")
    selected = [run for run in runs if run.get("display_title") == RUN_TITLE]
    if len(selected) != 1 or selected[0].get("id") != current or selected[0].get("run_attempt") != 1:
        raise RuntimeError("SSD1B permits exactly one new profile run, not an old trial retry")
    if (selected[0].get("event") != "workflow_dispatch"
            or selected[0].get("path") != ".github/workflows/free-cpu-scenes.yml"
            or selected[0].get("head_branch") not in ("main", "codex/ssd1b-single-preview-20261114")):
        raise ValueError("Wrong workflow provenance for SSD1B study")
    for expected in PRIOR_COMPLETED_RUNS:
        matches = [run for run in runs if run.get("id") == expected["id"]]
        if len(matches) != 1 or any(matches[0].get(key) != value for key,value in expected.items()):
            raise ValueError("The five original completed run records must match exactly")
    daily = []
    for run in runs:
        stamp = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("Unqualified quota timestamp")
        if stamp.astimezone(timezone(timedelta(hours=9))).date().isoformat() == day:
            daily.append(run["id"])
    prior_ids = [run["id"] for run in PRIOR_COMPLETED_RUNS]
    if set(daily) != {*prior_ids, current} or len(daily) != 6:
        raise RuntimeError("Only the exact five completed runs plus this one new study are authorized")
    return {"allowed":True, "profile":PROFILE, "run_id":current, "run_attempt":attempt,
            "daily_limit":MAX_DAILY_BACKFILLS, "observed_daily_attempts":len(daily),
            "normal_daily_cap_exceeded":True, "normal_cap_exceeded_by":len(daily)-MAX_DAILY_BACKFILLS,
            "counted_run_ids":daily, "prior_completed_runs":PRIOR_COMPLETED_RUNS,
            "legacy_grant_reused":False, "new_user_authorization":{
                "id":AUTHORIZATION_ID, "jst_day":AUTHORIZATION_JST_DAY, "additional_study_runs":1,
                "reason":"New user request for one different SSD-1B/F32 adult-fashion model study"}}


def check_daily_quota(*, now=None):
    if os.environ.get("USER_REQUESTED_RETRY_AFTER", ""):
        raise ValueError("Old PR722 retry grants cannot authorize SSD1B")
    pages = json.loads(subprocess.check_output([
        "gh", "api", "--paginate", "--slurp",
        f"repos/{REPOSITORY}/actions/workflows/free-cpu-scenes.yml/runs?event=workflow_dispatch&per_page=100"
    ], cwd=ROOT, text=True, timeout=30))
    return quota_evidence(pages, int(os.environ["GITHUB_RUN_ID"]), int(os.environ["GITHUB_RUN_ATTEMPT"]),
                          now or datetime.now(timezone.utc))


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


class StaleReview(RuntimeError):
    """Safe candidate exists, but current main no longer permits fresh export."""


def final_priority_gate(before, after):
    if any(not re.fullmatch(r"[0-9a-f]{40}", value) for value in (before, after)):
        raise ValueError("Cannot verify final main priority revisions")
    changed = subprocess.check_output(["git", "diff", "--no-renames", "--name-only", before, after,
                                       "--", *BACKFILL_PRIORITY_PATHS], cwd=ROOT, text=True, timeout=15).strip()
    if changed:
        raise StaleReview("Main article/image inputs advanced; safe JPEG is held for review")


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
        raise RuntimeError("Owned SSD1B worker exceeded 13 GiB RSS")
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
            raise RuntimeError("SSD1B exceeded its 48-minute controller deadline")
        self.remote.check()
        if shutil.disk_usage(self.work).free < MIN_FREE:
            raise RuntimeError("Less than 12 GiB free; SSD1B stopped")
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
        raise RuntimeError("Need 12 GiB reserve plus all pinned SSD1B/checker bytes before downloading")


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
           "OMP_NUM_THREADS":"4", "MKL_NUM_THREADS":"4", "SSD1B_CONTROL_PID":str(os.getpid()),
           "SSD1B_CONTROL_NONCE":json.loads((work / "request.json").read_text())["nonce"]}
    env.update(HF_HOME=str(work / "hf-cache"), HF_HUB_CACHE=str(work / "hf-cache/hub"),
               TORCH_HOME=str(work / "torch-cache"), XDG_CACHE_HOME=str(work / "cache"))
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker", str(work)],
                               cwd=ROOT, env=env, start_new_session=True)
    try:
        while process.poll() is None:
            budget.check(process.pid)
            time.sleep(.5)
        if process.returncode:
            raise RuntimeError("SSD1B worker failed with exit " + str(process.returncode))
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
            raise ValueError("SSD1B " + key + " exceeds its complete 77-token CLIP budget")
    return result


def load_components(base, classes, dtype):
    components = {}
    for name, cls in (("unet",classes["unet"]), ("text_encoder",classes["text_encoder"]),
                      ("text_encoder_2",classes["text_encoder_2"]), ("vae",classes["vae"])):
        components[name] = cls.from_pretrained(str(base), subfolder=name, torch_dtype=dtype,
                                              low_cpu_mem_usage=True,
                                              use_safetensors=True, local_files_only=True)
    return components


def validate_rgb(rgb, np):
    if (not isinstance(rgb, np.ndarray) or rgb.shape != (1, 576, 1024, 3)
            or not np.isfinite(rgb).all() or rgb.min() < 0 or rgb.max() > 1):
        raise ValueError("SSD1B did not return one finite RGB image")


def safety_result(rgb, flags, np):
    validate_rgb(rgb, np)
    if not verified_safety_flags(flags):
        raise ValueError("Existing safety checker rejected or did not verify the SSD1B preview")


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
    """Use CPU F32; release encoders before denoising and UNet before VAE decode."""
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
        if pipe.unet.dtype != torch.float32:
            raise ValueError("SSD1B requires its explicit F32 profile; no dtype fallback")
        progress(work, "unet_float32", text_encoders_released=True)
        positive, negative_embeds, pooled, negative_pooled = [value.to(dtype=torch.float32) for value in encoded]
        progress(work, "denoising_started", unet_compute_dtype="float32")
        latents = pipe(prompt_embeds=positive, negative_prompt_embeds=negative_embeds,
                       pooled_prompt_embeds=pooled, negative_pooled_prompt_embeds=negative_pooled,
                       width=settings["width"], height=settings["height"],
                       num_inference_steps=settings["num_inference_steps"], guidance_scale=settings["guidance_scale"],
                       num_images_per_prompt=1, generator=torch.Generator(device="cpu").manual_seed(seed),
                       output_type="latent").images
        if (not torch.is_tensor(latents) or tuple(latents.shape) != (1,4,72,128)
                or not torch.isfinite(latents).all()):
            raise ValueError("SSD1B denoising returned invalid/nonfinite latents")
        pipe.unet = None
        components.pop("unet")
        gc.collect()
        progress(work, "vae_float32_decode", unet_released=True, vae_decode_dtype="float32")
        pipe.vae.to(dtype=torch.float32)
        if (getattr(pipe.vae.config,"latents_mean",None) is not None
                or getattr(pipe.vae.config,"latents_std",None) is not None):
            raise ValueError("Unexpected latent normalization for the pinned SSD1B base VAE")
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
    if (os.environ.get("SSD1B_CONTROL_PID") != str(os.getppid())
            or os.environ.get("SSD1B_CONTROL_NONCE") != request.get("nonce")
            or not request.get("nonce") or os.getpgrp() != os.getpid()):
        raise RuntimeError("Worker requires its owning stdlib controller")
    candidate = request["candidate"]
    if (candidate.get("slug") != SLUG or candidate.get("scene_prompt") != BRIEF
            or article_fingerprint(candidate) != request["candidate_article_sha256"]
            or request.get("seed") != preview_seed(candidate)
            or request.get("user_revision_id") != USER_REVISION_ID or request.get("seed_scheme") != SEED_SCHEME):
        raise ValueError("Worker candidate differs from its source-bound portrait brief")
    try:
        render_worker(work, request, candidate)
    except BaseException as error:
        progress(work, "worker_failed", failure_type=type(error).__name__, failure=str(error)[:300])
        raise


def save_unreviewed_output(checked, flags, np, image_type, work, quality_check):
    observed = flags if isinstance(flags, list) and all(type(flag) is bool for flag in flags) else None
    try:
        safety_result(checked, flags, np)
    except BaseException:
        progress(work, "safety_checked", safety_flags=observed, safety_checked=False)
        raise
    progress(work, "safety_checked", safety_flags=flags, safety_checked=True)
    image = image_type.fromarray((checked[0] * 255).round().astype("uint8"), mode="RGB")
    png = io.BytesIO(); image.save(png, format="PNG")
    jpeg = png_to_jpeg(png.getvalue())
    if not MIN_JPEG_BYTES <= len(jpeg) <= MAX_SCENE_BYTES or jpeg_dimensions(jpeg) != (1200,675):
        raise ValueError("Safety-checked candidate failed JPEG size or dimension limits")
    (work / "rendered.jpg").write_bytes(jpeg)
    warning = {}
    try:
        quality_check(image)
    except ValueError as error:
        if str(error) != QUALITY_FAILURE:
            raise
        warning["quality_warning"] = str(error)
    progress(work, "worker_succeeded", safety_flags=flags, safety_checked=True, **warning)
    atomic_json(work / "result.json", {"status":"generated_unreviewed", "safety_flags":flags,
                                       "sha256":hashlib.sha256(jpeg).hexdigest(), "bytes":len(jpeg), **warning})


def render_worker(work, request, candidate):
    runner_gate()
    progress(work, "cpu_float32_profile", compute_dtype="float32", profile=PROFILE,
             user_revision_id=USER_REVISION_ID, seed_scheme=SEED_SCHEME, seed=request["seed"])
    progress(work, "import_started", model_import_started=True)
    import torch
    import numpy as np
    from PIL import Image
    from diffusers import AutoencoderKL, UNet2DConditionModel, StableDiffusionXLPipeline, EulerDiscreteScheduler
    from diffusers.pipelines.stable_diffusion import StableDiffusionSafetyChecker
    from transformers import CLIPTextModel, CLIPTextModelWithProjection, CLIPTokenizer, CLIPImageProcessor
    from importlib.metadata import version
    torch.set_num_threads(4); torch.set_num_interop_threads(1)
    base, safety = work / "base", work / "safety"
    tokenizer = CLIPTokenizer.from_pretrained(str(base), subfolder="tokenizer", local_files_only=True)
    tokenizer_2 = CLIPTokenizer.from_pretrained(str(base), subfolder="tokenizer_2", local_files_only=True)
    if __package__:
        from .github_cpu_scenes import check_quality
    else:
        from github_cpu_scenes import check_quality
    negative = preview_negative_prompt(candidate)
    counts = token_counts(tokenizer, tokenizer_2, negative)
    progress(work, "tokens_verified", token_counts=counts, negative_prompt=negative,
             packages={name:version(name) for name in ("torch","diffusers","transformers","accelerate","safetensors")})
    components = load_components(base, {"unet":UNet2DConditionModel, "vae":AutoencoderKL,
                                        "text_encoder":CLIPTextModel, "text_encoder_2":CLIPTextModelWithProjection}, torch.float32)
    settings = model.INFERENCE
    scheduler = EulerDiscreteScheduler.from_pretrained(
        str(base), subfolder="scheduler", local_files_only=True)
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
    save_unreviewed_output(checked, flags, np, Image, work, check_quality)



def stage_safe_candidate(work, output, receipt):
    """Retain only complete, safety-checked finite/format-verified worker results."""
    result = json.loads((work / "result.json").read_text())
    image = (work / "rendered.jpg").read_bytes()
    if (result.get("status") != "generated_unreviewed" or not verified_safety_flags(result.get("safety_flags"))
            or ("quality_warning" in result and result["quality_warning"] != QUALITY_FAILURE)
            or not MIN_JPEG_BYTES <= len(image) <= MAX_SCENE_BYTES
            or jpeg_dimensions(image) != (1200,675)
            or result.get("sha256") != hashlib.sha256(image).hexdigest() or result.get("bytes") != len(image)):
        raise ValueError("Worker output failed safety, source, JPEG or size verification")
    stage = output / (JPEG + ".tmp"); stage.write_bytes(image); os.replace(stage, output / JPEG)
    receipt.update(status="generated_unreviewed", jpeg_sha256=result["sha256"],
                   jpeg_bytes=len(image), final_priority_clear=False, freshness="unverified")
    if "quality_warning" in result:
        receipt["quality_warning"] = result["quality_warning"]
    atomic_json(output / "receipt.json", receipt)


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
    receipt = {"version":1, "publication":"preview_only", "purpose":"user_requested_model_study", "profile":PROFILE, "status":"started",
               "run_id":os.environ.get("GITHUB_RUN_ID"), "source_article_sha256":SOURCE_SHA,
               "article_slug":SLUG, "model":model.MODEL, "model_revision":model.REVISION,
               "license":model.LICENSE_URL, "license_sha256":model.LICENSE_SHA256,
               "model_card":model.MODEL_CARD_URL, "model_card_sha256":model.MODEL_CARD_SHA256,
               "license_text_url":model.LICENSE_TEXT_URL, "license_text_revision":model.LICENSE_TEXT_REVISION,
               "license_text_sha256":model.LICENSE_TEXT_SHA256, "inference":model.INFERENCE,
               "user_revision_id":USER_REVISION_ID, "seed_scheme":SEED_SCHEME,
               "prompt":PROMPT, "prompt_2":PROMPT_2, "download_started":False,
               "models_downloaded":False, "model_import_started":False,
               "model_execution_started":False, "model_generation_completed":False, "verified_files":{}}
    started = time.monotonic(); remote = RemoteStopGate(); budget = None
    safe_candidate_saved = False
    atomic_json(output / "receipt.json", receipt)
    try:
        remote.check()
        receipt["daily_quota"] = check_daily_quota()
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=10).strip()
        if head != os.environ["GITHUB_SHA"]:
            raise ValueError("Checkout must equal the exact dispatched github.sha")
        receipt["checkout_sha"] = head
        receipt["workflow_ref"] = os.environ["GITHUB_WORKFLOW_REF"]
        check_disk_reservation(temporary)
        articles = json.loads((ROOT / "content/articles.json").read_text())
        candidate = candidate_article(articles)
        receipt.update(candidate_article_sha256=article_fingerprint(candidate), seed=preview_seed(candidate),
                       fresh_main_sha_before=fresh_source())
        initial_priority_gate(receipt["fresh_main_sha_before"], articles, review_parent=temporary)
        receipt["initial_other_priority_clear"] = True
        atomic_json(output / "articles.json", [candidate])
        with tempfile.TemporaryDirectory(prefix="ssd1b-preview-", dir=temporary) as directory:
            work = Path(directory); budget = Budget(work, remote, revision=receipt["fresh_main_sha_before"]); budget.check()
            if check_only:
                receipt["status"] = "ready_unexecuted"
                return
            try:
                download_files(work, budget, receipt, output)
                atomic_json(work / "request.json", {"nonce":uuid.uuid4().hex, "candidate":candidate,
                            "candidate_article_sha256":receipt["candidate_article_sha256"], "seed":receipt["seed"],
                            "user_revision_id":USER_REVISION_ID, "seed_scheme":SEED_SCHEME})
                run_owned_worker(work, budget)
                stage_safe_candidate(work, output, receipt)
                safe_candidate_saved = True
                budget.check(); budget.finish_priority(); remote.next_check = 0; remote.check()
                receipt["fresh_main_sha_after"] = fresh_source()
                final_priority_gate(receipt["fresh_main_sha_before"], receipt["fresh_main_sha_after"])
                receipt.update(final_priority_clear=True, freshness="verified")
            except BaseException:
                # A stop can race worker exit after a complete safe result exists.
                # The owned worker has stopped; only the same full checks may retain it.
                if not safe_candidate_saved and (work / "result.json").is_file():
                    try:
                        stage_safe_candidate(work, output, receipt)
                        safe_candidate_saved = True
                    except (OSError, ValueError, TypeError):
                        pass
                raise
            finally:
                path = work / "progress.json"
                if path.is_file():
                    receipt["worker"] = json.loads(path.read_text())
                    for key in ("model_import_started", "model_execution_started", "model_generation_completed"):
                        receipt[key] = receipt["worker"].get(key, False)
    except BaseException as error:
        if safe_candidate_saved:
            receipt.update(status="held_unreviewed", final_priority_clear=False,
                           freshness="stale" if isinstance(error, StaleReview) else "unverified",
                           hold_reason="Post-generation priority/resource/stop verification did not clear")
        else:
            (output / JPEG).unlink(missing_ok=True)
            receipt["status"] = "failed"
        receipt.update(failure_type=type(error).__name__, failure=str(error)[:300])
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
        print("SSD1B preview stopped:", type(error).__name__, str(error), file=sys.stderr)
        raise SystemExit(1)
