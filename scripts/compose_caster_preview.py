#!/usr/bin/env python3
"""Compose an authenticated existing caster artifact; no model setup or inference.

Manual public GitHub CPU runner only. The first three seconds show the original
talking presenter once beside the reviewed story photo, followed by the full
photo and remaining original narration. Outputs are preview artifacts only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import wave
from zipfile import ZipFile

if __package__:
    from .news_caster_preview import (ROOT, REPOSITORY, ARTICLE_SLUG, TEXT, MAX_ARTIFACT, MIN_FREE,
                                    Budget, RemoteStopGate, command, digest, runner_gate,
                                    stop_child, validate_portrait)
    from .generated_photos import article_fingerprint, generated_scene_entry, usable_generated_scene
    from .visual_review import reviewed_generated_scene
else:
    from news_caster_preview import (ROOT, REPOSITORY, ARTICLE_SLUG, TEXT, MAX_ARTIFACT, MIN_FREE,
                                   Budget, RemoteStopGate, command, digest, runner_gate,
                                   stop_child, validate_portrait)
    from generated_photos import article_fingerprint, generated_scene_entry, usable_generated_scene
    from visual_review import reviewed_generated_scene

ARTICLE_SHA = "cc961b9736715a506d69b4af9b8d04166c0e3b47a7cd71fb439d0308975a90ac"
PHOTO_SHA = "35a6e7c589472b88999a8f52b6ed6325da478b0d2410d3bc10aef5ef7c8bd515"
PORTRAIT_SHA = "771380a1e1eb9037c3f1c00b3bf4c47303fd2c42e2a6ced4a628d4783e242a66"
WORKFLOW = ".github/workflows/auto-motion-clips.yml"
FILES = {"presenter-preview.mp4", "narration-full.wav", "narration-three-seconds.wav", "receipt.json"}
FONT = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
MOVIE = "news-preview.mp4"
# The retained pilot is the sole repository-file reuse source. These are the
# original bytes and original receipt, never a repaired/relabelled provenance.
COMPOSITION_RUN = 42930964897
COMPOSITION_SHA = "31141a1037699f24552ffe16f60d17ac835e304fa13c620a70d5b1a793406b93"
COMPOSITION_RECEIPT_SHA = "90daefade03c8f89bcb1d4ad25714dce38b50d24b1f800a289253f82c5864444"
SOURCE_MANIFEST = re.compile(r"([0-9a-f]{40}):(drafts/caster-review/[a-z0-9]+(?:-[a-z0-9]+)*/manifest\.json):([0-9a-f]{64})")


def validate_source_run(data, run_id):
    if (data.get("id") != run_id or data.get("event") != "workflow_dispatch"
            or data.get("status") != "completed" or data.get("conclusion") != "success"
            or data.get("path") != WORKFLOW
            or data.get("repository", {}).get("full_name") != REPOSITORY
            or data.get("repository", {}).get("private") is not False
            or data.get("head_repository", {}).get("full_name") != REPOSITORY
            or not re.fullmatch(r"[0-9a-f]{40}", str(data.get("head_sha", "")))):
        raise ValueError("Source must be a successful manual run of the same public workflow/repository")


def select_artifact(data, run_id):
    if data.get("total_count", 0) > 100:
        raise ValueError("Unexpected source artifact list size")
    matches = [a for a in data.get("artifacts", []) if a.get("name") == f"news-caster-preview-{run_id}"]
    if len(matches) != 1:
        raise ValueError("Exactly one named source artifact is required")
    artifact = matches[0]
    if (type(artifact.get("id")) is not int or artifact["id"] <= 0
            or artifact.get("expired") is not False
            or artifact.get("workflow_run", {}).get("id") != run_id
            or type(artifact.get("size_in_bytes")) is not int
            or not 1 <= artifact["size_in_bytes"] <= MAX_ARTIFACT
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(artifact.get("digest", "")))):
        raise ValueError("Source artifact is expired, oversized, or has no authenticated digest")
    return artifact


def unpack_artifact(archive: Path, destination: Path, artifact):
    if archive.stat().st_size != artifact["size_in_bytes"] or digest(archive) != artifact["digest"][7:]:
        raise ValueError("Downloaded artifact differs from GitHub's artifact digest/size")
    with ZipFile(archive) as source:
        entries = source.infolist()
        if (len(entries) != 4 or {a.filename for a in entries} != FILES
                or sum(a.file_size for a in entries) > MAX_ARTIFACT):
            raise ValueError("Source artifact must contain exactly four bounded preview files")
        for entry in entries:
            if (entry.flag_bits & 1 or stat.S_ISLNK(entry.external_attr >> 16)
                    or not 1 <= entry.file_size <= MAX_ARTIFACT):
                raise ValueError("Unsafe source artifact entry")
            data = source.read(entry)
            if len(data) != entry.file_size:
                raise ValueError("Source artifact member size mismatch")
            with (destination / entry.filename).open("xb") as output:
                output.write(data)


def pcm(path, rate):
    with wave.open(str(path), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, rate, "NONE"):
            raise ValueError("Expected mono 16-bit PCM narration at its recorded sample rate")
        duration = audio.getnframes() / rate
        if not 0.3 <= duration <= 12:
            raise ValueError("Narration must fit the bounded bulletin without changing speech speed")
        samples = audio.readframes(audio.getnframes())
    if not any(samples):
        raise ValueError("Silent narration is not a usable source")
    return duration, samples


def validate_bundle(folder: Path, portrait_receipt):
    receipt = json.loads((folder / "receipt.json").read_text(encoding="utf-8"))
    if (receipt.get("version") != 1 or receipt.get("publication") != "preview_only"
            or receipt.get("status") != "generated_unreviewed" or receipt.get("seconds") != 3
            or receipt.get("article_slug") != ARTICLE_SLUG or receipt.get("article_sha256") != ARTICLE_SHA
            or receipt.get("full_narration_text") != TEXT
            or receipt.get("preview_sha256") != digest(folder / "presenter-preview.mp4")):
        raise ValueError("Source receipt does not bind the current article, speech, and raw preview")
    for field in ("portrait_sha256", "source_png_sha256", "generation_id", "fictional_person", "intended_age"):
        if receipt.get("portrait", {}).get(field) != portrait_receipt.get(field):
            raise ValueError("Source presenter no longer matches the current portrait receipt")
    duration, _ = pcm(folder / "narration-full.wav", 24000)
    short_duration, _ = pcm(folder / "narration-three-seconds.wav", 16000)
    if abs(short_duration - 3) > 1 / 16000:
        raise ValueError("Source driving audio is not three seconds")
    return receipt, max(8.0, duration)


def current_assets(root=ROOT):
    articles = json.loads((root / "content/articles.json").read_text(encoding="utf-8"))
    article = next((a for a in articles if a["slug"] == ARTICLE_SLUG), None)
    if article is None or article_fingerprint(article) != ARTICLE_SHA:
        raise ValueError("Source article changed; old prototype cannot be reused")
    photo = root / "assets/generated" / (ARTICLE_SLUG + ".jpg")
    generation = generated_scene_entry(article, root=root)
    if (not generation or generation.get("sha256") != PHOTO_SHA or digest(photo) != PHOTO_SHA
            or not usable_generated_scene(article, root=root) or not reviewed_generated_scene(article, root=root)):
        raise ValueError("Current story photo is not the matching approved photograph")
    portrait = root / "assets/casters/newsreader-preview.jpg"
    portrait_receipt = validate_portrait(portrait, portrait.with_suffix(".json"))
    if portrait_receipt["portrait_sha256"] != PORTRAIT_SHA:
        raise ValueError("Current presenter differs from this preview")
    return article, photo, portrait_receipt


def check_fresh_main(budget):
    command(["git", "fetch", "--quiet", "origin", "main"], budget, cwd=ROOT)
    articles = json.loads(command(["git", "show", "FETCH_HEAD:content/articles.json"], budget, cwd=ROOT, capture=True))
    article = next((a for a in articles if a["slug"] == ARTICLE_SLUG), None)
    manifest = json.loads(command(["git", "show", "FETCH_HEAD:assets/generated/manifest.json"], budget, cwd=ROOT, capture=True))
    reviews = json.loads(command(["git", "show", "FETCH_HEAD:content/visual_reviews.json"], budget, cwd=ROOT, capture=True))
    review = reviews.get("reviews", {}).get(ARTICLE_SLUG, {})
    generation = manifest.get("images", {}).get(ARTICLE_SLUG, {})
    held = manifest.get("rejected", {}).get(ARTICLE_SLUG, {})
    if (not article or article_fingerprint(article) != ARTICLE_SHA
            or generation.get("sha256") != PHOTO_SHA or generation.get("article_sha256") != ARTICLE_SHA
            or held.get("article_sha256") == ARTICLE_SHA
            or review.get("article_sha256") != ARTICLE_SHA or review.get("image_sha256") != PHOTO_SHA
            or review.get("decision") != "accepted"):
        raise ValueError("Latest main invalidated the source article or reviewed photo")
    return command(["git", "rev-parse", "FETCH_HEAD"], budget, cwd=ROOT, capture=True).strip()


def binary_command(argv, target: Path, budget, max_bytes=MAX_ARTIFACT):
    """GitHub CLI handles authenticated redirects; never forward a token ourselves."""
    budget.check()
    with target.open("xb") as output:
        process = subprocess.Popen(argv, stdout=output, start_new_session=True)
        try:
            while process.poll() is None:
                budget.check()
                if target.stat().st_size > max_bytes:
                    raise ValueError("Downloaded source artifact is oversized")
                time.sleep(.25)
            if process.returncode:
                raise RuntimeError("Authenticated artifact download failed")
        except BaseException:
            stop_child(process)
            raise
    if target.stat().st_size > max_bytes:
        raise ValueError("Downloaded source artifact is oversized")


def repository_file(ref, path, sha256, destination, budget, max_bytes):
    endpoint = f"repos/{REPOSITORY}/contents/{path}?ref={ref}"
    metadata = json.loads(command(["gh", "api", endpoint, "--jq", "{type,path,size}"], budget, capture=True))
    if (metadata.get("type") != "file" or metadata.get("path") != path
            or type(metadata.get("size")) is not int or not 1 <= metadata["size"] <= max_bytes):
        raise ValueError("Pinned source must be a bounded regular file at its exact repository path")
    binary_command(["gh", "api", "-H", "Accept: application/vnd.github.raw+json", endpoint],
                   destination, budget, max_bytes=max_bytes)
    if destination.stat().st_size != metadata["size"] or digest(destination) != sha256:
        raise ValueError("Pinned repository source differs from its exact size or SHA-256")


def retained_composition(source, work, budget):
    match = SOURCE_MANIFEST.fullmatch(source)
    if not match:
        raise ValueError("Source manifest requires a pinned 40-hex commit, strict review path and SHA-256")
    ref, path, manifest_sha = match.groups()
    manifest_path = work / "source-manifest.json"
    repository_file(ref, path, manifest_sha, manifest_path, budget, 4096)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict) or type(manifest.get("version")) is not int
            or type(manifest.get("composition_run_id")) is not int or manifest != {"version": 1, "composition_run_id": COMPOSITION_RUN,
                    "movie_sha256": COMPOSITION_SHA, "receipt_sha256": COMPOSITION_RECEIPT_SHA}):
        raise ValueError("Manifest must identify the unchanged retained pilot and its original receipt")
    folder = work / "retained"; folder.mkdir()
    parent = path.rsplit("/", 1)[0]
    repository_file(ref, parent + "/" + MOVIE, COMPOSITION_SHA, folder / MOVIE, budget, MAX_ARTIFACT)
    repository_file(ref, parent + "/receipt.json", COMPOSITION_RECEIPT_SHA, folder / "receipt.json", budget, 64 * 1024)
    original = json.loads((folder / "receipt.json").read_text(encoding="utf-8"))
    if (original.get("version") != 1 or original.get("publication") != "preview_only"
            or original.get("status") != "composed_unreviewed" or original.get("source_run_id") != 42928393181
            or original.get("article_slug") != ARTICLE_SLUG or original.get("article_sha256") != ARTICLE_SHA
            or original.get("photo_sha256") != PHOTO_SHA or original.get("portrait_sha256") != PORTRAIT_SHA
            or original.get("narration") != TEXT or original.get("duration_seconds") != 8
            or original.get("sha256") != COMPOSITION_SHA
            or original.get("models_downloaded") is not False or original.get("models_executed") is not False):
        raise ValueError("Retained receipt does not bind the unchanged pilot, article, photo, presenter and narration")
    for run_id in (COMPOSITION_RUN, original["source_run_id"]):
        run = json.loads(command(["gh", "api", f"repos/{REPOSITORY}/actions/runs/{run_id}"], budget, capture=True))
        validate_source_run(run, run_id)
        if run_id == original["source_run_id"] and run["head_sha"] != original.get("source_head_sha"):
            raise ValueError("Retained receipt differs from the original producer run")
    return folder / MOVIE, original, {"source_manifest": source, "source_composition_run_id": COMPOSITION_RUN,
                                    "source_composition_sha256": COMPOSITION_SHA,
                                    "source_original_receipt_sha256": COMPOSITION_RECEIPT_SHA}


def validate_retained_probe(probe):
    streams = probe.get("streams", [])
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    duration = float(probe.get("format", {}).get("duration", 0))
    if (len(streams) != 2 or len(video) != 1 or len(audio) != 1
            or video[0].get("codec_name") != "h264" or audio[0].get("codec_name") != "aac"
            or (video[0].get("width"), video[0].get("height")) != (1280, 720)
            or video[0].get("avg_frame_rate") != "25/1" or video[0].get("nb_frames") != "200"
            or not math.isfinite(duration) or abs(duration - 8) > .001
            or audio[0].get("channels") != 1 or audio[0].get("sample_rate") != "24000"):
        raise ValueError("Retained pilot must be exactly eight seconds of 25fps H.264 and original mono AAC")
    for stream in (video[0], audio[0]):
        start = float(stream.get("start_time", "nan"))
        seconds = float(stream.get("duration", "nan"))
        if not all(math.isfinite(value) for value in (start, seconds)) or abs(start) > 1 / 25 or abs(seconds - 8) > 1 / 25:
            raise ValueError("Each retained stream must start at zero and last eight seconds within one frame")


def packet_timing(movie, probe, budget):
    packets = json.loads(command(["ffprobe", "-v", "error", "-show_packets", "-show_entries",
                                  "packet=stream_index,pts,dts:packet_side_data=side_data_type,skip_samples,discard_padding",
                                  "-of", "json", str(movie)], budget, capture=True)).get("packets", [])
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    audio = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    video_base, audio_base = Fraction(video["time_base"]), Fraction(audio["time_base"])
    if video_base <= 0 or audio_base <= 0 or not 200 <= len(packets) <= 500:
        raise ValueError("Unexpected retained packet count or time base")
    video_packets = [p for p in packets if p.get("stream_index") == video["index"]]
    audio_packets = [p for p in packets if p.get("stream_index") == audio["index"]]
    if (len(video_packets) != 200 or not audio_packets
            or any(type(p.get("pts")) is not int for p in video_packets)
            or any(type(audio_packets[0].get(field)) is not int for field in ("pts", "dts"))):
        raise ValueError("Retained presentation times must be explicit integer packet timestamps")
    video_pts = tuple(sorted(p["pts"] * video_base for p in video_packets))
    if video_pts != tuple(Fraction(frame, 25) for frame in range(200)):
        raise ValueError("Retained video presentation sequence changed, including the three-second cut")
    skip = [s for s in audio_packets[0].get("side_data_list", []) if s.get("side_data_type") == "Skip Samples"]
    if len(skip) != 1 or any(type(skip[0].get(key)) is not int or skip[0][key] < 0 for key in ("skip_samples", "discard_padding")):
        raise ValueError("Original AAC priming/skip sample evidence is required")
    return {"video_pts": video_pts, "audio_pts": audio_packets[0]["pts"] * audio_base,
            "audio_dts": audio_packets[0]["dts"] * audio_base,
            "skip_samples": skip[0]["skip_samples"], "discard_padding": skip[0]["discard_padding"]}


def compare_packet_timing(source, edited):
    if (source["video_pts"] != edited["video_pts"]
            or any(abs(source[key] - edited[key]) > Fraction(1, 24000) for key in ("audio_pts", "audio_dts"))
            or any(source[key] != edited[key] for key in ("skip_samples", "discard_padding"))):
        raise ValueError("Re-edit changed video presentation timing or AAC priming/synchronization")
    return {"video_packets": 200, "video_presentation_sequence_unchanged": True,
            "cut_frame_times_seconds": [float(edited["video_pts"][74]), float(edited["video_pts"][75])],
            "audio_first_pts_seconds": str(edited["audio_pts"]), "audio_first_dts_seconds": str(edited["audio_dts"]),
            "audio_skip_samples": edited["skip_samples"], "audio_discard_padding": edited["discard_padding"],
            "audio_first_packet_timing_matches_within_one_sample": True}


def readable_labels(work):
    labels = {"fiction": "創作ニュース", "headline": "警備ロボ「侵入者32人」",
              "detail": "正体は、前日の来場者の光の人影"}
    for name, value in labels.items():
        (work / (name + ".txt")).write_text(value, encoding="utf-8")
    graph = ("drawbox=x=1000:y=0:w=280:h=64:color=0x183b40:t=fill,"
             "drawbox=x=0:y=600:w=iw:h=120:color=white:t=fill")
    # The fixed Japanese lines fit inside 1216px at these sizes; the disclosure
    # fits its 248px header region. No part of the presenter/photo is replaced.
    for name, x, y, size, color in (("fiction", 1008, 8, 40, "white"),
                                   ("headline", 32, 604, 56, "0x17393d"),
                                   ("detail", 34, 666, 44, "0x31545a")):
        graph += f",drawtext=fontfile='{FONT}':textfile='{work / (name + '.txt')}':expansion=none:x={x}:y={y}:fontsize={size}:fontcolor={color}"
    return graph


def reedit_command(source, output, work):
    return ["ffmpeg", "-nostdin", "-v", "error", "-threads", "1", "-filter_threads", "1",
            "-i", str(source), "-vf", readable_labels(work), "-map", "0:v:0", "-map", "0:a:0",
            "-c:v", "libx264", "-threads", "1", "-preset", "veryfast", "-crf", "25",
            "-maxrate", "2000k", "-bufsize", "4000k", "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-movflags", "+faststart", str(output)]


def aac_packet_hash(movie, budget):
    value = command(["ffmpeg", "-nostdin", "-v", "error", "-i", str(movie), "-map", "0:a:0",
                     "-c:a", "copy", "-f", "hash", "-hash", "sha256", "-"], budget, capture=True).strip()
    if not re.fullmatch(r"SHA256=[0-9a-f]{64}", value):
        raise ValueError("Could not verify the original encoded AAC packets")
    return value[7:]


def filter_graph(work, duration):
    # UTF-8 text files keep title punctuation outside FFmpeg's filter syntax.
    labels = {"brand": "未発生新聞", "fiction": "創作ニュース",
              "headline": "警備ロボ「侵入者32人」", "detail": "正体は、前日の来場者の光の人影"}
    for name, value in labels.items():
        (work / (name + ".txt")).write_text(value, encoding="utf-8")
    graph = (
        f"color=c=0xe7edee:s=1280x720:r=25:d={duration}[base];"
        "[0:v]setpts=PTS-STARTPTS,fps=25,scale=440:440,setsar=1[anchor];"
        "[1:v]split=2[smallin][fullin];"
        "[smallin]scale=760:470:force_original_aspect_ratio=decrease,pad=760:470:(ow-iw)/2:(oh-ih)/2:color=0xdce5e7[small];"
        "[fullin]scale=1216:500:force_original_aspect_ratio=decrease,pad=1216:500:(ow-iw)/2:(oh-ih)/2:color=0xdce5e7[full];"
        "[base][small]overlay=488:88:enable='lt(t,3)'[s1];"
        "[s1][anchor]overlay=32:104:enable='lt(t,3)':eof_action=pass:repeatlast=0[s2];"
        "[s2][full]overlay=32:80:enable='gte(t,3)'[s3];"
        "[s3]drawbox=x=0:y=0:w=iw:h=64:color=0x183b40:t=fill,"
        "drawbox=x=0:y=600:w=iw:h=120:color=white:t=fill,"
        "drawbox=x=32:y=592:w=1216:h=4:color=0x2e7678:t=fill"
    )
    for name, x, y, size, color in (("brand",32,15,28,"white"),("fiction",1096,18,24,"white"),
                                   ("headline",32,612,38,"0x17393d"),("detail",34,664,26,"0x31545a")):
        graph += f",drawtext=fontfile='{FONT}':textfile='{work / (name+'.txt')}':expansion=none:x={x}:y={y}:fontsize={size}:fontcolor={color}"
    return graph + "[video]"


def execute(source_run: int | None, output: Path, source_manifest=None):
    temporary = runner_gate()
    if source_manifest is not None:
        if source_run is not None or not SOURCE_MANIFEST.fullmatch(source_manifest):
            raise ValueError("Supply only one strict pinned source manifest")
    elif type(source_run) is not int or source_run <= 0:
        raise ValueError("A positive source run ID is required")
    remote = RemoteStopGate(); remote.check()
    if shutil.disk_usage(temporary).free < MIN_FREE:
        raise RuntimeError("Less than 12 GiB free; composition stopped")
    article, photo, portrait = current_assets()
    output = output.resolve()
    if not output.is_relative_to(temporary) or output.exists():
        raise ValueError("Output must be a new directory inside RUNNER_TEMP")
    if not FONT.is_file() or not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("Existing FFmpeg, ffprobe and Noto CJK font are required")
    output.mkdir()
    receipt = {"version":1,"publication":"preview_only","status":"started","source_run_id":source_run,
               "article_sha256":ARTICLE_SHA,"article_slug":ARTICLE_SLUG,"photo_sha256":PHOTO_SHA,
               "portrait_sha256":PORTRAIT_SHA,"models_downloaded":False,"models_executed":False}
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="mihassei-caster-compose-",dir=temporary) as directory:
            work = Path(directory); budget = Budget(work, timeout=180, remote=remote)
            receipt["fresh_main_sha_before"] = check_fresh_main(budget)
            if source_manifest is not None:
                source_movie, original, provenance = retained_composition(source_manifest, work, budget)
                receipt.update({key: value for key, value in original.items() if key.startswith("source_")})
                receipt.update(provenance, duration_seconds=8.0, audio_stream_copied=True,
                               edit="Readable lower third and fiction disclosure only; original framing and three-second cut retained")
                duration = 8.0
                raw_probe = json.loads(command(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source_movie)], budget, capture=True))
                validate_retained_probe(raw_probe)
                receipt["source_ffprobe"] = raw_probe
                source_timing = packet_timing(source_movie, raw_probe, budget)
                receipt["source_aac_packet_sha256"] = aac_packet_hash(source_movie, budget)
                command(reedit_command(source_movie, output / MOVIE, work), budget)
            else:
                run = json.loads(command(["gh","api",f"repos/{REPOSITORY}/actions/runs/{source_run}"],budget,capture=True))
                validate_source_run(run,source_run)
                artifact_list = json.loads(command(["gh","api",f"repos/{REPOSITORY}/actions/runs/{source_run}/artifacts?per_page=100"],budget,capture=True))
                artifact = select_artifact(artifact_list,source_run)
                archive = work / "source.zip"
                binary_command(["gh","api",f"repos/{REPOSITORY}/actions/artifacts/{artifact['id']}/zip"],archive,budget)
                bundle = work / "source";bundle.mkdir();unpack_artifact(archive,bundle,artifact)
                original, duration = validate_bundle(bundle,portrait)
                receipt.update(source_artifact_id=artifact["id"],source_artifact_digest=artifact["digest"],
                               source_head_sha=run["head_sha"],source_preview_sha256=original["preview_sha256"],
                               source_narration_full_sha256=digest(bundle/"narration-full.wav"),
                               source_narration_three_seconds_sha256=digest(bundle/"narration-three-seconds.wav"),
                               source_audio_hash_evidence="Observed within authenticated immutable source artifact; source producer receipt supplied no per-audio hashes.",
                               duration_seconds=duration)
                raw_probe=json.loads(command(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(bundle/"presenter-preview.mp4")],budget,capture=True))
                streams=raw_probe.get("streams",[])
                if (len([s for s in streams if s.get("codec_type")=="video" and s.get("codec_name")=="h264"])!=1
                        or len([s for s in streams if s.get("codec_type")=="audio" and s.get("codec_name")=="aac"])!=1
                        or not 2.9<=float(raw_probe["format"]["duration"])<=3.2):
                    raise ValueError("Source preview streams/duration do not match its receipt")
                derived = work / "derived-driving.wav"
                command(["ffmpeg","-nostdin","-v","error","-i",str(bundle/"narration-full.wav"),"-af","apad,afade=t=out:st=2.9:d=0.1","-t","3","-ar","16000","-ac","1",str(derived)],budget)
                if pcm(derived,16000)[1] != pcm(bundle/"narration-three-seconds.wav",16000)[1]:
                    raise ValueError("Full narration does not reproduce the source driving audio")
                command(["ffmpeg","-nostdin","-v","error","-threads","1","-filter_complex_threads","1",
                         "-i",str(bundle/"presenter-preview.mp4"),"-loop","1","-framerate","25","-i",str(photo),
                         "-i",str(bundle/"narration-full.wav"),"-filter_complex",filter_graph(work,duration),
                         "-map","[video]","-map","2:a:0","-af",f"apad,afade=t=out:st={duration-.1}:d=0.1",
                         "-t",str(duration),"-r","25","-c:v","libx264","-threads","1","-preset","veryfast","-crf","25",
                         "-maxrate","2000k","-bufsize","4000k","-pix_fmt","yuv420p","-c:a","aac","-b:a","80k",
                         "-movflags","+faststart",str(output/MOVIE)],budget)
            probe=json.loads(command(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(output/MOVIE)],budget,capture=True))
            video=[s for s in probe.get("streams",[]) if s.get("codec_type")=="video"]
            audio=[s for s in probe.get("streams",[]) if s.get("codec_type")=="audio"]
            if (len(video)!=1 or len(audio)!=1 or video[0].get("codec_name")!="h264" or audio[0].get("codec_name")!="aac"
                    or (video[0].get("width"),video[0].get("height"))!=(1280,720)
                    or abs(float(probe["format"]["duration"])-duration)>.2
                    or not 1024<=(output/MOVIE).stat().st_size<=MAX_ARTIFACT-128*1024):
                raise ValueError("Composed bulletin failed format, duration or size validation")
            if source_manifest is not None:
                validate_retained_probe(probe)
                receipt["timing_verification"] = compare_packet_timing(source_timing, packet_timing(output / MOVIE, probe, budget))
                receipt["aac_packet_sha256"] = aac_packet_hash(output / MOVIE, budget)
                if receipt["aac_packet_sha256"] != receipt["source_aac_packet_sha256"]:
                    raise ValueError("Re-edit changed the original encoded narration")
            receipt["fresh_main_sha_after"] = check_fresh_main(budget)
            receipt.update(status="composed_unreviewed",sha256=digest(output/MOVIE),ffprobe=probe,
                           narration=TEXT,notice="Original presenter appears once for three seconds; scene remains a still photograph. Audio/visual review required before publication.")
    except BaseException as error:
        receipt.update(status="failed",failure_type=type(error).__name__)
        raise
    finally:
        movie=output/MOVIE
        if movie.is_file() and movie.stat().st_size>MAX_ARTIFACT-128*1024:
            movie.unlink();receipt.update(status="failed",failure_type="ArtifactSizeLimit")
        receipt.update(elapsed_seconds=round(time.monotonic()-started,3),checked_at_utc=datetime.now(timezone.utc).isoformat())
        (output/"receipt.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",action="store_true")
    sources=parser.add_mutually_exclusive_group()
    sources.add_argument("--source-run",type=int)
    sources.add_argument("--source-manifest")
    parser.add_argument("--output",type=Path)
    args=parser.parse_args(argv)
    if not args.run:
        print(json.dumps({"publication":"preview_only","models_downloaded":False,"models_executed":False,
                          "source_run":args.source_run,"source_manifest":args.source_manifest,"seconds":"8–12","presenter_seconds":3,"downloads":"none in plan mode"},ensure_ascii=False))
        return 0
    if (args.source_run is None and args.source_manifest is None) or args.output is None:
        parser.error("--run requires a source and --output")
    execute(args.source_run,args.output,args.source_manifest)
    return 0


if __name__=="__main__":
    try:raise SystemExit(main())
    except (ValueError,OSError,RuntimeError,subprocess.SubprocessError,wave.Error) as error:
        print("Caster composition stopped:",type(error).__name__,str(error),file=sys.stderr)
        raise SystemExit(1)
