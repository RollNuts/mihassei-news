"""Pure validation and fingerprints for internal, authored fictional body designs.

These values are character design instructions, not measurements from photos or
proof that a generated image matches. Face and body references remain separate.
"""
from __future__ import annotations

import hashlib
import json
import math
import re


BASIS = "authored_fictional_design_cm"
# Broad unit/error checks, not a photographic acceptance or attractiveness test.
PROFILE_RANGES = {
    "height_cm": (120, 220),
    "bust_cm": (50, 160),
    "waist_cm": (40, 150),
    "hip_cm": (50, 170),
    "shoulder_width_cm": (25, 60),
    "inseam_cm": (40, 120),
    "heads_tall": (5, 9),
}
SHA = re.compile(r"[0-9a-f]{64}\Z")
BODY_PATH = re.compile(r"assets/generated/[a-z0-9-]+\.jpg\Z")


def validate_body_profile(profile):
    """Return a normalized copy, rejecting missing fields and non-finite values."""
    if (not isinstance(profile, dict)
            or set(profile) != {"version", "basis", *PROFILE_RANGES}
            or type(profile.get("version")) is not int or profile["version"] != 1
            or profile.get("basis") != BASIS):
        raise ValueError("Invalid authored fictional body profile")
    normalized = {"version": 1, "basis": BASIS}
    for field, (low, high) in PROFILE_RANGES.items():
        value = profile[field]
        if (type(value) not in (int, float) or not low <= value <= high
                or not math.isfinite(value)):
            raise ValueError("Invalid body profile " + field)
        normalized[field] = int(value) if value == int(value) else value
    if normalized["inseam_cm"] >= normalized["height_cm"]:
        raise ValueError("Body profile inseam must be shorter than height")
    return normalized


def body_profile_fingerprint(profile):
    """Hash only the authored design; references/reviews/issue state are separate."""
    encoded = json.dumps(validate_body_profile(profile), ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_body_reference(reference):
    """Check a body-only reference descriptor without reading images or files."""
    if not isinstance(reference, dict):
        raise ValueError("Invalid photo body reference")
    if reference == {"state": "unprepared"}:
        return dict(reference)
    required = {"state", "path", "sha256", "source_png_sha256", "provenance"}
    if (set(reference) != required or reference.get("state") != "accepted_reference"
            or not isinstance(reference.get("path"), str)
            or not BODY_PATH.fullmatch(reference["path"])
            or any(not isinstance(reference.get(key), str)
                   or not SHA.fullmatch(reference[key])
                   for key in ("sha256", "source_png_sha256"))
            or not isinstance(reference.get("provenance"), str)
            or not reference["provenance"].startswith("assets/")):
        raise ValueError("Invalid accepted photo body reference")
    return dict(reference)
