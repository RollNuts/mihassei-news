"""Frozen weekly photo contents, using observed PVs or an explicit fallback."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import random
import re
import secrets
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
JST = timezone(timedelta(hours=9))
API = "https://veripsa.com/api/engagement"
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
SEED = re.compile(r"[0-9a-f]{32}\Z")
MAX_COUNT = 2**53 - 1


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("A publication/window date must be YYYY-MM-DD")
    return date.fromisoformat(value)


def _stamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("A photo edition timestamp must be timezone-aware") from exc
    if result.tzinfo is None:
        raise ValueError("A photo edition timestamp must be timezone-aware")
    return result


def _count(value):
    return type(value) is int and 0 <= value <= MAX_COUNT


def closed_window(publication_day):
    day = _date(publication_day)
    return {"start_date_jst": (day - timedelta(days=7)).isoformat(),
            "end_date_jst": (day - timedelta(days=1)).isoformat(),
            "timezone": "Asia/Tokyo", "unit": "page-view"}


def _get_json(url):
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "mihassei-photo-edition/1"})
    with urlopen(request, timeout=8) as response:
        data = response.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("PV response exceeds the bounded read size")
    return json.loads(data)


def _tracking(payload):
    if not isinstance(payload, dict):
        raise ValueError("PV response is not an object")
    tracking = payload.get("pageViewTracking", {})
    if (not isinstance(tracking, dict) or tracking.get("unit") != "page-view"
            or tracking.get("timezone") != "Asia/Tokyo"):
        raise ValueError("PV response has no page-view/JST tracking declaration")


def _window_counts(payload, window, keys):
    _tracking(payload)
    ranking = payload.get("topPhotoIssues", {})
    if not isinstance(ranking, dict):
        raise ValueError("PV response has no historical photo window")
    echo = ranking.get("window", {})
    if (not isinstance(echo, dict) or ranking.get("version") != 1 or echo.get("startDateJst") != window["start_date_jst"]
            or echo.get("endDateJst") != window["end_date_jst"]
            or echo.get("timezone") != "Asia/Tokyo" or echo.get("unit") != "page-view"
            or echo.get("complete") is not True or echo.get("measuredDays") != 7
            or ranking.get("truncated") is not False or not isinstance(ranking.get("issues"), list)):
        raise ValueError("PV response does not confirm the exact complete, untruncated seven-day window")
    observations = {}
    for row in ranking["issues"]:
        if not isinstance(row, dict):
            raise ValueError("PV response has an invalid issue observation")
        key, value = row.get("article"), row.get("pageViews")
        if (not isinstance(key, str) or not re.fullmatch(r"photo-issue:[a-z0-9]+(?:-[a-z0-9]+)*", key)
                or not _count(value) or value == 0 or key in observations):
            raise ValueError("PV response has an invalid or duplicate issue observation")
        observations[key] = value
    # A missing positive counter is zero only after complete coverage and a
    # non-truncated response have both been established above.
    return {key: observations.get(key, 0) for key in keys}


def _daily_counts(window, keys, fetcher):
    if len(keys) > 20:
        raise ValueError("The seven-read bootstrap supports at most twenty existing feature keys")
    first = _date(window["start_date_jst"])
    dates = [(first + timedelta(days=i)).isoformat() for i in range(7)]

    def read(day):
        url = API + "?" + urlencode({"day": day, "articles": ",".join(keys)})
        payload = fetcher(url)
        _tracking(payload)
        echo = payload.get("day", {})
        if (not isinstance(echo, dict) or echo.get("dateJst") != day
                or echo.get("pageViewsCoverage") != "complete"):
            raise ValueError("Daily PV response does not confirm complete coverage for " + day)
        articles = payload.get("articles", {})
        if not isinstance(articles, dict):
            raise ValueError("Daily PV response has no issue observations")
        counts = {key: articles.get(key, {}).get("requestedDayPageViews")
                  if isinstance(articles.get(key), dict) else None for key in keys}
        if any(not _count(value) for value in counts.values()):
            raise ValueError("Daily PV response has a missing/noninteger issue count for " + day)
        return {"date_jst": day, "issue_page_views": counts}

    # Seven GETs total, at most four concurrently, with eight-second network
    # timeouts. No visit, reaction, retry write, or new measurement is sent.
    with ThreadPoolExecutor(max_workers=4) as pool:
        daily = list(pool.map(read, dates))
    totals = {key: sum(day["issue_page_views"][key] for day in daily) for key in keys}
    if any(not _count(value) for value in totals.values()):
        raise ValueError("Daily PV sum exceeds the safe integer range")
    return totals, daily


def _ordered(models, seed, status, previous_order):
    rng = random.Random(int(seed, 16))
    ids = {row["model_id"] for row in models}
    if status == "fallback":
        order = [model_id for model_id in previous_order if model_id in ids]
        new = sorted(ids - set(order))
        rng.shuffle(new)
        return order + new
    order = []
    for value in sorted({row["page_views"] for row in models}, reverse=True):
        tied = sorted(row["model_id"] for row in models if row["page_views"] == value)
        if len(tied) > 1:
            rng.shuffle(tied)
        order.extend(tied)
    return order


def freeze_order(catalog, selected_models=None, publication_day=None, *, api_payload=None,
                 previous_order=(), seed=None, fetcher=None, daily_bootstrap=False, now=None):
    """Create one immutable proposal; never edit catalog or an earlier edition."""
    window = closed_window(publication_day)
    roster = [model["id"] for model in catalog["models"]]
    selected = list(roster if selected_models is None else selected_models)
    if (any(not isinstance(model_id, str) or not SLUG.fullmatch(model_id) for model_id in roster)
            or len(roster) != 7 or len(set(roster)) != 7 or len(selected) != 7 or set(selected) != set(roster)):
        raise ValueError("A joint photo edition includes all seven registered models exactly once")
    if len(set(previous_order)) != len(previous_order) or not set(previous_order) <= set(roster):
        raise ValueError("Previous contents must be a fixed, unique registered-model order")
    seed = secrets.token_hex(16) if seed is None else seed
    if not isinstance(seed, str) or not SEED.fullmatch(seed):
        raise ValueError("A shuffle seed must be a 32-character hexadecimal string")
    prior = {model_id: [] for model_id in selected}
    for feature in catalog["issues"]:
        if feature.get("state") != "published":
            continue
        if _stamp(feature["published_at"]).astimezone(JST).date() >= _date(publication_day):
            continue
        model_id = feature["model_id"]
        if model_id in prior:
            prior[model_id].append("photo-issue:" + feature["slug"])
    for keys in prior.values():
        keys.sort()
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate published feature identity")
    keys = sorted(key for values in prior.values() for key in values)
    reason, totals, daily = None, {}, []
    status, source_kind = "debut", "catalog-debut"
    if keys:
        try:
            if daily_bootstrap:
                totals, daily = _daily_counts(window, keys, fetcher or _get_json)
                source_kind = "daily-public-api"
            else:
                payload = api_payload if api_payload is not None else (fetcher or _get_json)(
                    API + "?" + urlencode({"view": "public", "photoPopular": "1", "photoEnd": window["end_date_jst"]}))
                totals = _window_counts(payload, window, keys)
                source_kind = "photo-window-api"
            status = "measured"
        except (OSError, ValueError, TypeError, KeyError) as exc:
            status, source_kind = "fallback", "unavailable"
            reason = str(exc)[:400] or type(exc).__name__
    models = [{"model_id": model_id, "feature_keys": prior[model_id],
               "page_views": (sum(totals[key] for key in prior[model_id]) if status == "measured" else None)
                             if prior[model_id] else 0,
               "status": ("measured" if status == "measured" else "unavailable")
                         if prior[model_id] else "debut"} for model_id in selected]
    if any(row["page_views"] is not None and not _count(row["page_views"]) for row in models):
        raise ValueError("Model PV total exceeds the safe integer range")
    snapshot = {"schema_version": 1, "publication_date_jst": publication_day, "window": window,
                "status": status, "reason": reason, "seed": seed,
                "retrieved_at_utc": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
                "source": {"kind": source_kind, "issue_page_views": totals, "daily": daily},
                "models": models, "previous_order": list(previous_order),
                "order": _ordered(models, seed, status, previous_order)}
    validate_order_snapshot(snapshot, selected)
    return snapshot


def validate_order_snapshot(snapshot, model_ids):
    if (not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1
            or snapshot.get("window") != closed_window(snapshot.get("publication_date_jst"))
            or snapshot.get("status") not in ("measured", "debut", "fallback")
            or not isinstance(snapshot.get("seed"), str) or not SEED.fullmatch(snapshot["seed"])
            or not isinstance(snapshot.get("models"), list) or len(snapshot["models"]) != 7):
        raise ValueError("Invalid frozen photo contents/PV window")
    _stamp(snapshot.get("retrieved_at_utc"))
    models, status = snapshot["models"], snapshot["status"]
    if (any(not isinstance(row, dict) for row in models)
            or {row.get("model_id") for row in models} != set(model_ids) or len(set(model_ids)) != 7):
        raise ValueError("Frozen contents must cover all seven models")
    previous = snapshot.get("previous_order")
    if not isinstance(previous, list) or len(set(previous)) != len(previous) or not set(previous) <= set(model_ids):
        raise ValueError("Invalid previous fixed contents")
    source = snapshot.get("source", {})
    expected_source = {"measured": {"photo-window-api", "daily-public-api"},
                       "debut": {"catalog-debut"}, "fallback": {"unavailable"}}
    if not isinstance(source, dict) or source.get("kind") not in expected_source[status]:
        raise ValueError("Frozen PV source does not match its availability status")
    counts = source.get("issue_page_views", {})
    if not isinstance(counts, dict) or any(not _count(value) for value in counts.values()):
        raise ValueError("Invalid frozen issue PV observations")
    for row in models:
        keys = row.get("feature_keys")
        if (not isinstance(keys, list) or len(set(keys)) != len(keys)
                or any(not isinstance(key, str) or not re.fullmatch(r"photo-issue:[a-z0-9]+(?:-[a-z0-9]+)*", key) for key in keys)):
            raise ValueError("Invalid frozen existing-feature keys")
        if not keys:
            if row.get("status") != "debut" or not _count(row.get("page_views")) or row["page_views"] != 0:
                raise ValueError("A debut records structural zero, never unavailable PV")
        elif status == "fallback":
            if row.get("status") != "unavailable" or row.get("page_views") is not None:
                raise ValueError("Unavailable existing PV must remain null")
        elif (status != "measured" or row.get("status") != "measured"
              or not _count(row.get("page_views")) or any(key not in counts for key in keys)
              or row["page_views"] != sum(counts[key] for key in keys)):
            raise ValueError("Frozen model PV must sum its actual feature observations")
    if status == "fallback" and (not isinstance(snapshot.get("reason"), str) or not snapshot["reason"].strip()):
        raise ValueError("Fallback contents need their actual unavailable reason")
    expected_keys = {key for row in models for key in row["feature_keys"]}
    if (status == "measured" and set(counts) != expected_keys) or (status != "measured" and counts):
        raise ValueError("Frozen observations must cover exactly the measured feature keys")
    if source["kind"] == "daily-public-api":
        daily = source.get("daily")
        first = _date(snapshot["window"]["start_date_jst"])
        dates = [(first + timedelta(days=i)).isoformat() for i in range(7)]
        if (not isinstance(daily, list) or len(daily) != 7
                or any(not isinstance(day, dict) or day.get("date_jst") != stamp
                       or not isinstance(day.get("issue_page_views"), dict)
                       or set(day["issue_page_views"]) != expected_keys
                       or any(not _count(value) for value in day["issue_page_views"].values())
                       for day, stamp in zip(daily, dates))
                or any(sum(day["issue_page_views"][key] for day in daily) != counts[key] for key in expected_keys)):
            raise ValueError("Frozen daily observations must match all seven dates and the adopted PV totals")
    if snapshot.get("order") != _ordered(models, snapshot["seed"], status, previous):
        raise ValueError("Frozen order is inconsistent with PVs, ties or the previous fixed order")
    return snapshot


def edition_url(edition):
    return "/photo/weekly/" + edition["slug"] + "/"


def load_photo_editions(root=ROOT, *, catalog, published, now=None):
    path = Path(root) / "content/photo_editions.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("editions"), list):
        raise ValueError("Invalid photo editions catalog")
    roster = [model["id"] for model in catalog["models"]]
    features = {feature["slug"]: feature for feature in catalog["issues"]}
    clock = now or datetime.now(timezone.utc)
    released = {feature["slug"]: feature for feature in published if feature.get("state") == "published"
                and _stamp(feature["published_at"]) <= clock}
    result, numbers, slugs, claimed_features = [], set(), set(), {}
    for edition in data["editions"]:
        number, slug = edition.get("number"), edition.get("slug")
        legacy, state = edition.get("legacy"), edition.get("state")
        if (type(number) is not int or number < 1 or number in numbers or not isinstance(slug, str)
                or not SLUG.fullmatch(slug) or slug in slugs or type(legacy) is not bool
                or state not in ("draft", "published") or (not legacy and slug != f"issue-{number:03d}")):
            raise ValueError("Invalid photo edition identity/state")
        for field in ("title", "summary"):
            value = edition.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > 500:
                raise ValueError("Photo edition needs reader title/summary")
        members = edition.get("features")
        if (not isinstance(members, list) or len(members) != (1 if legacy else 7)
                or any(not isinstance(member, str) or member not in features for member in members)
                or len(set(members)) != len(members)):
            raise ValueError("A joint photo edition needs seven distinct known features")
        ordered = [features[member] for member in members]
        if legacy:
            if number != 1 or slug != members[0] or edition.get("ranking") is not None:
                raise ValueError("Legacy photo edition must retain its original feature URL")
        else:
            ranking = validate_order_snapshot(edition.get("ranking"), roster)
            if [feature["model_id"] for feature in ordered] != ranking["order"]:
                raise ValueError("Photo edition features do not match its frozen model order")
            for row in ranking["models"]:
                expected_keys = sorted("photo-issue:" + feature["slug"] for feature in features.values()
                                       if feature["model_id"] == row["model_id"] and feature.get("state") == "published"
                                       and _stamp(feature["published_at"]).astimezone(JST).date()
                                       < _date(ranking["publication_date_jst"]))
                if row["feature_keys"] != expected_keys:
                    raise ValueError("Frozen PV evidence omits or invents an earlier published feature")
                for key in row["feature_keys"]:
                    prior = features.get(key.removeprefix("photo-issue:"))
                    if (not prior or prior["model_id"] != row["model_id"] or prior.get("state") != "published"
                            or _stamp(prior["published_at"]).astimezone(JST).date() >= _date(ranking["publication_date_jst"])):
                        raise ValueError("Frozen PV evidence is not an earlier published feature of this model")
        if state == "published":
            stamp = _stamp(edition.get("published_at"))
            if stamp > clock or any(member not in released for member in members):
                raise ValueError("Photo edition has unpublished features or a future publication")
            if not legacy and stamp.astimezone(JST).date().isoformat() != edition["ranking"]["publication_date_jst"]:
                raise ValueError("Photo edition actual publication date differs from its frozen PV window")
            for member in members:
                if member in claimed_features:
                    raise ValueError("A published feature cannot belong to two photo editions: " + member)
                claimed_features[member] = slug
            result.append({**edition, "ordered_features": [released[member] for member in members]})
        numbers.add(number)
        slugs.add(slug)
    return sorted(result, key=lambda edition: edition["number"], reverse=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Freeze one photo edition order using read-only PV observations")
    parser.add_argument("--catalog", type=Path, default=ROOT / "content/photo_issues.json")
    parser.add_argument("--publication-date", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--seed")
    parser.add_argument("--daily-bootstrap", action="store_true")
    args = parser.parse_args(argv)
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    roster = [model["id"] for model in catalog["models"]]
    if args.output.is_symlink():
        raise ValueError("A frozen order output cannot be a symlink")
    if args.output.exists():
        snapshot = validate_order_snapshot(json.loads(args.output.read_text(encoding="utf-8")), roster)
        if snapshot["publication_date_jst"] != args.publication_date:
            raise ValueError("Existing frozen order belongs to another publication date")
    else:
        previous = json.loads(args.previous.read_text(encoding="utf-8"))["order"] if args.previous else []
        snapshot = freeze_order(catalog, publication_day=args.publication_date, previous_order=previous,
                                seed=args.seed, daily_bootstrap=args.daily_bootstrap)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "status": snapshot["status"],
                      "order": snapshot["order"], "window": snapshot["window"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
