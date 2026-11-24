/* Small, read-only creator input from immutable catalog/story JSON.
 * Load with new Function(source + '\nreturn CastStoryContext;')().
 * This selects recorded metadata; it never verifies JPEG bytes or approves a post.
 */
var CastStoryContext = (function () {
  "use strict";
  const SHA = /^[0-9a-f]{64}$/, GIT = /^[0-9a-f]{40}$/, SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  const CUT = /^(?:0[1-9]|10)$/, JST = 9 * 60 * 60 * 1000;
  const PERSONA = ["occupation", "casting_specialty", "first_person", "personality", "voice", "likes", "weakness"];

  function object(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }
  function text(value, limit, label) {
    if (typeof value !== "string" || !value.trim() || value.length > limit)
      throw Error("Invalid " + label);
    return value;
  }
  function stamp(value) {
    const m = typeof value === "string" && value.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(Z|[+-](\d{2}):(\d{2}))$/);
    if (!m) throw Error("A valid timezone-aware timestamp is required");
    const y = Number(m[1]), month = Number(m[2]), day = Number(m[3]);
    const leap = y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
    const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    if (y < 1 || month < 1 || month > 12 || day < 1 || day > days[month - 1]
        || Number(m[4]) > 23 || Number(m[5]) > 59 || Number(m[6]) > 59
        || (m[7] !== "Z" && (Number(m[8]) > 23 || Number(m[9]) > 59)))
      throw Error("Invalid calendar timestamp");
    const ms = Date.parse(value);
    if (!Number.isFinite(ms)) throw Error("Invalid timestamp");
    return ms;
  }
  function jstDay(ms) { return new Date(ms + JST).toISOString().slice(0, 10); }
  function compare(a, b) { return a < b ? -1 : a > b ? 1 : 0; }
  function character(model) {
    const input = model.character, result = {};
    if (!object(input)) throw Error("Missing creator character");
    for (const key of PERSONA) {
      const value = input[key];
      if (key === "likes" || key === "personality" && Array.isArray(value)) {
        if (!Array.isArray(value) || !value.length || value.length > 20)
          throw Error("Invalid creator " + key);
        result[key] = value.map(item => text(item, 1000, "creator " + key));
      } else result[key] = text(value, 1500, "creator " + key);
    }
    return result;
  }

  function indexStories(stories, modelId, clock) {
    if (!object(stories) || stories.schema_version !== 1 || !Array.isArray(stories.stories)
        || !SLUG.test(modelId || "")) throw Error("Invalid creator context inputs");
    const day = jstDay(clock), records = [], ids = new Set(), todayIds = [];
    let futureToday = 0;
    for (const story of stories.stories) {
      if (!object(story)) throw Error("Invalid story record");
      if (story.model_id !== modelId) continue;
      const time = stamp(story.published_at);
      const id = text(story.id, 100, "creator story ID");
      if (!SLUG.test(id) || ids.has(id)) throw Error("Invalid or duplicate creator story ID");
      ids.add(id);
      if (time <= clock) records.push({ story, time });
      if (jstDay(time) !== day) continue;
      if (time <= clock) todayIds.push(id);
      else futureToday++;
    }
    return { records, daily: { jst_day: day, today_has_record: todayIds.length + futureToday > 0,
      today_story_ids: todayIds.sort(), future_today_record_count: futureToday } };
  }

  function build({ catalog, stories, modelId, now } = {}) {
    const clock = stamp(now);
    return creatorContext(catalog, indexStories(stories, modelId, clock), modelId, now, clock);
  }

  function photoLocation({ issue_slug, cut_id, image_sha256 }) {
    if (typeof issue_slug !== "string" || !SLUG.test(issue_slug)
        || typeof cut_id !== "string" || !CUT.test(cut_id)
        || typeof image_sha256 !== "string" || !SHA.test(image_sha256))
      throw Error("Invalid creator photo reference");
    return {
      path: "assets/generated/photo-issue-" + issue_slug + "-" + cut_id + ".jpg",
      url: "/photo/weekly/" + issue_slug + "/" + cut_id + ".jpg?v=" + image_sha256.slice(0, 12)
    };
  }

  function creatorContext(catalog, index, modelId, now, clock) {
    if (!object(catalog) || catalog.schema_version !== 1 || !Array.isArray(catalog.models)
        || !Array.isArray(catalog.issues)) throw Error("Invalid creator context inputs");
    const matches = catalog.models.filter(model => object(model) && model.id === modelId);
    const model = matches[0];
    if (matches.length !== 1 || model.fictional !== true || !Number.isInteger(model.age)
        || model.age < 20 || model.age > 99) throw Error("Unknown fictional adult creator");
    const persona = { id: modelId, name: text(model.name, 100, "creator name"),
      age: model.age, fictional: true, character: character(model) };

    const candidates = [], bindings = new Map(), issueIds = new Set();
    for (const issue of catalog.issues) {
      if (!object(issue) || issue.model_id !== modelId || issue.state !== "published") continue;
      const published = stamp(issue.published_at);
      if (published > clock) continue;
      if (!SLUG.test(issue.slug || "") || issueIds.has(issue.slug))
        throw Error("Invalid or duplicate creator issue");
      issueIds.add(issue.slug);
      const snapshot = issue.model_snapshot;
      if (!SHA.test(issue.issue_sha256 || "") || snapshot?.id !== modelId
          || snapshot.fictional !== true || !Number.isInteger(snapshot.age) || snapshot.age < 20 || snapshot.age > 99
          || !SHA.test(snapshot.master?.sha256 || "") || !Array.isArray(issue.shots) || !object(issue.images)
          || issue.generation_holds !== undefined && (!object(issue.generation_holds) || Object.keys(issue.generation_holds).length)) continue;
      const setting = issue.setting;
      if (!object(setting) || !Number.isInteger(setting.year) || setting.year < 2031
          || !Number.isInteger(setting.month) || setting.month < 1 || setting.month > 12) continue;
      const cuts = new Set();
      for (const shot of issue.shots) {
        if (!object(shot) || !CUT.test(shot.id || "") || cuts.has(shot.id))
          throw Error("Invalid or duplicate creator cut");
        cuts.add(shot.id);
        const image = issue.images[shot.id], review = image?.review;
        if (!object(image) || !object(review) || review.decision !== "accepted"
            || !SHA.test(image.sha256 || "") || review.issue_sha256 !== issue.issue_sha256
            || review.image_sha256 !== image.sha256 || review.cut_id !== shot.id
            || review.master_sha256 !== snapshot.master.sha256) continue;
        const source = { issue_slug: issue.slug, cut_id: shot.id, image_sha256: image.sha256 };
        const location = photoLocation(source);
        if (image.path !== location.path) continue;
        const reviewed = stamp(review.reviewed_at);
        if (reviewed > published || reviewed > clock) continue;
        const photo = { ...source, issue_sha256: issue.issue_sha256, ...location,
          alt: text(shot.alt, 300, "creator photo alt"),
          setting: { year: setting.year, month: setting.month } };
        candidates.push({ photo, published });
        bindings.set(issue.slug + "/" + shot.id + "/" + image.sha256, photo);
      }
    }

    const own = [], used = new Map();
    let excludedStoryCount = 0;
    for (const { story, time } of index.records) {
      const source = story.source;
      const photo = object(source) && bindings.get(source.issue_slug + "/" + source.cut_id + "/" + source.image_sha256);
      if (!photo || story.setting?.year !== photo.setting.year || story.setting?.month !== photo.setting.month) {
        excludedStoryCount++;
        continue;
      }
      const body = text(story.text, 180, "creator story text");
      used.set(source.image_sha256, Math.max(used.get(source.image_sha256) ?? -Infinity, time));
      own.push({ time, story: { id: story.id, published_at: story.published_at, text: body,
        source: { issue_slug: source.issue_slug, cut_id: source.cut_id, image_sha256: source.image_sha256 } } });
    }
    own.sort((a, b) => b.time - a.time || compare(a.story.id, b.story.id));
    candidates.sort((a, b) => {
      const aUsed = used.get(a.photo.image_sha256), bUsed = used.get(b.photo.image_sha256);
      if (aUsed !== bUsed) {
        if (aUsed === undefined) return -1;
        if (bUsed === undefined) return 1;
        return aUsed - bUsed;
      }
      return b.published - a.published || compare(a.photo.issue_slug, b.photo.issue_slug)
        || compare(a.photo.cut_id, b.photo.cut_id);
    });
    const distinct = [], hashes = new Set();
    for (const { photo } of candidates) if (!hashes.has(photo.image_sha256)) {
      hashes.add(photo.image_sha256);
      distinct.push(photo);
    }
    return { schema_version: 1, verification: "metadata_only", image_bytes_verified: false,
      open_prs: "unknown", as_of: now, ...index.daily, model: persona,
      recent_stories: own.slice(0, 3).map(row => row.story),
      excluded_story_record_count: excludedStoryCount, photo_candidate_count: distinct.length,
      photos: distinct.slice(0, 5) };
  }
  function filePayload(reply, path) {
    let data = reply;
    for (let depth = 0; depth < 4; depth++) {
      if (!object(data) || data.isError || data.error || data.error_code || data.truncated || data.content_truncated)
        throw Error("GitHub fetch_file failed: " + path);
      if (data.structuredContent !== undefined) { data = data.structuredContent; continue; }
      if (data.result !== undefined) { data = data.result; continue; }
      if (data.encoding !== "utf-8" || typeof data.content !== "string" || !GIT.test(data.sha || "")
          || data.path !== undefined && data.path !== path)
        throw Error("Expected a complete UTF-8 GitHub file and blob SHA: " + path);
      return data;
    }
    throw Error("Unrecognized GitHub fetch_file response: " + path);
  }
  async function read({ fetchFile, ref, modelId, now, skipIfRecorded = false, compactPhotos = false } = {}) {
    const clock = stamp(now);
    if (typeof fetchFile !== "function" || !GIT.test(ref || "") || !SLUG.test(modelId || "")
        || typeof skipIfRecorded !== "boolean" || typeof compactPhotos !== "boolean")
      throw Error("Provide fetchFile, a full immutable commit SHA, and a creator ID");
    const repository = "RollNuts/mihassei-news";
    const storyPath = "content/photo_stories/" + modelId + ".json";
    const catalogPath = "content/photo_issues.json", historyPath = "content/photo_stories.json";
    const source = { repository, ref, story_path: storyPath, blob_shas: {} };
    async function load(path) {
      let reply;
      try { reply = await fetchFile({ repository_full_name: repository, path, ref, encoding: "utf-8" }); }
      catch (_) { throw Error("GitHub fetch_file failed: " + path); }
      const file = filePayload(reply, path);
      source.blob_shas[path] = file.sha;
      return JSON.parse(file.content);
    }
    const [catalog, history, stream] = await Promise.all([
      skipIfRecorded ? undefined : load(catalogPath), load(historyPath), load(storyPath)
    ]);
    for (const doc of [history, stream]) {
      if (!object(doc) || doc.schema_version !== 1 || !Array.isArray(doc.stories))
        throw Error("Invalid creator story catalog");
    }
    if (stream.stories.some(row => !object(row) || row.model_id !== modelId))
      throw Error("Creator story file contains another model");
    const stories = { schema_version: 1, stories: history.stories.concat(stream.stories) };
    const index = indexStories(stories, modelId, clock);
    if (skipIfRecorded && index.daily.today_has_record) {
      return { schema_version: 1, verification: "story_records_only", image_bytes_verified: false,
        open_prs: "unknown", as_of: now, model_id: modelId, ...index.daily, source };
    }
    const result = creatorContext(skipIfRecorded ? await load(catalogPath) : catalog, index, modelId, now, clock);
    if (compactPhotos) {
      result.photos = result.photos.map(({ issue_slug, cut_id, image_sha256, alt, setting }) =>
        ({ issue_slug, cut_id, image_sha256, alt, setting }));
    }
    return { ...result, source };
  }
  return Object.freeze({ build, read, photoLocation });
})();
if (typeof module !== "undefined" && module.exports) module.exports = CastStoryContext;
