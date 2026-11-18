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

  function build({ catalog, stories, modelId, now } = {}) {
    const clock = stamp(now), day = jstDay(clock);
    if (!object(catalog) || catalog.schema_version !== 1 || !Array.isArray(catalog.models)
        || !Array.isArray(catalog.issues) || !object(stories) || stories.schema_version !== 1
        || !Array.isArray(stories.stories) || !SLUG.test(modelId || ""))
      throw Error("Invalid creator context inputs");
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
            || review.master_sha256 !== snapshot.master.sha256
            || image.path !== "assets/generated/photo-issue-" + issue.slug + "-" + shot.id + ".jpg") continue;
        const reviewed = stamp(review.reviewed_at);
        if (reviewed > published || reviewed > clock) continue;
        const photo = { issue_slug: issue.slug, cut_id: shot.id, image_sha256: image.sha256,
          issue_sha256: issue.issue_sha256, path: image.path,
          url: "/photo/weekly/" + issue.slug + "/" + shot.id + ".jpg?v=" + image.sha256.slice(0, 12),
          alt: text(shot.alt, 300, "creator photo alt"),
          setting: { year: setting.year, month: setting.month } };
        candidates.push({ photo, published });
        bindings.set(issue.slug + "/" + shot.id + "/" + image.sha256, photo);
      }
    }

    const own = [], todayIds = [], used = new Map(), storyIds = new Set();
    let todayHasRecord = false, futureTodayCount = 0, excludedStoryCount = 0;
    for (const story of stories.stories) {
      if (!object(story)) throw Error("Invalid story record");
      if (story.model_id !== modelId) continue;
      const time = stamp(story.published_at);
      const id = text(story.id, 100, "creator story ID");
      if (!SLUG.test(id) || storyIds.has(id)) throw Error("Invalid or duplicate creator story ID");
      storyIds.add(id);
      if (jstDay(time) === day) {
        todayHasRecord = true;
        if (time <= clock) todayIds.push(id);
        else futureTodayCount++;
      }
      if (time > clock) continue;
      const source = story.source;
      const photo = object(source) && bindings.get(source.issue_slug + "/" + source.cut_id + "/" + source.image_sha256);
      if (!photo || story.setting?.year !== photo.setting.year || story.setting?.month !== photo.setting.month) {
        excludedStoryCount++;
        continue;
      }
      const body = text(story.text, 180, "creator story text");
      used.set(source.image_sha256, Math.max(used.get(source.image_sha256) ?? -Infinity, time));
      own.push({ time, story: { id, published_at: story.published_at, text: body,
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
      open_prs: "unknown", as_of: now, jst_day: day, model: persona,
      today_has_record: todayHasRecord, today_story_ids: todayIds.sort(),
      future_today_record_count: futureTodayCount, recent_stories: own.slice(0, 3).map(row => row.story),
      excluded_story_record_count: excludedStoryCount, photo_candidate_count: distinct.length,
      photos: distinct.slice(0, 5) };
  }
  function filePayload(reply, path) {
    let data = reply;
    for (let depth = 0; depth < 4; depth++) {
      if (!object(data) || data.isError || data.error || data.error_code)
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
  async function read({ fetchFile, ref, modelId, now } = {}) {
    // Never read a moving branch twice and silently mix its two revisions.
    stamp(now);
    if (typeof fetchFile !== "function" || !GIT.test(ref || "") || !SLUG.test(modelId || ""))
      throw Error("Provide fetchFile, a full immutable commit SHA, and a creator ID");
    const repository = "RollNuts/mihassei-news";
    const paths = ["content/photo_issues.json", "content/photo_stories.json"];
    const replies = await Promise.allSettled(paths.map(path => Promise.resolve().then(() => fetchFile({
      repository_full_name: repository, path, ref, encoding: "utf-8"
    }))));
    const files = replies.map((reply, index) => {
      if (reply.status !== "fulfilled") throw Error("GitHub fetch_file failed: " + paths[index]);
      return filePayload(reply.value, paths[index]);
    });
    const result = build({ catalog: JSON.parse(files[0].content), stories: JSON.parse(files[1].content), modelId, now });
    return { ...result, source: { repository, ref,
      blob_shas: { [paths[0]]: files[0].sha, [paths[1]]: files[1].sha } } };
  }
  return Object.freeze({ build, read });
})();
if (typeof module !== "undefined" && module.exports) module.exports = CastStoryContext;
