"""Read publication metadata without inferring task execution or photo stock.

This is an observation of one checkout, not a live-delivery or visual-review gate.
Missing inputs stay unavailable; a closed calendar date without a post is not a
claim that an automation failed or that a market was open.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

try:
    from .photo_editions import load_photo_editions
    from .photo_stories import read_story_document
except ImportError:
    from photo_editions import load_photo_editions
    from photo_stories import read_story_document

JST = timezone(timedelta(hours=9))


def _read(root, relative):
    path = Path(root) / relative
    if not path.is_file() or path.is_symlink():
        raise ValueError("Missing or unsafe " + relative)
    return json.loads(path.read_text(encoding="utf-8"))


def _stamp(value):
    if not isinstance(value, str):
        raise ValueError("Missing actual publication timestamp")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Actual publication timestamp needs a timezone")
    return stamp


def _timeline(rows, identity, now):
    if not isinstance(rows, list):
        raise ValueError("Publication records must be a list")
    seen, dated, future = set(), [], 0
    for row in rows:
        key = row.get(identity) if isinstance(row, dict) else None
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError("Missing or duplicate publication identity")
        seen.add(key)
        stamp = _stamp(row.get("published_at"))
        if stamp > now:
            future += 1
        else:
            dated.append((stamp, key, row["published_at"]))
    today = now.astimezone(JST).date()
    latest = max(dated, default=None)
    return {
        "recorded_count": len(rows), "published_by_observation_count": len(dated),
        "today_count": sum(stamp.astimezone(JST).date() == today for stamp, *_ in dated),
        "previous_day_count": sum(stamp.astimezone(JST).date() == today - timedelta(days=1)
                                  for stamp, *_ in dated),
        "future_dated_count": future,
        "latest": ({identity: latest[1], "published_at": latest[2],
                    "published_at_jst": latest[0].astimezone(JST).isoformat(),
                    "date_jst": latest[0].astimezone(JST).date().isoformat()}
                   if latest else None),
    }


def _catalog(root):
    catalog = _read(root, "content/photo_issues.json")
    if (not isinstance(catalog, dict) or catalog.get("schema_version") != 1
            or not isinstance(catalog.get("models"), list)
            or not isinstance(catalog.get("issues"), list)):
        raise ValueError("Invalid photo catalog")
    models = catalog["models"]
    if (not models or any(not isinstance(model, dict) or not isinstance(model.get("id"), str)
                         or not isinstance(model.get("name"), str) for model in models)
            or len({model["id"] for model in models}) != len(models)):
        raise ValueError("Invalid photo model roster")
    return catalog


def _photo(root, now):
    catalog = _catalog(root)
    data = _read(root, "content/photo_editions.json")
    # Reuse the existing metadata gate; this does not read or approve image bytes.
    public = load_photo_editions(root, catalog=catalog, published=catalog["issues"], now=now)
    timeline = _timeline([row for row in catalog["issues"] if row.get("state") == "published"],
                         "slug", now)
    actual = {row["slug"]: row for row in catalog["issues"]
              if row.get("state") == "published" and _stamp(row.get("published_at")) <= now}
    def photo_count(slugs):
        count = 0
        for slug in slugs:
            images = actual[slug].get("images")
            if not isinstance(images, dict):
                raise ValueError("Published photo feature is missing image records")
            count += len(images)
        return count
    by_slug = {row["slug"]: row for row in public}
    unfinished = []
    for edition in data["editions"]:
        if edition.get("release_mode") != "daily":
            continue
        schedule = edition["schedule"]
        released = by_slug.get(edition["slug"], {}).get("features", [])
        if len(released) < len(schedule["order"]):
            unfinished.append((schedule["week_start_date_jst"], edition, released))
    oldest = None
    if unfinished:
        week, edition, released = min(unfinished, key=lambda item: item[0])
        order = edition["schedule"]["order"]
        next_model = order[len(released)]
        names = {model["id"]: model["name"] for model in catalog["models"]}
        due = datetime.fromisoformat(week).date() + timedelta(days=len(released))
        oldest = {"slug": edition["slug"], "week_start_date_jst": week,
                  "published_feature_count": len(released), "scheduled_model_count": len(order),
                  "recorded_photo_count": photo_count(released),
                  "next_model_id": next_model, "next_model_name": names[next_model],
                  "next_scheduled_date_jst": due.isoformat()}
    return {**timeline, "recorded_published_photo_count": photo_count(actual),
            "oldest_incomplete_daily_edition": oldest,
            "stock_status": "not_observed",
            "stock_reason": "Candidate branches, original readbacks and visual selection are not inspected."}


def _cast(root, now):
    catalog = _catalog(root)
    stories = read_story_document(root)["stories"]
    _timeline(stories, "id", now)  # Duplicate IDs and invalid dates must not disappear into per-person totals.
    model_ids = {model["id"] for model in catalog["models"]}
    if any(story.get("model_id") not in model_ids for story in stories):
        raise ValueError("Unknown model in creator story records")
    rows = []
    for model in catalog["models"]:
        timeline = _timeline([story for story in stories if story["model_id"] == model["id"]], "id", now)
        latest = timeline["latest"]
        gap = (max(0, (now.astimezone(JST).date()
                       - datetime.fromisoformat(latest["date_jst"]).date()).days - 1)
               if latest else None)
        rows.append({"model_id": model["id"], "name": model["name"], **timeline,
                     "completed_days_since_latest_post": gap})
    return {"models": rows}


def collect(root, *, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Content observation clock needs a timezone")
    result = {"basis": "checkout_publication_metadata", "observed_at_jst": now.astimezone(JST).isoformat(),
              "date_jst": now.astimezone(JST).date().isoformat(),
              "live_delivery_verified": False,
              "schedule_evaluation": "not_performed",
              "note": "Task schedules/runs, holiday calendars and intentional skipped slots are not inspected. "
                      "Today's absence is not a failure; closed-date gaps are publication observations only."}
    for name, operation in (
        ("news", lambda: _timeline(_read(root, "content/articles.json"), "slug", now)),
        ("market", lambda: _timeline(_read(root, "content/market_bulletins.json"), "slug", now)),
        ("photo_editions", lambda: _photo(root, now)),
        ("cast_stories", lambda: _cast(root, now)),
    ):
        try:
            result[name] = {"status": "observed", **operation()}
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            # Keep other channels observable and never replace unknown counts with zero.
            result[name] = {"status": "unavailable", "reason": str(exc)}
    return result
