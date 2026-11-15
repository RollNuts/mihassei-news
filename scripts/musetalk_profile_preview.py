"""One explicit, artifact-only MuseTalk core experiment on public Linux CPU."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time

if __package__:
    from . import news_caster_preview as runtime
    from .profile_caster_input import resolve_profile_input
    from .musetalk_preview_sources import SRC_FILES, WEIGHTS, input_manifest
else:
    import news_caster_preview as runtime
    from profile_caster_input import resolve_profile_input
    from musetalk_preview_sources import SRC_FILES, WEIGHTS, input_manifest

ROOT = Path(__file__).resolve().parents[1]
SPOKEN_LINE = "え、もうこんな時間？"
IMAGE_SHA = "101edc530c7f6321b70369f60cfe282aa9304283c84b767c38f47cf29168f1a4"
# Manually selected after displaying the full, accepted 1200x800 cafe photograph.
# Only the lower-face ellipse is blended; hair, eyes, hand and body stay original.
RECIPE = {"source_dimensions": [1200, 800], "face_box": [480, 80, 870, 470],
          "mask": {"shape": "ellipse", "center": [0.60, 0.61], "radii": [0.18, 0.13],
                   "blur_sigma_px": 8, "right_edge": 0.80, "right_feather": 0.04},
          "fps": 10, "frames": 30, "seconds": 3, "seed": 0, "dtype": "float32", "batch_size": 1}
ARCHIVES = {"hexgrad/kokoro": (runtime.KOKORO_REV, "25afd8e26050a13afa026d688e07284ac659c929655dba056d7c6e7706e68bec"),
            "hexgrad/misaki": (runtime.MISAKI_REV, "d36a94442077faf9c37965ea14220cd0c1f4f2550b0340fb236a462f08b67818")}


class PreviewBudget(runtime.Budget):
    def check(self):
        super().check()
        # A whole public runner belongs to this one job. Keep RAM headroom for
        # logs/upload; do not wait for the kernel to kill a model process.
        memory = Path("/proc/meminfo")
        if not memory.is_file():
            raise RuntimeError("Linux memory information is unavailable")
        available = next((int(line.split()[1]) * 1024 for line in memory.read_text().splitlines()
                          if line.startswith("MemAvailable:")), 0)
        if available < 2 * 1024**3:
            raise RuntimeError("Less than 2 GiB available RAM; preview stopped")


def plan(*, root=ROOT, spoken_line=SPOKEN_LINE):
    if spoken_line != SPOKEN_LINE:
        raise ValueError("This one pilot uses only Rio's explicitly supplied short line")
    source = resolve_profile_input("shioba-rio", spoken_line, root=root)
    if source["context"]["source_image_sha256"] != IMAGE_SHA:
        raise ValueError("The manually reviewed cafe photograph changed")
    return {"backend": "musetalk-core", "publication": "preview_only", "publish": False,
            "status": "planned", "cpu_only": True, "spoken_line": spoken_line,
            "repository_revision": os.environ.get("GITHUB_SHA"),
            "recipe": RECIPE, "inputs": input_manifest(), **source["context"],
            "portrait": source["portrait_record"], "voice": "jf_alpha",
            "kokoro_revision": runtime.MODEL_REV, "kokoro_files": runtime.KOKORO_FILES,
            "kokoro_license": runtime.LICENSES["kokoro_model"],
            "kokoro_code_license": runtime.LICENSES["kokoro_code"],
            "misaki_code_license": "https://github.com/hexgrad/misaki/blob/" + runtime.MISAKI_REV + "/LICENSE",
            "limits": {"cpu_threads": 2, "minimum_free_disk_gib": 12, "minimum_available_ram_gib": 2,
                       "maximum_work_gib": 8, "maximum_artifact_bytes": runtime.MAX_ARTIFACT,
                       "maximum_log_bytes": 512 * 1024},
            "timeout_seconds": runtime.TIMEOUT, "downloads": "none in plan mode"}


def logged_command(argv, budget, log_path, *, env):
    budget.check()
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(argv, env=env, start_new_session=True, stdout=log,
                                   stderr=subprocess.STDOUT, text=True)
        try:
            while process.poll() is None:
                budget.check()
                if log_path.stat().st_size > 512 * 1024:
                    raise RuntimeError("Preview log exceeded 512 KiB")
                time.sleep(1)
            if process.returncode:
                raise RuntimeError("Preview subprocess failed with exit " + str(process.returncode))
        except BaseException:
            runtime.stop_child(process)
            raise


def execute(output, *, root=ROOT, spoken_line=SPOKEN_LINE):
    temporary = runtime.runner_gate()  # Reject Mac before any file/network/model work.
    receipt = plan(root=root, spoken_line=spoken_line)
    source = resolve_profile_input("shioba-rio", spoken_line, root=root)
    remote = runtime.RemoteStopGate(); remote.check()
    output = Path(output).resolve()
    if not output.is_relative_to(temporary) or output.exists():
        raise ValueError("Output must be a new directory inside RUNNER_TEMP")
    if shutil.disk_usage(temporary).free < runtime.MIN_FREE:
        raise RuntimeError("Less than 12 GiB free; preview stopped")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("Existing FFmpeg/ffprobe required")
    output.mkdir()
    receipt.update(status="started", downloads=[], steps=[], runtime={"system": platform.platform(),
                   "python": platform.python_version()})
    started = time.monotonic()
    log_path = output / "preview.log"
    try:
        with tempfile.TemporaryDirectory(prefix="mihassei-musetalk-", dir=temporary) as folder:
            work = Path(folder)
            budget = PreviewBudget(work, remote=remote)
            env = {**os.environ, "CUDA_VISIBLE_DEVICES": "", "PYTORCH_ENABLE_MPS_FALLBACK": "0",
                   "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2",
                   "PIP_NO_CACHE_DIR": "1", "HF_HOME": str(work / "hf"), "TORCH_HOME": str(work / "torch"),
                   "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                   "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "XDG_CACHE_HOME": str(work / "cache"),
                   "PYTHONUNBUFFERED": "1"}

            def step(name, argv):
                receipt["current_step"] = name
                print("MuseTalk preview step:", name, flush=True)
                before = time.monotonic()
                logged_command(argv, budget, log_path, env=env)
                receipt["steps"].append({"name": name, "seconds": round(time.monotonic() - before, 3)})

            receipt["current_step"] = "download_exact_core_files"
            for item in SRC_FILES:
                record = runtime.download(item["url"], work / item["path"], budget,
                                          size=item["bytes"], sha256=item["sha256"])
                receipt["downloads"].append({**record, "path": item["path"], "license": item["license"]})
            sources = {}
            receipt["current_step"] = "download_existing_kokoro_sources"
            for repo, (revision, expected) in ARCHIVES.items():
                sources[repo], record = runtime.source_archive(repo, revision, work, budget)
                if record["sha256"] != expected:
                    raise ValueError("Pinned Kokoro/Misaki source archive changed")
                receipt["downloads"].append({**record, "revision": revision})
            interpreter = work / "cpu-env/bin/python"
            step("create_cpu_environment", [sys.executable, "-m", "venv", str(interpreter.parents[1])])
            step("pin_pip", [str(interpreter), "-m", "pip", "install", "--quiet", "pip==24.3.1",
                             "setuptools==75.6.0", "wheel==0.45.1"])
            step("install_cpu_torch", [str(interpreter), "-m", "pip", "install", "--quiet", "--index-url",
                                      "https://download.pytorch.org/whl/cpu", "torch==2.5.1+cpu", "torchvision==0.20.1+cpu"])
            constraints = work / "constraints.txt"
            constraints.write_text(runtime.TTS_CONSTRAINTS + "\ndiffusers==0.30.2\naccelerate==0.28.0\n"
                                   "opencv-python-headless==4.9.0.80\nlibrosa==0.11.0\neinops==0.8.1\n")
            step("install_core_and_existing_tts", [str(interpreter), "-m", "pip", "install", "--quiet",
                 "-c", str(constraints), str(sources["hexgrad/misaki"]) + "[ja,en]", str(sources["hexgrad/kokoro"]),
                 "soundfile==0.12.1", "diffusers==0.30.2", "accelerate==0.28.0", "opencv-python-headless==4.9.0.80",
                 "librosa==0.11.0", "einops==0.8.1"])
            step("verify_cpu_core_imports", [str(interpreter), "-c",
                 "import torch,cv2;from diffusers import AutoencoderKL,UNet2DConditionModel;"
                 "from transformers import WhisperModel;from kokoro import KModel;from misaki.ja import JAG2P;"
                 "assert not torch.cuda.is_available()"])
            receipt["current_step"] = "record_runtime_packages"
            receipt["packages"] = runtime.command([str(interpreter), "-m", "pip", "freeze"],
                                                  budget, env=env, capture=True).splitlines()
            receipt["current_step"] = "download_exact_model_weights"
            for item in WEIGHTS:
                record = runtime.download(item["url"], work / item["path"], budget,
                                          size=item["bytes"], sha256=item["sha256"])
                receipt["downloads"].append({**record, "path": item["path"], "license": item["license"]})
            receipt["current_step"] = "download_existing_kokoro_weights"
            kokoro = work / "kokoro-model"
            for name, size, sha in runtime.KOKORO_FILES:
                receipt["downloads"].append(runtime.download(runtime.HF + name, kokoro / name, budget,
                                                            size=size, sha256=sha))
            tts = work / "synthesize.py"; tts.write_text(runtime.TTS_PROGRAM)
            step("synthesize_supplied_rio_line", [str(interpreter), str(tts), str(kokoro),
                                                str(output / "narration-full.wav"), spoken_line])
            step("bound_audio_to_three_seconds", ["ffmpeg", "-nostdin", "-v", "error", "-i",
                 str(output / "narration-full.wav"), "-af", "apad,afade=t=out:st=2.9:d=0.1", "-t", "3",
                 "-ar", "16000", "-ac", "1", str(output / "narration-three-seconds.wav")])
            recipe = work / "recipe.json"; recipe.write_text(json.dumps(RECIPE))
            step("animate_rio_mouth_cpu_30_frames", [str(interpreter), str(Path(root) / "scripts/musetalk_cpu_inference.py"),
                 str(work), str(source["portrait"]), str(output / "narration-three-seconds.wav"), str(output), str(recipe)])
            receipt["motion_check"] = json.loads((output / "motion-check.json").read_text())
            step("encode_preview", ["ffmpeg", "-nostdin", "-v", "error", "-threads", "1", "-filter_threads", "1", "-framerate", "10", "-i",
                 str(work / "frames/%03d.png"), "-i", str(output / "narration-three-seconds.wav"), "-frames:v", "30",
                 "-t", "3", "-vf", "scale=640:426", "-c:v", "libx264", "-threads", "1", "-crf", "23",
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart",
                 str(output / "presenter-preview.mp4")])
            receipt["current_step"] = "verify_preview"
            probe = json.loads(runtime.command(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
                                               str(output / "presenter-preview.mp4")], budget, capture=True))
            video = [s for s in probe["streams"] if s["codec_type"] == "video"]
            audio = [s for s in probe["streams"] if s["codec_type"] == "audio"]
            if (len(video) != 1 or len(audio) != 1 or video[0]["codec_name"] != "h264"
                    or audio[0]["codec_name"] != "aac" or int(video[0].get("nb_frames", 0)) != 30
                    or not 2.9 <= float(probe["format"]["duration"]) <= 3.2):
                raise RuntimeError("Preview video/audio validation failed")
            receipt.update(status="generated_unreviewed", preview_sha256=runtime.digest(output / "presenter-preview.mp4"),
                           ffprobe=probe, notice="Mouth animation only. Identity, lip-sync and voice require actual review; never auto-publish.")
    except BaseException as error:
        receipt.update(status="failed", failure_type=type(error).__name__, failure_message=str(error)[:500])
        raise
    finally:
        if log_path.exists() and log_path.stat().st_size > 512 * 1024:
            with log_path.open("rb") as log:
                log.seek(-512 * 1024, 2)
                tail = log.read()
            log_path.write_bytes(tail)
            receipt["log_truncated_to_last_bytes"] = len(tail)
        if runtime.tree_bytes(output) > runtime.MAX_ARTIFACT - 128 * 1024:
            # Remove only this pilot's media, retaining its source binding/log.
            for name in ("presenter-preview.mp4", "narration-full.wav", "first-frame.jpg"):
                (output / name).unlink(missing_ok=True)
            receipt.update(status="failed", failure_type="ArtifactSizeLimit")
        receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
        (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not args.run:
        print(json.dumps(plan(), ensure_ascii=False, indent=2)); return 0
    if args.output is None:
        parser.error("--run requires --output")
    execute(args.output); return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print("MuseTalk preview stopped:", type(error).__name__, str(error), file=sys.stderr)
        raise SystemExit(1)
