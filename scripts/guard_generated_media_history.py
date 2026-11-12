#!/usr/bin/env python3
"""Block stale-photo provenance rewrites in published-news pull requests.

If an existing JPEG's SHA-256 is unchanged, its generation record is immutable.
Editing article_sha256 to make an old image pass a new story fingerprint is not
image regeneration. A JSON reserialization must not round 64-bit random seeds.
No network, external API, or extra generated images are involved.
"""
from __future__ import annotations

import json
import hashlib
import re
import subprocess
import sys
from pathlib import Path

try:
    from .cpu_photo_model import ORIGIN as CPU_ORIGIN, REFERENCE_ORIGIN
    from .generated_photos import (
        article_fingerprint, usable_generated_scene, generation_record_fingerprint,
        accepted_review_matches, valid_native_text_revision, review_timestamp, SAFE_SLUG,
    )
    from .local_scene_photos import stable_seed
except ImportError:
    from cpu_photo_model import ORIGIN as CPU_ORIGIN, REFERENCE_ORIGIN
    from generated_photos import (
        article_fingerprint, usable_generated_scene, generation_record_fingerprint,
        accepted_review_matches, valid_native_text_revision, review_timestamp, SAFE_SLUG,
    )
    from local_scene_photos import stable_seed

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "assets/generated/manifest.json"
ARTICLES = "content/articles.json"
REVIEWS = "content/visual_reviews.json"
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
        if origin in {"local-comfyui", "github-cpu-lcm", CPU_ORIGIN, REFERENCE_ORIGIN}:
            if type(record.get("seed")) is not int or record["seed"] != stable_seed(article):
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


def _reviews(payload: object) -> dict:
    if (not isinstance(payload, dict) or type(payload.get("version")) is not int
            or payload["version"] != 1 or not isinstance(payload.get("reviews"), dict)):
        raise ValueError("Invalid visual-review document")
    return payload["reviews"]


def _articles(payload: object) -> dict:
    if (not isinstance(payload, list) or any(not isinstance(item, dict)
            or not isinstance(item.get("slug"), str) or not SAFE_SLUG.fullmatch(item["slug"])
            for item in payload)):
        raise ValueError("Invalid article document")
    result = {item["slug"]: item for item in payload}
    if len(result) != len(payload):
        raise ValueError("Duplicate article slug")
    return result


