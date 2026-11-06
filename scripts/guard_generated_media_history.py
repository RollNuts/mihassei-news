#!/usr/bin/env python3
"""Block stale-photo provenance rewrites in published-news pull requests.

If an existing JPEG's SHA-256 is unchanged, its generation record is immutable.
Editing article_sha256 to make an old image pass a new story fingerprint is not
image regeneration. A JSON reserialization must not round 64-bit random seeds.
No network, external API, or extra generated images are involved.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

try:
    from .generated_photos import article_fingerprint, usable_generated_scene
    from .local_scene_photos import stable_seed
except ImportError:
    from generated_photos import article_fingerprint, usable_generated_scene
    from local_scene_photos import stable_seed

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "assets/generated/manifest.json"
SHA = re.compile(r"[0-9a-f]{40}\Z")


def images(payload: object) -> dict:
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("Invalid generated photo manifest version")
    result = payload.get("images")
    if not isinstance(result, dict):
        raise ValueError("Generated photo manifest needs images map")
    return result


def incompatible_rewrites(previous: dict, current: dict) -> list[str]:
    """Review existing image identities; an old JPEG cannot be recertified."""
    before, after = images(previous), images(current)
    failures = []
    for slug in sorted(before.keys() & after.keys()):
        old, new = before[slug], after[slug]
        if not isinstance(old, dict) or not isinstance(new, dict):
            failures.append(slug + ": malformed image record")
            continue
        if old == new:
            continue
        if old.get("sha256") == new.get("sha256"):
            changed = sorted(k for k in (old.keys() | new.keys())
                             if old.get(k) != new.get(k))
            failures.append(slug + ": unchanged JPEG SHA but changed record "
                            + ", ".join(changed)
                            + "; preserve the old manifest and use the SVG fallback "
                              "until a truly new JPEG is generated")
    return failures


def verify_new_images(previous: dict, current: dict, *, root: Path = ROOT) -> list[str]:
    """Validate genuine new/replaced JPEG bytes against the current article."""
    before, after = images(previous), images(current)
    try:
        published = json.loads((root / "content/articles.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Cannot inspect current published article data") from exc
    by_slug = {a["slug"]: a for a in published}
    failures = []
    for slug, record in after.items():
        old = before.get(slug)
        if old is not None and old == record:
            continue
        if (isinstance(old, dict) and isinstance(record, dict)
                and old.get("sha256") == record.get("sha256")):
            continue  # Already rejected by incompatible_rewrites.
        article = by_slug.get(slug)
        if not article:
            failures.append(slug + ": new photo has no published article")
            continue
        if not isinstance(record, dict):
            failures.append(slug + ": malformed new photo record")
            continue
        origin = record.get("origin")
        if origin in {"local-comfyui", "github-cpu-lcm"}:
            if record.get("seed") != stable_seed(article):
                failures.append(slug + ": generation seed does not match article; "
                                "avoid rounding large JSON integers")
        elif origin == "chatgpt-imagegen":
            # Native ChatGPT generations have a provider generation receipt
            # rather than the deterministic local/CPU seed. generated_scene_entry()
            # validates that receipt, model, bytes and current article fingerprint.
            if "seed" in record:
                failures.append(slug + ": chatgpt-imagegen record must use its "
                                "generation_id receipt, not a synthetic seed")
        else:
            failures.append(slug + ": new photo has an unsupported generation origin")
        if record.get("article_sha256") != article_fingerprint(article):
            failures.append(slug + ": image record is not for the current text and scene")
        if not usable_generated_scene(article, root=root):
            failures.append(slug + ": changed JPEG is missing or failed byte/dimension "
                            "and manifest integrity verification")
    return failures


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not SHA.fullmatch(args[0].lower()):
        print("Usage: python scripts/guard_generated_media_history.py <base-git-sha>",
              file=sys.stderr)
        return 2
    base = args[0].lower()
    if base == "0" * 40:
        print("Initial commit has no prior image history; nothing to compare.")
        return 0
    fetched = subprocess.run(
        ["git", "show", base + ":" + MANIFEST],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=15
    )
    if fetched.returncode:
        print("Cannot read baseline image manifest at " + base[:12] +
              "; refusing to guess: " + fetched.stderr[:350], file=sys.stderr)
        return 2
    try:
        previous = json.loads(fetched.stdout)
        current = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
        errors = incompatible_rewrites(previous, current)
        errors.extend(verify_new_images(previous, current))
    except (OSError, ValueError, TypeError) as exc:
        print("Generated-image history check failed: " + str(exc), file=sys.stderr)
        return 2
    for err in errors:
        print("::error title=Generated photo provenance::" + err, file=sys.stderr)
    if errors:
        print("Rejected " + str(len(errors)) +
              " provenance error(s). Do not change old photo hashes or seeds to "
              "retain art after editorial revisions.", file=sys.stderr)
        return 1
    print("Generated photo history: validated; no old JPEG was falsely reapproved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
