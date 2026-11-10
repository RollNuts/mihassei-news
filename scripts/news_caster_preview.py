#!/usr/bin/env python3
"""One manual, artifact-only CPU talking-presenter experiment; never publishes.

Ordinary invocation is an offline plan. Downloads and inference are restricted
to this public repository's disposable GitHub Linux CPU runner. Existing silent
clips, article text, media manifests, schedules, and the Mac are not modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

if __package__:
    from .media_policy import jpeg_dimensions
    from .generated_photos import article_fingerprint
else:
    from media_policy import jpeg_dimensions
    from generated_photos import article_fingerprint

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "RollNuts/mihassei-news"
SECONDS = 3
# Leave checkout/setup and artifact upload room in the job's 15-minute limit.
TIMEOUT = 12 * 60
MIN_FREE = 12 * 1024**3
MAX_WORK = 8 * 1024**3
MAX_ARTIFACT = 4 * 1024**2
MAX_PORTRAIT = 700 * 1024
TEXT = "夜の美術展で、警備ロボが光の人影に警告しました。"
ARTICLE_SLUG = "night-art-exhibition-thirty-two-shadow-intruders"
SADTALKER_REV = "cd4c0465ae0b54a6f85af57f5c65fec9fe23e7f8"
KOKORO_REV = "dfb907a02bba8152ca444717ca5d78747ccb4bec"
MISAKI_REV = "fba1236595f2d2bf21d414ba6e57d25256afada3"
MODEL_REV = "f3ff3571791e39611d31c381e3a41a3af07b4987"
HF = "https://huggingface.co/hexgrad/Kokoro-82M/resolve/" + MODEL_REV + "/"

# Upstream Apache-2.0 project explicitly removed its non-commercial restriction.
# The detector/alignment dependencies are upstream MIT/Apache-2.0 components.
# These 2023 release assets expose no publisher SHA256: size/id are pinned,
# and their *observed* download hashes are recorded, never labelled vendor proof.
RELEASES = (
    ("OpenTalker/SadTalker", "v0.0.2-rc", "SadTalker_V0.0.2_256.safetensors", 110648281, 725066984, "checkpoints"),
    ("OpenTalker/SadTalker", "v0.0.2-rc", "mapping_00109-model.pth.tar", 110649359, 155779231, "checkpoints"),
    ("xinntao/facexlib", "v0.1.0", "alignment_WFLW_4HG.pth", 38201318, 193670248, "gfpgan/weights"),
    ("xinntao/facexlib", "v0.1.0", "detection_Resnet50_Final.pth", 38201344, 109497761, "gfpgan/weights"),
)
KOKORO_FILES = (
    ("kokoro-v1_0.pth", 327212226, "496dba118d1a58f5f3db2efc88dbdc216e0483fc89fe6e47ee1f2c53f18ad1e4"),
    ("voices/jf_alpha.pt", 523425, "1bf4c9dc69e45ee46183b071f4db766349aac5592acbcfeaf051018048a5d787"),
    ("config.json", 2351, "5abb01e2403b072bf03d04fde160443e209d7a0dad49a423be15196b9b43c17f"),
)
LICENSES = {
    "sadtalker": "https://github.com/OpenTalker/SadTalker/blob/" + SADTALKER_REV + "/LICENSE",
    "kokoro_model": "https://huggingface.co/hexgrad/Kokoro-82M/tree/" + MODEL_REV,
    "kokoro_code": "https://github.com/hexgrad/kokoro/blob/" + KOKORO_REV + "/LICENSE",
    "facexlib": "https://github.com/xinntao/facexlib/blob/master/LICENSE",
    "alignment": "https://github.com/protossw512/AdaptiveWingLoss/blob/master/LICENSE",
    "retinaface": "https://github.com/biubug6/Pytorch_Retinaface/blob/master/LICENSE.MIT",
}

SAD_CONSTRAINTS = """numpy==1.23.4
numba==0.57.1
llvmlite==0.40.1
opencv-python==4.8.1.78
gfpgan==1.3.8
av==12.3.0
safetensors==0.4.5
Pillow==10.4.0
"""
TTS_CONSTRAINTS = """numpy==1.26.4
transformers==4.46.3
spacy==3.7.5
spacy-curated-transformers==0.2.2
huggingface-hub==0.26.5
soundfile==0.12.1
pyopenjtalk==0.4.1
"""

# Explicit local files prevent hidden model downloads. pyopenjtalk G2P avoids
# Misaki's default full UniDic dictionary; its small official JTalk dictionary
# is obtained by the pinned package on the disposable runner only.
TTS_PROGRAM = r'''
import sys, json, wave
import numpy as np
import torch
from kokoro import KModel
from misaki.ja import JAG2P
torch.set_num_threads(2)
assert not torch.cuda.is_available()
base, output, text = sys.argv[1:]
g2p = JAG2P(version="pyopenjtalk")
phonemes, _ = g2p(text)
if not phonemes or len(phonemes) >= 500:
    raise RuntimeError("Invalid bounded Japanese phonemes")
model = KModel(config=base+"/config.json", model=base+"/kokoro-v1_0.pth").to("cpu").eval()
voice = torch.load(base+"/voices/jf_alpha.pt", map_location="cpu", weights_only=True)
with torch.no_grad():
    samples = model(phonemes, voice[len(phonemes)-1], speed=1).numpy()
if not np.isfinite(samples).all() or not 0.3 <= len(samples)/24000 <= 20:
    raise RuntimeError("Invalid or excessive narration duration")
pcm=(np.clip(samples,-1,1)*32767).astype("<i2")
with wave.open(output,"wb") as f:
    f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(pcm.tobytes())
'''


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def validate_portrait(image: Path, receipt_path: Path) -> dict:
    if image.is_symlink() or not image.is_file() or not 1024 <= image.stat().st_size <= MAX_PORTRAIT:
        raise ValueError("A bounded regular JPEG portrait is required")
    data = image.read_bytes()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    dimensions = jpeg_dimensions(data)
    if not isinstance(receipt, dict):
        raise ValueError("Portrait receipt must be an object")
    if (not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9")
            or dimensions is None or min(dimensions) < 512 or max(dimensions) > 2048
            or receipt.get("version") != 1
            or receipt.get("fictional_person") is not True
            or type(receipt.get("intended_age")) is not int
            or not 25 <= receipt["intended_age"] <= 35
            or receipt.get("publication") != "preview_only"
            or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("source_png_sha256", "")))
            or not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", str(receipt.get("generation_id", "")))
            or not all(isinstance(receipt.get(k), str) and receipt[k].strip()
                       for k in ("generation_id_source", "generation_receipt", "generation_prompt"))
            or receipt.get("portrait_sha256") != digest(image)):
        raise ValueError("Portrait must match an adult fictional preview-only receipt")
    return receipt


def cap_partial_artifacts(output: Path) -> list[str]:
    """Discard only our own partial media if a failed encode exceeds the cap."""
    files = [output / name for name in ("presenter-preview.mp4", "narration-full.wav", "narration-three-seconds.wav")]
    files = [path for path in files if path.is_file() and not path.is_symlink()]
    discarded = []
    # Receipt metadata (including resolved package versions) retains 128 KiB.
    for path in sorted(files, key=lambda item: item.stat().st_size, reverse=True):
        if sum(item.stat().st_size for item in files if item.exists()) <= MAX_ARTIFACT - 128 * 1024:
            break
        path.unlink()
        discarded.append(path.name)
    return discarded


def runner_gate(env=None, *, system=None, python_version=None) -> Path:
    env = os.environ if env is None else env
    if (env.get("GITHUB_ACTIONS") != "true" or env.get("GITHUB_REPOSITORY") != REPOSITORY
            or env.get("NEWS_CASTER_PUBLIC_REPOSITORY") != "true"
            or env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or (system or platform.system()) != "Linux"
            or (python_version or sys.version_info[:2]) != (3, 10)
            or env.get("RUNNER_ENVIRONMENT") != "github-hosted"):
        raise RuntimeError("Preview execution requires a manual public GitHub-hosted Linux Python 3.10 runner")
    if any(Path(p).exists() for p in ("/dev/nvidia0", "/dev/kfd")):
        raise RuntimeError("GPU devices are forbidden in this CPU preview")
    temporary = Path(env.get("RUNNER_TEMP", ""))
    if not temporary.is_absolute() or not temporary.is_dir():
        raise RuntimeError("A disposable RUNNER_TEMP is required")
    return temporary.resolve()


class RemoteStopGate:
    """Reuse the existing all-image stop switch; never resume a workflow."""
    def __init__(self):
        self.next_check = 0

    def check(self):
        if time.monotonic() < self.next_check:
            return
        result = subprocess.run(
            ["gh", "api", f"repos/{REPOSITORY}/actions/workflows/free-cpu-scenes.yml", "--jq", ".state"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode != 0 or result.stdout.strip() != "active":
            raise RuntimeError("Image workflow stopped or status unavailable; preview halted")
        self.next_check = time.monotonic() + 45


class Budget:
    def __init__(self, work: Path, timeout=TIMEOUT, remote=None):
        self.work, self.deadline = work, time.monotonic() + timeout
        self.remote, self.next_tree_check = remote, 0

    def check(self):
        if time.monotonic() >= self.deadline:
            raise RuntimeError("Preview exceeded its bounded runtime deadline")
        if shutil.disk_usage(self.work).free < MIN_FREE:
            raise RuntimeError("Less than 12 GiB free; preview stopped")
        if time.monotonic() >= self.next_tree_check:
            if tree_bytes(self.work) > MAX_WORK:
                raise RuntimeError("Preview work exceeded its 8 GiB cap")
            self.next_tree_check = time.monotonic() + 5
        if self.remote:
            self.remote.check()


def stop_child(process):
    """Only terminate the process group this runner created, never other Python."""
    if process.poll() is None and os.getpgid(process.pid) == process.pid:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if process.poll() is None and os.getpgid(process.pid) == process.pid:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def command(argv, budget: Budget, *, cwd=None, env=None, capture=False):
    budget.check()
    process = subprocess.Popen(argv, cwd=cwd, env=env, start_new_session=True,
                               stdout=subprocess.PIPE if capture else None, text=True)
    try:
        while process.poll() is None:
            budget.check()
            time.sleep(1)
        if process.returncode:
            raise RuntimeError("Preview subprocess failed with exit " + str(process.returncode))
        return process.stdout.read() if capture else ""
    except BaseException:
        stop_child(process)
        raise


def allowed_url(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    return (parsed.scheme == "https" and not parsed.username and not parsed.password
            and (host in {"github.com", "api.github.com", "codeload.github.com", "raw.githubusercontent.com",
                          "release-assets.githubusercontent.com", "objects.githubusercontent.com", "huggingface.co"}
                 or host.endswith(".hf.co") or host.endswith(".huggingface.co")))


def download(url: str, destination: Path, budget: Budget, *, size=None, sha256=None, max_bytes=None):
    if not allowed_url(url):
        raise ValueError("Download is not from a permitted official source")
    budget.check()
    destination.parent.mkdir(parents=True, exist_ok=True)
    maximum = size if size is not None else max_bytes
    if maximum is None:
        raise ValueError("Every download needs an explicit size budget")
    with urlopen(Request(url, headers={"User-Agent": "Mihassei-Manual-CPU-Preview/1.0"}), timeout=20) as response:
        if not allowed_url(response.url):
            raise ValueError("Unexpected download redirect")
        received = 0
        with destination.open("xb") as output:
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if received > maximum:
                    raise ValueError("Download exceeds pinned size")
                budget.check()
                output.write(chunk)
    observed = digest(destination)
    if (size is not None and received != size) or (sha256 is not None and observed != sha256):
        raise ValueError("Downloaded model failed size or publisher-hash verification")
    return {"url": url, "bytes": received, "sha256": observed,
            "sha256_evidence": "pinned_publisher_lfs_sha256" if sha256 else "observed_download_only"}


def source_archive(repo, revision, work, budget):
    archive = work / (repo.split("/")[-1] + ".tar.gz")
    record = download(f"https://codeload.github.com/{repo}/tar.gz/{revision}", archive,
                      budget, max_bytes=100 * 1024**2)
    destination = work / repo.split("/")[-1]
    destination.mkdir()
    with tarfile.open(archive) as source:
        for member in source:
            parts = Path(member.name).parts
            if any(p in ("..", "") for p in parts) or member.name.startswith("/") or member.issym() or member.islnk():
                raise ValueError("Unsafe upstream archive member")
            relative = Path(*parts[1:])
            # Do not import unrelated upstream example faces/audio/video.
            if not relative.parts or relative.parts[0] in {"examples", ".git", ".github"}:
                continue
            target = destination / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                if member.size > 40 * 1024**2:
                    raise ValueError("Unexpected source file size")
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as data, target.open("xb") as output:
                    shutil.copyfileobj(data, output)
            budget.check()
    return destination, record


def execute(portrait: Path, portrait_receipt: Path, output: Path):
    temporary = runner_gate()
    portrait_record = validate_portrait(portrait, portrait_receipt)
    remote_stop = RemoteStopGate()
    remote_stop.check()
    if shutil.disk_usage(temporary).free < MIN_FREE:
        raise RuntimeError("Less than 12 GiB free; preview stopped")
    output = output.resolve()
    if not output.is_relative_to(temporary) or output.exists():
        raise ValueError("Output must be a new directory inside RUNNER_TEMP")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("Existing FFmpeg and ffprobe are required")
    article = next((a for a in json.loads((ROOT / "content/articles.json").read_text(encoding="utf-8"))
                    if a["slug"] == ARTICLE_SLUG), None)
    if article is None:
        raise ValueError("Preview source article is unavailable")
    output.mkdir()
    receipt = {"version": 1, "publication": "preview_only", "status": "started", "seconds": SECONDS,
               "full_narration_text": TEXT, "article_slug": ARTICLE_SLUG, "voice": "jf_alpha",
               "article_sha256": article_fingerprint(article), "article_title": article["title"],
               "portrait": portrait_record, "licenses": LICENSES, "downloads": [], "steps": []}
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="mihassei-caster-", dir=temporary) as folder:
            work = Path(folder)
            budget = Budget(work, remote=remote_stop)
            env = {**os.environ, "CUDA_VISIBLE_DEVICES": "", "PYTORCH_ENABLE_MPS_FALLBACK": "0",
                   "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2",
                   "PIP_NO_CACHE_DIR": "1", "HF_HOME": str(work / "hf"), "TORCH_HOME": str(work / "torch"),
                   "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
                   "XDG_CACHE_HOME": str(work / "cache"), "PYTHONUNBUFFERED": "1"}

            def step(name, argv, *, cwd=None, capture=False):
                receipt["current_step"] = name
                print("Preview step:", name, flush=True)
                before = time.monotonic()
                result = command(argv, budget, cwd=cwd, env=env, capture=capture)
                receipt["steps"].append({"name": name, "seconds": round(time.monotonic()-before, 3)})
                return result

            receipt["current_step"] = "download_pinned_sources"
            sources = {}
            for repo, revision in (("OpenTalker/SadTalker", SADTALKER_REV), ("hexgrad/kokoro", KOKORO_REV), ("hexgrad/misaki", MISAKI_REV)):
                sources[repo], record = source_archive(repo, revision, work, budget)
                receipt["downloads"].append({**record, "revision": revision})
            sad = sources["OpenTalker/SadTalker"]
            tts_python, sad_python = [work / name / "bin/python" for name in ("tts-env", "sad-env")]
            for interpreter in (tts_python, sad_python):
                step("create_venv", [sys.executable, "-m", "venv", str(interpreter.parents[1])])
                step("pin_pip", [str(interpreter), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-cache-dir", "pip==24.3.1", "setuptools==75.6.0", "wheel==0.45.1"])
            step("install_cpu_tts_torch", [str(tts_python), "-m", "pip", "install", "--quiet", "--index-url", "https://download.pytorch.org/whl/cpu", "torch==2.5.1+cpu"])
            step("install_cpu_sad_torch", [str(sad_python), "-m", "pip", "install", "--quiet", "--index-url", "https://download.pytorch.org/whl/cpu", "torch==1.12.1+cpu", "torchvision==0.13.1+cpu", "torchaudio==0.12.1"])
            for name, text in (("tts-constraints.txt", TTS_CONSTRAINTS), ("sad-constraints.txt", SAD_CONSTRAINTS)):
                (work / name).write_text(text, encoding="utf-8")
            step("install_tts", [str(tts_python), "-m", "pip", "install", "--quiet", "-c", str(work / "tts-constraints.txt"), str(sources["hexgrad/misaki"]) + "[ja,en]", str(sources["hexgrad/kokoro"]), "soundfile==0.12.1"])
            # The upstream gradio dependency serves its web UI; this CLI-only
            # experiment never starts that UI or installs it.
            sad_requirements = work / "sad-cli-requirements.txt"
            sad_requirements.write_text("\n".join(line for line in (sad / "requirements.txt").read_text().splitlines()
                                                   if not line.strip().startswith("gradio"))+"\n", encoding="utf-8")
            step("install_sad", [str(sad_python), "-m", "pip", "install", "--quiet", "-c", str(work / "sad-constraints.txt"), "-r", str(sad_requirements)])
            for name, interpreter in (("tts", tts_python), ("sadtalker", sad_python)):
                receipt[name + "_packages"] = step("record_"+name+"_packages", [str(interpreter), "-m", "pip", "freeze"], capture=True).splitlines()
            receipt["current_step"] = "download_models"
            kokoro = work / "kokoro-model"
            for name, size, expected in KOKORO_FILES:
                receipt["downloads"].append(download(HF+name, kokoro / name, budget, size=size, sha256=expected))
            for repo, tag, name, asset_id, size, subdir in RELEASES:
                metadata = work / (name + ".metadata.json")
                download(f"https://api.github.com/repos/{repo}/releases/assets/{asset_id}", metadata, budget, max_bytes=20000)
                asset = json.loads(metadata.read_text())
                url = f"https://github.com/{repo}/releases/download/{tag}/{name}"
                if (asset.get("id"), asset.get("name"), asset.get("size"), asset.get("browser_download_url")) != (asset_id, name, size, url):
                    raise ValueError("Official release asset identity changed")
                receipt["downloads"].append({**download(url, sad / subdir / name, budget, size=size), "release_asset_id": asset_id, "publisher_checksum_provided": False})
            tts_script = work / "synthesize.py"
            tts_script.write_text(TTS_PROGRAM, encoding="utf-8")
            step("synthesize_narration", [str(tts_python), str(tts_script), str(kokoro), str(output / "narration-full.wav"), TEXT])
            step("bound_audio_to_three_seconds", ["ffmpeg", "-nostdin", "-v", "error", "-i", str(output / "narration-full.wav"), "-af", "apad,afade=t=out:st=2.9:d=0.1", "-t", str(SECONDS), "-ar", "16000", "-ac", "1", str(output / "narration-three-seconds.wav")])
            step("animate_cpu_presenter", [str(sad_python), str(sad / "inference.py"), "--cpu", "--still", "--size", "256", "--batch_size", "1", "--preprocess", "full", "--source_image", str(portrait.resolve()), "--driven_audio", str(output / "narration-three-seconds.wav"), "--checkpoint_dir", str(sad / "checkpoints"), "--result_dir", str(work / "result")], cwd=sad)
            movies = list((work / "result").glob("*.mp4"))
            if len(movies) != 1:
                raise RuntimeError("Expected exactly one completed preview MP4")
            step("encode_preview", ["ffmpeg", "-nostdin", "-v", "error", "-i", str(movies[0]), "-t", str(SECONDS), "-vf", "scale=640:640:force_original_aspect_ratio=decrease,setsar=1", "-r", "25", "-c:v", "libx264", "-threads", "1", "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(output / "presenter-preview.mp4")])
            probe = json.loads(step("verify_preview", ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output / "presenter-preview.mp4")], capture=True))
            video = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
            audio = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
            if (len(video) != 1 or len(audio) != 1 or video[0].get("codec_name") != "h264"
                    or audio[0].get("codec_name") != "aac" or not 2.9 <= float(probe["format"]["duration"]) <= 3.2
                    or max(video[0]["width"], video[0]["height"]) > 640):
                raise RuntimeError("Preview format validation failed")
            if tree_bytes(output) > MAX_ARTIFACT - 128 * 1024:
                raise RuntimeError("Preview artifact exceeds 4 MiB")
            receipt.update(status="generated_unreviewed", preview_sha256=digest(output / "presenter-preview.mp4"), ffprobe=probe,
                           notice="Three-second technical excerpt, not a completed news bulletin. Human audio/visual review required; do not publish automatically.")
    except BaseException as exc:
        receipt.update(status="failed", failure_type=type(exc).__name__)
        raise
    finally:
        discarded = cap_partial_artifacts(output)
        if discarded:
            receipt.update(status="failed", discarded_partial_outputs=discarded,
                           failure_type="ArtifactSizeLimit")
        receipt["elapsed_seconds"] = round(time.monotonic()-started, 3)
        (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--portrait", type=Path, default=ROOT / "assets/casters/newsreader-preview.jpg")
    parser.add_argument("--portrait-receipt", type=Path, default=ROOT / "assets/casters/newsreader-preview.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not args.run:
        print(json.dumps({"publication": "preview_only", "seconds": SECONDS, "timeout_seconds": TIMEOUT,
                          "cpu_only": True, "downloads": "none in plan mode", "licenses": LICENSES}, ensure_ascii=False, indent=2))
        return 0
    if args.output is None:
        parser.error("--run requires --output inside RUNNER_TEMP")
    execute(args.portrait, args.portrait_receipt, args.output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print("Presenter preview stopped:", type(error).__name__, str(error), file=sys.stderr)
        raise SystemExit(1)
