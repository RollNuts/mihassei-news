"""Pure, internal plans for new weekly issues; never generate or accept images.

Existing issue snapshots are not upgraded here. The caller validates the issue's
editorial fingerprint and actual reference JPEG bytes before using these inputs.
"""
from __future__ import annotations

import copy
import re

if __package__:
    from .photo_body_profiles import (
        body_profile_fingerprint, validate_body_profile, validate_body_reference,
    )
else:
    from photo_body_profiles import (
        body_profile_fingerprint, validate_body_profile, validate_body_reference,
    )


SHA = re.compile(r"[0-9a-f]{64}\Z")
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
REF_PATH = re.compile(r"assets/(?:generated|casters)/[a-z0-9-]+\.jpg\Z")


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing photo plan " + label)
    return value


def _reference(reference, *, master=False):
    if (not isinstance(reference, dict)
            or not isinstance(reference.get("path"), str)
            or not REF_PATH.fullmatch(reference["path"])
            or not isinstance(reference.get("sha256"), str)
            or not SHA.fullmatch(reference["sha256"])):
        raise ValueError("Invalid photo plan face/master reference")
    keys = ["path", "sha256"]
    if master or "source_png_sha256" in reference:
        if (not isinstance(reference.get("source_png_sha256"), str)
                or not SHA.fullmatch(reference["source_png_sha256"])):
            raise ValueError("Invalid photo plan source PNG reference")
        keys.append("source_png_sha256")
    if master:
        if (not isinstance(reference.get("provenance"), str)
                or not reference["provenance"].startswith("assets/")):
            raise ValueError("Invalid photo plan master provenance")
        return copy.deepcopy(reference)
    return {key: reference[key] for key in keys}


def _identity(model):
    if (not isinstance(model, dict) or not isinstance(model.get("id"), str)
            or not SLUG.fullmatch(model["id"]) or model.get("fictional") is not True
            or type(model.get("age")) is not int or not 20 <= model["age"] <= 99):
        raise ValueError("Photo plan requires a fictional adult model")
    for key in ("name", "appearance"):
        _text(model.get(key), key)
    character = model.get("character")
    if not isinstance(character, dict):
        raise ValueError("Missing photo plan character")
    for key in ("first_person", "voice", "weakness"):
        _text(character.get(key), "character " + key)
    personality = character.get("personality")
    traits = [personality] if isinstance(personality, str) else personality
    if not isinstance(traits, list) or not traits:
        raise ValueError("Missing photo plan personality")
    for trait in traits:
        _text(trait, "personality")
    likes = character.get("likes")
    if not isinstance(likes, list) or not likes:
        raise ValueError("Missing photo plan character likes")
    for like in likes:
        _text(like, "character like")


def _prepared_body(reference):
    body = validate_body_reference(reference)
    if body["state"] != "accepted_reference":
        raise ValueError("Photo plan body reference is unprepared; face reference is insufficient")
    return body


def freeze_model_plan(model):
    """Copy one prepared adult's design into a new issue's model_snapshot."""
    _identity(model)
    body = _prepared_body(model.get("body_reference"))
    profile = validate_body_profile(model.get("body_profile"))
    master = _reference(model.get("master"), master=True)
    face = _reference(model.get("preferred_face_reference", master))
    return copy.deepcopy({
        **{key: model[key] for key in ("id", "name", "age", "fictional", "appearance", "character")},
        "master": master,
        "body_profile": profile,
        "body_profile_sha256": body_profile_fingerprint(profile),
        "face_reference": face,
        "body_reference": body,
    })


def generation_inputs(issue, cut):
    """Return metadata from a frozen issue only; reference bytes need caller QA."""
    if (not isinstance(issue, dict) or not isinstance(issue.get("issue_sha256"), str)
            or not SHA.fullmatch(issue["issue_sha256"])):
        raise ValueError("Invalid photo plan issue fingerprint")
    snapshot = issue.get("model_snapshot")
    if not isinstance(snapshot, dict) or "body_profile_sha256" not in snapshot:
        raise ValueError("Legacy photo snapshot has no frozen body plan; keep its original records")
    _identity(snapshot)
    if snapshot["id"] != issue.get("model_id"):
        raise ValueError("Photo plan snapshot/model mismatch")
    profile = validate_body_profile(snapshot.get("body_profile"))
    design_sha = body_profile_fingerprint(profile)
    if snapshot["body_profile_sha256"] != design_sha:
        raise ValueError("Frozen photo body profile fingerprint mismatch")
    _reference(snapshot.get("master"), master=True)
    face = _reference(snapshot.get("face_reference"))
    body = _prepared_body(snapshot.get("body_reference"))
    if not isinstance(cut, str) or not re.fullmatch(r"(?:0[1-9]|10)", cut):
        raise ValueError("Photo plan needs a cut ID from 01 through 10")
    shots = issue.get("shots")
    matches = [shot for shot in shots if isinstance(shot, dict) and shot.get("id") == cut] if isinstance(shots, list) else []
    if len(matches) != 1:
        raise ValueError("Photo plan cut is missing or duplicated")
    shot = _text(matches[0].get("description"), "shot description")
    setting = issue.get("setting")
    if (not isinstance(setting, dict) or type(setting.get("year")) is not int
            or setting["year"] < 2031 or type(setting.get("month")) is not int
            or not 1 <= setting["month"] <= 12 or setting.get("aspect_ratio") not in ("16:9", "original")):
        raise ValueError("Invalid photo plan setting")
    location = _text(setting.get("location"), "location")
    dimensions = "、".join(f"{label}{profile[key]:g}{unit}" for key, label, unit in (
        ("height_cm", "身長", "cm"), ("bust_cm", "バスト", "cm"),
        ("waist_cm", "ウエスト", "cm"), ("hip_cm", "ヒップ", "cm"),
        ("shoulder_width_cm", "肩幅", "cm"), ("inseam_cm", "股下", "cm"),
        ("heads_tall", "頭身", "")))
    framing = "横長16:9。" if setting["aspect_ratio"] == "16:9" else "写真ごとの元の縦横比を保つ。"
    prompt = (
        f"架空の成人モデル・{snapshot['name']}（{snapshot['age']}歳）の自然な雑誌写真。"
        f"人物設定: {snapshot['appearance']}\n"
        "顔と髪はface参照、全身の自然な比例はbody参照を使い、同じ成人として維持する。\n"
        f"創作上の体型設計: {dimensions}。写真からの実測値ではなく、一致は実画像で確認する。\n"
        f"舞台は{setting['year']}年{setting['month']}月、{location}。{framing}\n"
        f"このカット: {shot}\n"
        "通常の衣服・水着のファッション写真として自然な姿勢と表情で撮る。"
    )
    references = [{"role": "face", **face},
                  {"role": "body", **{key: body[key] for key in ("path", "sha256", "source_png_sha256")}}]
    return {"verification": "metadata_only", "requires_visual_review": True,
            "issue_sha256": issue["issue_sha256"], "cut_id": cut,
            "body_profile_sha256": design_sha, "prompt": prompt,
            "references": copy.deepcopy(references)}