def _same_json(left: object, right: object) -> bool:
    # Python considers 0, 0.0 and False equal; provenance must retain JSON types.
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def verify_text_revision_reviews(previous: dict, current: dict,
                                 previous_articles: object, previous_reviews: object,
                                 baseline_image_sha256: dict, *, root: Path = ROOT) -> list[str]:
    """Prove explicit same-scene reuse against the real baseline, never self-approval.

    The original native generation record stays immutable. The base JPEG bytes
    and accepted review establish prior adoption; the current review binds the
    same bytes to a changed text after a new actual visual/readability review.
    """
    old_images, new_images = images(previous), images(current)
    old_articles, old_reviews = _articles(previous_articles), _reviews(previous_reviews)
    new_articles = _articles(json.loads((root / ARTICLES).read_text(encoding="utf-8")))
    new_reviews = _reviews(json.loads((root / REVIEWS).read_text(encoding="utf-8")))
    old_rejected, new_rejected = previous.get("rejected", {}), current.get("rejected", {})
    if not isinstance(old_rejected, dict) or not isinstance(new_rejected, dict):
        raise ValueError("Invalid rejection history")
    failures = []
    for slug, review in sorted(new_reviews.items()):
        if not isinstance(review, dict) or "reuse" not in review:
            continue
        prefix = slug + ": native text-revision reuse "
        old, record = old_images.get(slug), new_images.get(slug)
        before_article, article = old_articles.get(slug), new_articles.get(slug)
        before_review = old_reviews.get(slug)
        if not all(isinstance(value, dict) for value in (old, record, before_article, article, before_review)):
            failures.append(prefix + "requires an existing article, image and accepted review")
            continue
        if (generation_record_fingerprint(old) != generation_record_fingerprint(record)
                or not _same_json(old_rejected.get(slug), new_rejected.get(slug))):
            failures.append(prefix + "must preserve the original generation and rejection history")
            continue
        if not valid_native_text_revision(article, record, review, new_rejected):
            failures.append(prefix + "has an invalid current review, source approval, timestamp or scene binding")
            continue
        old_sha, current_sha = article_fingerprint(before_article), article_fingerprint(article)
        if "reuse" in before_review:
            previously_accepted = valid_native_text_revision(before_article, old, before_review, old_rejected)
            original = before_review.get("reuse", {}).get("original_review") if isinstance(before_review.get("reuse"), dict) else None
        else:
            previously_accepted = (old.get("article_sha256") == old_sha
                                   and accepted_review_matches(before_review, old_sha, old.get("sha256")))
            original = before_review
            hold = old_rejected.get(slug)
            if isinstance(hold, dict) and (hold.get("article_sha256") == old_sha
                    or hold.get("image_sha256", hold.get("sha256")) == old.get("sha256")):
                previously_accepted = False
        if (not previously_accepted or not isinstance(original, dict)
                or generation_record_fingerprint(review["reuse"]["original_review"]) != generation_record_fingerprint(original)):
            failures.append(prefix + "does not retain the actual baseline accepted review")
            continue
        if (baseline_image_sha256.get(slug) != old.get("sha256")
                or not usable_generated_scene(article, root=root)):
            failures.append(prefix + "must keep the actual baseline JPEG bytes intact")
            continue
        if (before_article.get("scene_prompt") != article.get("scene_prompt")
                or before_article.get("category") != article.get("category")
                or before_article.get("visual_strategy", "cpu-photo") != article.get("visual_strategy", "cpu-photo")
                or before_article.get("published_at") != article.get("published_at")
                or before_article.get("fiction_at") != article.get("fiction_at")):
            failures.append(prefix + "cannot change the scene, category, visual strategy or original publication dates")
            continue
        # Existing, unchanged opt-ins continue to work on later unrelated PRs.
        if old_sha == current_sha and _same_json(before_review, review):
            continue
        modified = review_timestamp(article.get("modified_at"))
        before_modified = review_timestamp(before_article.get("modified_at", before_article.get("published_at")))
        if (old_sha == current_sha or modified is None or before_modified is None
                or modified <= before_modified
                or review_timestamp(review["reviewed_at"]) <= review_timestamp(before_review["reviewed_at"])):
            failures.append(prefix + "requires changed text and a newer actual modification/review time")
    return failures


def _baseline_bytes(base: str, path: str, *, root: Path) -> bytes:
    fetched = subprocess.run(["git", "show", base + ":" + path], cwd=root,
                             capture_output=True, check=False, timeout=15)
    if fetched.returncode:
        raise ValueError("Cannot read baseline " + path + "; refusing to guess")
    return fetched.stdout


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not SHA.fullmatch(args[0].lower()):
        print("Usage: python scripts/guard_generated_media_history.py <base-git-sha>",
              file=sys.stderr)
        return 2
    base = args[0].lower()
    if base == "0" * 40:
        try:
            review_path = ROOT / REVIEWS
            records = _reviews(json.loads(review_path.read_text(encoding="utf-8"))) if review_path.exists() else {}
            if any(isinstance(review, dict) and "reuse" in review for review in records.values()):
                print("Initial commit cannot prove a prior accepted photo for text-revision reuse.", file=sys.stderr)
                return 1
        except (OSError, ValueError, TypeError):
            return 2
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
        errors.extend(verify_new_images(previous, current, root=ROOT))
        review_path = ROOT / REVIEWS
        current_reviews = _reviews(json.loads(review_path.read_text(encoding="utf-8"))) if review_path.exists() else {}
        reuse_slugs = [slug for slug, review in current_reviews.items()
                       if isinstance(review, dict) and "reuse" in review]
        if reuse_slugs:
            previous_articles = json.loads(_baseline_bytes(base, ARTICLES, root=ROOT))
            previous_reviews = json.loads(_baseline_bytes(base, REVIEWS, root=ROOT))
            baseline_images = {slug: hashlib.sha256(_baseline_bytes(
                base, "assets/generated/" + slug + ".jpg", root=ROOT)).hexdigest()
                for slug in reuse_slugs if isinstance(slug, str) and SAFE_SLUG.fullmatch(slug)}
            errors.extend(verify_text_revision_reviews(previous, current, previous_articles,
                                                       previous_reviews, baseline_images, root=ROOT))
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

