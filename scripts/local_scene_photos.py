#!/usr/bin/env python3
"""Generate deadpan, photorealistic fictional-news scenes on a LOCAL ComfyUI.

This tool never calls a hosted image API. The only allowed network destination
is the local machine's loopback interface. Python stdlib handles ComfyUI;
Pillow (installed locally) validates/resizes the actual images.
Run --dry-run first; generated JPEGs are optional for normal site publishing.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    from .generated_photos import (
        RELATIVE_DIR, MANIFEST_NAME, PROMPT_VERSION, ROOT, SAFE_SLUG,
        article_fingerprint, usable_generated_scene, MAX_SCENE_BYTES,
        MAX_ARCHIVE_BYTES, MAX_BATCH, MIN_FREE_GIB,
    )
    from .media_policy import MIN_JPEG_BYTES, MAX_JPEG_BYTES, jpeg_dimensions
    from .editorial_covers import story_subject
except ImportError:
    from generated_photos import (
        RELATIVE_DIR, MANIFEST_NAME, PROMPT_VERSION, ROOT, SAFE_SLUG,
        article_fingerprint, usable_generated_scene, MAX_SCENE_BYTES,
        MAX_ARCHIVE_BYTES, MAX_BATCH, MIN_FREE_GIB,
    )
    from media_policy import MIN_JPEG_BYTES, MAX_JPEG_BYTES, jpeg_dimensions
    from editorial_covers import story_subject


# Explicit image directions for recognizable jokes, followed by topic-level
# fallbacks so NEW articles still get a viable scene without hand-editing code.
SCENES = (
    (("毎週捕まる", "自力脱出"), "A fictional spy actor with a rescue bag stands in an empty room, staring solemnly at an empty chair and a neatly coiled rope; the open back door reveals his partner calmly walking home."),
    (("700人", "名前を呼ぶだけ"), "A weary television host holds a microphone beside a long queue of seated anonymous adult guests. Behind them a stagehand wheels away a still-wrapped game buzzer. Everyone remains solemn."),
    (("ネタバレ",), "A suited spokesperson at a modest press conference covers an unplugged router with a cloth as though revealing a magic trick. Two reporters wait with deadpan faces, holding disconnected phones."),
    (("限定アイス", "誰も食べない"), "A solemn adult collector kneels before an open home freezer packed with pristine plain ice cream bars, handling one with white museum gloves. An untouched spoon rests beside the freezer."),
    (("牛とロバ",), "A cow and a donkey calmly graze a few paces apart in a Japanese pasture. In the foreground two solemn adults aim phones at them while a farmer patiently holds a hay bucket."),
    (("戦力外",), "An older baseball fan in a plain unbranded cap solemnly hands his cheering megaphone to a suited stadium official across a folding table. The official measures the megaphone with a ruler."),
    (("起きる前",), "An office employee solemnly hands an apology envelope to a bewildered customer, while another employee is still installing a large padlock on a sealed filing cabinet."),
    (("責任だけ",), "A solemn office manager at a bare desk holds an absurdly large rubber stamp. A tiny desktop robot at the adjacent desk controls all of the keyboards and telephones."),
    (("充電待ち",), "Three small factory service robots queue patiently at one wall charging socket. The front robot holds a plain cardboard moving box while a human supervisor watches solemnly."),
    (("議題重複", "AI議長"), "Three solemn suited executives stand outside a glass meeting-room door with their chairs in their arms. Inside, a small tabletop robot presides over a nearly empty conference table."),
    (("夕飯ガチャ", "献立ガチャ"), "An exhausted parent stands by a ridiculous tabletop capsule lottery machine at dinner. Three family members in the background stare at an untouched table of ordinary dishes with perfectly serious faces."),
    (("スベるほど黒字", "笑わないで"), "An awkward comedian alone under a spotlight in a small comedy theater; rows of spectators deliberately sit stone-faced and silent; one person holds back a smile."),
    (("返信3日", "恋活アプリ"), "A slightly awkward couple in a Japanese cafe sitting side by side, each staring at a phone, both sharing a shy smile after an extremely long silent conversation."),
    (("上司の機嫌予報", "機嫌予報"), "An office employee studies an elaborate weather forecast of office moods on a monitor; a stern manager silently walks past with a tiny personal rain cloud above the desk."),
    (("地元100％", "土産"), "A tourist carrying a locally wrapped souvenir box at a small rural town boundary, while two very serious shopkeepers measure the package with a clipboard."),
    # Sport-specific cues: the generic word "選手" must never turn volleyball
    # or another sport into a baseball photograph.
    (("バレー", "バレーボール"), "A bright indoor volleyball court with a clearly visible volleyball net, fictional players in plain uniforms and fans solemnly watching an absurd game-day moment."),
    (("球団", "野球", "プロ野球"), "A Japanese baseball stadium at afternoon practice, with a completely serious team official, a confused player, and absurd fan memorabilia on a folding table."),
    (("会見", "謝罪"), "A serious formal press conference with suited actors, many empty chairs and an absurdly oversized stack of apology documents on the podium."),
    (("ドラマ", "最終回", "考察"), "A living room with several devoted television fans in deep concentration, a corkboard covered in blank notes and red strings, television playing a blank final scene."),
    (("転職", "求人"), "A strangely solemn office job interview with a humanoid service robot sitting opposite a person holding a folder."),
    (("管理職", "議長", "役員"), "A corporate meeting room with a large empty chair at the head of the table, a tiny robot at the center, and puzzled office executives who remain utterly serious."),
    (("工場", "製造"), "A tidy Japanese factory with a small industrial robot standing beside perfectly lined up workers, all posing for a matter-of-fact corporate photograph."),
    (("電話", "迷惑", "着信"), "A middle-aged person staring in disbelief at a ringing smartphone on a dining table, surrounded by suspiciously many identical phones."),
    (("学校", "卒業", "授業"), "A brightly lit Japanese classroom; students and a teacher face an absurd stack of paperwork with earnest expressions."),
    (("雨", "天気", "気象"), "A city street under a grey sky, where one office worker carries a comically tiny umbrella indoors while colleagues watch with straight faces."),
    (("推し", "ファン", "アイドル"), "An excited fan in a small apartment carefully organizing an absurd collection of merchandise, treating it like a museum exhibit."),
    (("研究", "論文", "開所"), "A research laboratory with fully suited scientists gathered in front of an almost empty display case, solemnly applauding a mysterious small object."),
    (("ゲーム", "RPG"), "A tabletop fantasy board game with tiny hand-painted characters and a serious group of adult players in a cozy living room."),
    (("牛", "牧場", "動物"), "A quiet rural pasture, two farm animals standing in an oddly formal portrait next to a confused farmer wearing work clothes."),
)
GENERIC_SCENES = {
    "frozen": "A deadpan shopkeeper in a dessert shop holds a melting ice cream inside an absurdly serious glass display case.",
    "livestock": "A farmer in a quiet pasture stares at two animals standing together like distinguished guests.",
    "romance": "Two people in a quiet Japanese cafe share an awkwardly serious romantic moment over untouched cups of coffee.",
    "gaming": "Adults in a living room take a tabletop fantasy game far too seriously, miniature figures arranged with military precision.",
    "school": "A school classroom where a serious teacher points at an absurdly elaborate arrangement of blank worksheets.",
    "music": "A concert rehearsal with a conductor and musicians holding perfectly straight faces during a strange pause.",
    "telephone": "A very serious office worker takes a call while multiple phones ring across an otherwise ordinary desk.",
    "weather": "Office workers look anxiously at a changing weather forecast inside a modern corporate office.",
    "baseball": "A baseball stadium where a perplexed player stands next to oddly formal officials and fans.",
    "parcel": "A puzzled delivery worker stands before an impossible-looking stack of packages in a neighborhood shop.",
    "movie": "Television drama fans confer intensely over an elaborate theory board in a small living room.",
    "security": "A serious corporate IT briefing in a conference room with a comically oversized locked document cabinet.",
    "robot": "A humanoid robot and puzzled human workers pose for a very formal office group photo.",
    "laboratory": "Scientists in a clean laboratory solemnly study a comically unremarkable object.",
    "meeting": "An office meeting frozen in awkward silence as colleagues stare at a bizarre ordinary object on the table.",
    "fandom": "Fans at a small event display their collectibles with the seriousness of an official press conference.",
    "document": "An official at a reception desk studies an impossibly tall pile of blank administrative forms.",
    "newspaper": "A street scene in contemporary Japan with ordinary people staring seriously at an absurd everyday mishap.",
}
NEGATIVE = (
    "written text, captions, headlines, letters, words, numbers, logo, watermark, "
    "press logo, brand marks, named real people, recognizable public figures, "
    "photomontage, collage, cartoon, anime, illustration, CGI, glossy stock photo, "
    "deformed faces, duplicate people, extra fingers, extra hands, low resolution, "
    "blurry, oversaturated, violence, weapons, gore"
)


def scene_description(article: dict) -> str:
    override = article.get("scene_prompt")
    if isinstance(override, str) and 20 <= len(override) <= 800:
        return override.strip()
    source = " ".join(str(article.get(key, "")) for key in ("title", "summary"))
    for keywords, direction in SCENES:
        if any(keyword in source for keyword in keywords):
            return direction
    return GENERIC_SCENES.get(story_subject(article), GENERIC_SCENES["newspaper"])


def scene_prompt(article: dict) -> str:
    """Keep the central absurdity visually legible; never pose as real evidence."""
    framing = (
        "eye-level medium-wide shot, 35mm lens",
        "side-on view with the key prop in the foreground, 50mm lens",
        "slightly elevated wide shot, 35mm lens",
        "over-the-shoulder view toward the absurd action, 40mm lens",
    )[stable_seed(article) % 4]
    # Put the photographic medium in the first CLIP chunk. "Sketch" also
    # means a drawing to SDXL; it must not appear as a positive style token.
    return (
        "Candid color photograph of anonymous Japanese adult actors. "
        + scene_description(article) + " "
        "A staged fictional live-action scene, neutral serious faces. "
        "Full-frame DSLR, realistic skin and fabric, natural daylight, "
        + framing + ", 16:9 composition. "
        "No title text, no captions, no logos, no recognizable real person."
    )


def stable_seed(article: dict) -> int:
    key = article["slug"] + "|" + article["title"]
    if article["slug"] == "stolen-doorbell-returned-by-parcel":
        # This unpublished scene needs genuinely new initial noise after the
        # original seed repeatedly produced appliances instead of a handbell.
        key += "|handbell-redraw-1"
    return int.from_bytes(hashlib.sha256(
        key.encode("utf-8")).digest()[:8], "big") % (2**63)


def workflow(article: dict, checkpoint: str, steps: int = 24) -> dict:
    if not checkpoint or "/" in checkpoint or "\\" in checkpoint or not checkpoint.endswith(".safetensors"):
        raise ValueError("Use a local checkpoint filename ending in .safetensors, not a URL or path")
    seed = stable_seed(article)
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": scene_prompt(article), "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": NEGATIVE, "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 1152, "height": 648, "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": steps, "cfg": 5.5, "sampler_name": "dpmpp_2m",
            "scheduler": "karras", "denoise": 1.0,
            "model": ["1", 0], "positive": ["2", 0],
            "negative": ["3", 0], "latent_image": ["4", 0]}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "mihassei-staged-scene"}},
    }


def loopback_base(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/") or not parsed.port):
        raise ValueError("Only an explicit local ComfyUI URL, e.g. http://127.0.0.1:8188, is allowed")
    return url.rstrip("/")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not forward local image-generation requests to external URLs."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)


class StorageLimitError(RuntimeError):
    """A batch-wide resource stop, rather than one bad article/image."""


def check_storage(paths: list[Path], min_free_gb: int = MIN_FREE_GIB) -> None:
    # Reserve space for the bounded PNG, JPEG and atomic-write scratch bytes.
    required = min_free_gb * 1024**3 + 32 * 1024**2
    if any(shutil.disk_usage(path).free < required for path in paths):
        raise StorageLimitError(f"Less than {min_free_gb} GiB plus image headroom free")


def archive_bytes(root: Path) -> int:
    directory = root / RELATIVE_DIR
    if directory.is_symlink():
        raise RuntimeError("Refusing symlinked generated-image directory")
    return sum(p.stat().st_size for p in directory.glob("*.jpg") if p.is_file())


def reserved_jpeg_bytes() -> int:
    return max(0, int(os.environ.get("VERIPSA_JPEG_RESERVED_BYTES", "0")))


def request_json(base: str, path: str, data: dict | None = None) -> dict:
    payload = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=payload,
        headers={"Content-Type": "application/json"} if payload is not None else {},
        method="POST" if payload is not None else "GET",
    )
    with LOCAL_OPENER.open(req, timeout=20) as resp:
        if resp.geturl().split("?", 1)[0] != base + path.split("?", 1)[0]:
            raise RuntimeError("ComfyUI redirected outside the configured local endpoint")
        if resp.status != 200:
            raise RuntimeError("Local ComfyUI returned HTTP " + str(resp.status))
        return json.loads(resp.read(2_000_000))


def create_png(base: str, article: dict, checkpoint: str, timeout: int,
               output_dir: Path | None = None, min_free_gb: int = MIN_FREE_GIB) -> bytes:
    queued = request_json(base, "/prompt", {
        "prompt": workflow(article, checkpoint),
        "client_id": "mihassei-local-scene",
    })
    identifier = queued.get("prompt_id")
    if not isinstance(identifier, str) or not identifier:
        raise RuntimeError("Local ComfyUI rejected the workflow: " + str(queued)[:500])
    deadline = time.monotonic() + timeout
    result = None
    while time.monotonic() < deadline:
        check_storage([ROOT] + ([output_dir] if output_dir else []), min_free_gb)
        history = request_json(base, "/history/" + urllib.parse.quote(identifier, safe=""))
        entry = history.get(identifier)
        if entry:
            if entry.get("status", {}).get("status_str") == "error":
                raise RuntimeError("Local ComfyUI generation failed: " + str(entry.get("status"))[:400])
            images = entry.get("outputs", {}).get("7", {}).get("images", [])
            if images:
                result = images[0]
                break
        time.sleep(2)
    if not isinstance(result, dict):
        raise TimeoutError("Local ComfyUI did not finish within " + str(timeout) + " seconds")
    if result.get("type") != "output" or not isinstance(result.get("filename"), str):
        raise RuntimeError("Unexpected local ComfyUI image output")
    filename = result["filename"]
    subfolder = result.get("subfolder", "")
    if (not isinstance(filename, str) or not filename.startswith("mihassei-staged-scene_")
            or not filename.endswith(".png") or Path(filename).name != filename or subfolder != ""):
        raise RuntimeError("Unexpected ComfyUI image name; refusing unsafe cleanup")
    params = urllib.parse.urlencode({
        "filename": filename, "subfolder": subfolder, "type": "output",
    })
    req = urllib.request.Request(base + "/view?" + params)
    try:
        with LOCAL_OPENER.open(req, timeout=40) as response:
            if response.geturl().split("?", 1)[0] != base + "/view":
                raise RuntimeError("Image download redirected away from local ComfyUI")
            payload = response.read(12 * 1024 * 1024 + 1)
        if len(payload) > 12 * 1024 * 1024 or not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Local ComfyUI did not return a valid, bounded PNG")
        return payload
    finally:
        # The dedicated Mac worker passes its own output directory. Delete only
        # this exact SaveImage result; never other users' ComfyUI files.
        if output_dir is not None:
            root = output_dir.resolve()
            candidate = root / filename
            if candidate.parent.resolve() == root and not candidate.is_symlink() and candidate.is_file():
                candidate.unlink()


def png_to_jpeg(png: bytes) -> bytes:
    try:
        from PIL import Image, ImageOps, ImageStat
    except ImportError as exc:
        raise RuntimeError("Pillow is needed only on the generation PC: python -m pip install Pillow") from exc
    with Image.open(io.BytesIO(png)) as source:
        if source.width * source.height > 4096 * 4096:
            raise ValueError("Generated scene exceeds the decoded pixel budget")
        source.load()
        if source.width < 640 or source.height < 360:
            raise ValueError("Generated scene is too small")
        resized = ImageOps.fit(source.convert("RGB"), (1200, 675), method=Image.Resampling.LANCZOS)
        if max(ImageStat.Stat(resized).stddev) < 2:
            raise ValueError("Generated scene is blank or nearly uniform; retain fallback")
        # A small per-article cap keeps storage/repository growth predictable.
        # Prioritize visual quality, then reduce JPEG quality only as needed.
        data = b""
        for quality in (84, 80, 76, 72, 68, 62):
            buffer = io.BytesIO()
            resized.save(buffer, format="JPEG", quality=quality, optimize=True)
            data = buffer.getvalue()
            if len(data) <= 700 * 1024:
                break
    if not MIN_JPEG_BYTES <= len(data) <= min(MAX_JPEG_BYTES, 700 * 1024) or jpeg_dimensions(data) != (1200, 675):
        raise ValueError("Generated image exceeds 700 KiB or failed JPEG validation")
    return data


def write_generated(article: dict, data: bytes, checkpoint: str, *, root: Path = ROOT,
                    origin: str = "local-comfyui") -> Path:
    """Atomic first-party image writes with provenance and a strict storage budget."""
    if origin == "github-cpu-lcm":
        if checkpoint != "SimianLuo/LCM_Dreamshaper_v7":
            raise ValueError("Unreviewed CPU model is forbidden")
    elif origin != "local-comfyui":
        raise ValueError("Unreviewed image origin is forbidden")
    slug = article["slug"]
    if not SAFE_SLUG.fullmatch(slug):
        raise ValueError("Invalid article slug")
    if not MIN_JPEG_BYTES <= len(data) <= MAX_SCENE_BYTES or jpeg_dimensions(data) != (1200, 675):
        raise ValueError("Refusing to save a non-conforming 1200x675 JPEG")
    target_dir = root / RELATIVE_DIR
    if target_dir.is_symlink() or (root / "assets").is_symlink():
        raise RuntimeError("Refusing symlinked generated-image directory")
    target_dir.mkdir(parents=True, exist_ok=True)
    image_path = target_dir / (slug + ".jpg")
    manifest_path = target_dir / MANIFEST_NAME
    for path in (image_path, manifest_path, image_path.with_suffix(".jpg.tmp"),
                 manifest_path.with_suffix(".json.tmp")):
        if path.is_symlink():
            raise RuntimeError("Refusing symlinked generated-image file")
    existing_size = image_path.stat().st_size if image_path.exists() else 0
    if archive_bytes(root) - existing_size + len(data) + reserved_jpeg_bytes() > MAX_ARCHIVE_BYTES:
        raise StorageLimitError("Generated JPEG archive would exceed 1.5 GiB; photos preserved")
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("images"), dict):
            raise ValueError("Invalid existing manifest; refusing to overwrite it")
    else:
        manifest = {"version": 1, "images": {}}
    image_tmp = image_path.with_suffix(".jpg.tmp")
    image_tmp.write_bytes(data)
    os.replace(image_tmp, image_path)
    manifest["images"][slug] = {
        "origin": origin,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "article_sha256": article_fingerprint(article),
        "prompt_version": PROMPT_VERSION,
        "model": checkpoint,
        "seed": stable_seed(article),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    # A successful deliberate repair replaces that article's visual hold.
    # Preserve holds for every other article when merging new image records.
    if isinstance(manifest.get("rejected"), dict):
        manifest["rejected"].pop(slug, None)
    manifest_tmp = manifest_path.with_suffix(".json.tmp")
    manifest_tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(manifest_tmp, manifest_path)
    if not usable_generated_scene(article, root=root):
        raise RuntimeError("Generated file failed manifest integrity check after writing")
    return image_path


def load_articles(root: Path = ROOT) -> list[dict]:
    items = json.loads((root / "content/articles.json").read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError("content/articles.json must be an array")
    return sorted(items, key=lambda a: (a.get("published_at") or "", a.get("slug") or ""), reverse=True)


def held_scene(article: dict, *, root: Path = ROOT) -> bool:
    """Keep a visually rejected scene on fallback until its source changes."""
    slug = article.get("slug")
    if not isinstance(slug, str) or not SAFE_SLUG.fullmatch(slug):
        return False
    try:
        payload = json.loads((root / RELATIVE_DIR / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return False
    rejected = payload.get("rejected")
    if not isinstance(rejected, dict):
        return False
    record = rejected.get(slug)
    return (isinstance(record, dict)
            and record.get("article_sha256") == article_fingerprint(article))


def pending_articles(articles: list[dict], *, root: Path = ROOT, force: bool = False,
                     slug: str = "", limit: int = MAX_BATCH) -> list[dict]:
    pending = [
        a for a in articles
        if (not slug or a.get("slug") == slug)
        and (force or not usable_generated_scene(a, root=root))
        and (force or not held_scene(a, root=root))
    ]
    return pending[:limit] if limit else pending


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Free local ComfyUI: staged photorealistic satire scenes")
    ap.add_argument("--dry-run", action="store_true", help="Print pending scene directions; do not call ComfyUI")
    ap.add_argument("--server", default="http://127.0.0.1:8188", help="Must be a loopback ComfyUI URL")
    ap.add_argument("--checkpoint", default="sd_xl_base_1.0.safetensors", help="Installed local SDXL checkpoint filename")
    ap.add_argument("--acknowledge-model-license", action="store_true",
                    help="Confirm you checked your exact checkpoint's commercial-use license")
    ap.add_argument("--slug", default="", help="Generate only this published article")
    ap.add_argument("--limit", type=int, default=MAX_BATCH, help="Maximum new scenes this run (1–4)")
    ap.add_argument("--force", action="store_true", help="Regenerate even if a verified local scene exists")
    ap.add_argument("--timeout", type=int, default=600, help="Time limit in seconds for each scene")
    ap.add_argument("--output-dir", default="", help="Optional dedicated ComfyUI output directory; remove only this job's PNG")
    ap.add_argument("--min-free-gb", type=int, default=12, help="Stop generation if repo or output disk has less free space")
    args = ap.parse_args(argv)
    base = loopback_base(args.server)
    if not 1 <= args.limit <= MAX_BATCH or not 30 <= args.timeout <= 3600 or not MIN_FREE_GIB <= args.min_free_gb <= 100:
        ap.error("limit must be 1–4, timeout 30–3600 seconds, free-space floor 12–100 GiB")
    articles = load_articles()
    if args.slug and not any(a.get("slug") == args.slug for a in articles):
        ap.error("Article slug is not in content/articles.json")
    work = pending_articles(articles,force=args.force,slug=args.slug,limit=args.limit)
    print(f"Published articles: {len(articles)} | scenes pending: {len(work)}", flush=True)
    if args.dry_run:
        for article in work:
            print(json.dumps({"slug":article["slug"],"scene":scene_description(article),
                              "seed":stable_seed(article)},ensure_ascii=False))
        return 0
    if not args.acknowledge_model_license:
        ap.error("Review checkpoint/LoRA rights and add --acknowledge-model-license before generating")
    if not work:
        print("All published articles already have valid local scenes.")
        return 0
    # Validate checkpoint before starting jobs, without fetching anything.
    workflow(work[0], args.checkpoint)
    succeeded, failed = 0, []
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else None
    if output_dir is not None and (not output_dir.is_dir() or output_dir.is_symlink()):
        ap.error("--output-dir must be an existing directory owned by the local ComfyUI worker")
    for idx, article in enumerate(work, 1):
        volumes = [ROOT] + ([output_dir] if output_dir is not None else [])
        print(f"[{idx}/{len(work)}] Creating a staged comedy photo for {article['slug']}",flush=True)
        try:
            check_storage(volumes, args.min_free_gb)
            if archive_bytes(ROOT) + MAX_SCENE_BYTES + reserved_jpeg_bytes() > MAX_ARCHIVE_BYTES:
                raise StorageLimitError("Generated JPEG archive at capacity; photos preserved")
            image = png_to_jpeg(create_png(
                base, article, args.checkpoint, args.timeout, output_dir=output_dir,
                min_free_gb=args.min_free_gb))
            check_storage(volumes, args.min_free_gb)
            destination = write_generated(article, image, args.checkpoint)
            print("  Saved validated JPEG:", destination, flush=True)
            succeeded += 1
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            failed.append(article["slug"])
            print(f"  Skipped (existing site fallback remains): {type(exc).__name__}: {exc}",file=sys.stderr)
            if isinstance(exc, (StorageLimitError, TimeoutError)):
                # A timed-out ComfyUI job may still be running. Do not enqueue
                # more GPU work behind it. The dedicated worker owns shutdown;
                # already validated JPEGs are still saved by local_scene_sync.
                print("  Stopping this batch; remaining articles will retry next run.", file=sys.stderr)
                break
    print(f"Scenes created: {succeeded} | failed: {len(failed)}",flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
