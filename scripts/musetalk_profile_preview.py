"""One explicit, artifact-only MuseTalk core experiment on public Linux CPU."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import wave

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
# A visual estimate from nose bridge/face edges/chin, NOT measured DWPose
# landmarks. Compare only this crop against run 43691031221. Keep the old
# rasterized ellipse at image center (714,318), radii (70,51), blur 8, and
# absolute right fade boundary 792 / width 15.6; only normalize to the new box.
RECIPE = {"source_dimensions": [1200, 800], "face_box": [540, 88, 820, 428],
          "mask": {"shape": "ellipse", "center": [(714 - 540) / 280, (318 - 88) / 340],
                   "radii": [70 / 280, 51 / 340], "blur_sigma_px": 8,
                   "right_edge": (792 - 540) / 280, "right_feather": 15.6 / 280},
          "fps": 10, "frames": 30, "seconds": 3, "seed": 0, "dtype": "float32", "batch_size": 1}
BASELINE_RUN = 43691031221
BASELINE_HEAD = "261f52a6fb1c68da408b35e61259a25e2a43038b"
AUDIO_DIR = "maintenance/musetalk-pilot/run-43691031221/"
BASELINE_FILES = {
    "narration-three-seconds.wav": (96078, "5920ac75800aeafd4d745656569a59ccfca6f05b1cba9c9cbba93ddd7e728dfa"),
    "receipt.json": (24278, "e9cfd71918a4df2355369350e5693bbda5887a87d75ec17d4919eeb2f01d564d"),
    "presenter-preview.mp4": (70535, "b180230cddb1dd06ff79d9a105f2664f9677a79c649484d8456fcbe8233e78b7"),
}
# No TTS/G2P/voice package, archive, or weight is installed for this comparison.
CORE_PACKAGES = ("numpy==1.26.4", "transformers==4.46.3", "huggingface-hub==0.26.5",
                 "soundfile==0.12.1", "diffusers==0.30.2", "accelerate==0.28.0",
                 "opencv-python-headless==4.9.0.80", "librosa==0.11.0", "einops==0.8.1")


def fixed_audio(*, root, source):
    """Read the saved unreviewed waveform; no synthetic speech is recreated."""
    root = Path(root)
    if root.is_symlink():
        raise ValueError("Unsafe fixed audio root")
    root = root.resolve(strict=True)
    records, data = {}, {}
    for name in (*BASELINE_FILES, "artifact-source.json", "rejected-review.json"):
        path = root / AUDIO_DIR / name
        if (any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file()
                or not 1 <= path.stat().st_size <= 256 * 1024):
            raise ValueError("Missing or unsafe fixed pilot audio/evidence")
        data[name] = path.read_bytes()
        records[name] = {"path": AUDIO_DIR + name, "bytes": len(data[name]),
                         "sha256": hashlib.sha256(data[name]).hexdigest()}
        if name in BASELINE_FILES and (len(data[name]), records[name]["sha256"]) != BASELINE_FILES[name]:
            raise ValueError("Fixed pilot audio or original evidence bytes changed")
    receipt = json.loads(data["receipt.json"])
    artifact = json.loads(data["artifact-source.json"])
    review = json.loads(data["rejected-review.json"])
    if (artifact.get("schema_version") != 1 or artifact.get("repository") != "RollNuts/mihassei-news"
            or artifact.get("run_id") != BASELINE_RUN or artifact.get("head_sha") != BASELINE_HEAD
            or type(artifact.get("artifact_id")) is not int or artifact["artifact_id"] <= 0
            or artifact.get("artifact_name") != "news-caster-preview-" + str(BASELINE_RUN)
            or artifact.get("run_status") != "completed" or artifact.get("run_conclusion") != "success"
            or any(artifact.get("files", {}).get(name) != {"bytes": count, "sha256": sha}
                   for name, (count, sha) in BASELINE_FILES.items())
            or receipt.get("status") != "generated_unreviewed" or receipt.get("publish") is not False
            or receipt.get("publication") != "preview_only" or receipt.get("backend") != "musetalk-core"
            or receipt.get("repository_revision") != BASELINE_HEAD or receipt.get("spoken_line") != SPOKEN_LINE
            or any(receipt.get(key) != value for key, value in source["context"].items())
            or review.get("decision") != "rejected" or review.get("run_id") != BASELINE_RUN
            or review.get("source_report", {}).get("audio_listened") is not False
            or review.get("recording_agent_audio_listening") is not False
            or review.get("preview_sha256") != BASELINE_FILES["presenter-preview.mp4"][1]
            or review.get("narration_sha256") != BASELINE_FILES["narration-three-seconds.wav"][1]
            or review.get("receipt_sha256") != BASELINE_FILES["receipt.json"][1]):
        raise ValueError("Fixed audio is not bound to the original unreviewed pilot/run/source")
    with wave.open(io.BytesIO(data["narration-three-seconds.wav"]), "rb") as audio:
        if ((audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes(), audio.getcomptype())
                != (1, 2, 16000, 48000, "NONE")):
            raise ValueError("Fixed pilot audio must be mono 16k PCM16 for exactly three seconds")
    return {"kind": "saved-pilot-audio", "run_id": BASELINE_RUN, "status": "generated_unreviewed",
            "audio_review": "not_listened", "source_video_decision": "rejected", "files": records,
            "format": {"channels": 1, "sample_rate": 16000, "sample_width": 2, "frames": 48000},
            "origin": {key: receipt[key] for key in ("voice", "kokoro_revision", "kokoro_license",
                                                     "kokoro_code_license", "misaki_code_license")}}


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
            "portrait": source["portrait_record"], "audio_source": fixed_audio(root=root, source=source),
            "comparison": {"baseline_run_id": BASELINE_RUN, "original_face_box": [480, 80, 870, 470],
                           "change": "manual face bbox only; audio bytes and physical mouth mask retained",
                           "bbox_method": "visual estimate from nose bridge, face edges and chin; not DWPose landmarks",
                           "preserved_mask_image_coordinates": {"center": [714, 318], "radii": [70, 51],
                               "blur_sigma_px": 8, "right_edge": 792, "right_feather": 15.6},
                           "quality": "unreviewed hypothesis; improvement is not established"},
            "core_packages": list(CORE_PACKAGES),
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
        audio = receipt["audio_source"]["files"]["narration-three-seconds.wav"]
        saved_audio = Path(root) / audio["path"]
        if runtime.digest(saved_audio) != audio["sha256"]:
            raise ValueError("Saved pilot waveform changed before copying")
        shutil.copyfile(saved_audio, output / "narration-three-seconds.wav")
        if runtime.digest(output / "narration-three-seconds.wav") != audio["sha256"]:
            raise ValueError("Copied pilot waveform bytes changed")
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
            interpreter = work / "cpu-env/bin/python"
            step("create_cpu_environment", [sys.executable, "-m", "venv", str(interpreter.parents[1])])
            step("pin_pip", [str(interpreter), "-m", "pip", "install", "--quiet", "pip==24.3.1",
                             "setuptools==75.6.0", "wheel==0.45.1"])
            step("install_cpu_torch", [str(interpreter), "-m", "pip", "install", "--quiet", "--index-url",
                                      "https://download.pytorch.org/whl/cpu", "torch==2.5.1+cpu", "torchvision==0.20.1+cpu"])
            constraints = work / "constraints.txt"
            constraints.write_text("\n".join(CORE_PACKAGES) + "\n")
            step("install_core_only", [str(interpreter), "-m", "pip", "install", "--quiet",
                 "-c", str(constraints), *CORE_PACKAGES])
            step("verify_cpu_core_imports", [str(interpreter), "-c",
                 "import torch,cv2;from diffusers import AutoencoderKL,UNet2DConditionModel;"
                 "from transformers import WhisperModel;import librosa,soundfile;"
                 "assert not torch.cuda.is_available()"])
            receipt["current_step"] = "record_runtime_packages"
            receipt["packages"] = runtime.command([str(interpreter), "-m", "pip", "freeze"],
                                                  budget, env=env, capture=True).splitlines()
            receipt["current_step"] = "download_exact_model_weights"
            for item in WEIGHTS:
                record = runtime.download(item["url"], work / item["path"], budget,
                                          size=item["bytes"], sha256=item["sha256"])
                receipt["downloads"].append({**record, "path": item["path"], "license": item["license"]})
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
            for name in ("presenter-preview.mp4", "first-frame.jpg"):
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
