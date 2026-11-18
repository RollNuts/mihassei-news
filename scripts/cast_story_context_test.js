"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const context = require("./cast_story_context.js");

const NOW = "2026-11-18T09:10:00+09:00";
const SHA = n => n.toString(16).padStart(64, "0");
const persona = { occupation: "取材担当", casting_specialty: "体験取材", first_person: "私",
  personality: "マイペース", voice: "短く柔らかく", likes: ["水辺"], weakness: "寄り道する" };
function fixture() {
  const id = "nagino-saya", slug = "november-saya-test";
  const issue = { slug, model_id: id, issue_sha256: SHA(100), state: "published",
    published_at: "2026-11-17T12:00:00+09:00", setting: { year: 2031, month: 11 },
    model_snapshot: { id, age: 27, fictional: true, master: { sha256: SHA(99) } }, shots: [], images: {} };
  for (let n = 1; n <= 10; n++) {
    const cut = String(n).padStart(2, "0");
    issue.shots.push({ id: cut, alt: "水辺で立ち止まる本人。" });
    issue.images[cut] = { path: "assets/generated/photo-issue-" + slug + "-" + cut + ".jpg",
      sha256: SHA(n), review: { decision: "accepted", issue_sha256: issue.issue_sha256,
        image_sha256: SHA(n), master_sha256: SHA(99), cut_id: cut, reviewed_at: "2026-11-17T11:00:00+09:00" } };
  }
  const catalog = { schema_version: 1,
    models: [{ id, name: "凪野さや", age: 27, fictional: true, character: structuredClone(persona),
      body_profile: { private_numbers: "must not be copied" }, appearance: "not needed for a daily post" }], issues: [issue] };
  return { catalog, stories: { schema_version: 1, stories: [] }, modelId: id, now: NOW };
}
function story(input, n, date, cut = "01") {
  const issue = input.catalog.issues[0];
  return { id: input.modelId + "-post-" + n, model_id: input.modelId, published_at: date,
    setting: { year: 2031, month: 11 }, text: "寄り道のメモ、その" + n + "。",
    source: { issue_slug: issue.slug, cut_id: cut, image_sha256: issue.images[cut].sha256 } };
}

test("CommonJS and a plain V8 new Function load the same pure helper", () => {
  const input = fixture(), before = JSON.stringify(input);
  const source = fs.readFileSync(path.join(__dirname, "cast_story_context.js"), "utf8");
  const plain = new Function(source + "\nreturn CastStoryContext;")();
  const report = context.build(input);
  assert.deepEqual(plain.build(input), report);
  assert.equal(JSON.stringify(input), before);
  assert.equal(report.verification, "metadata_only");
  assert.equal(report.image_bytes_verified, false);
  assert.equal(report.open_prs, "unknown");
  assert.equal(JSON.stringify(report).includes("private_numbers"), false);
  assert.equal(JSON.stringify(report).includes("appearance"), false);
});

test("returns only three current own posts and actual JST-day duplicate IDs", () => {
  const input = fixture();
  input.stories.stories = [story(input, 1, "2026-11-17T13:00:00+09:00"),
    story(input, 2, "2026-11-17T15:30:00Z", "02"),
    story(input, 3, "2026-11-18T07:16:59.000001+09:00", "03"),
    story(input, 4, "2026-11-18T08:00:00+09:00", "04")];
  const report = context.build(input);
  assert.equal(report.jst_day, "2026-11-18");
  assert.equal(report.today_has_record, true);
  assert.deepEqual(report.today_story_ids, input.stories.stories.slice(1).map(row => row.id));
  assert.deepEqual(report.recent_stories.map(row => row.id), input.stories.stories.slice(1).reverse().map(row => row.id));
  assert.equal(report.photos.length, 5);
  assert.equal(report.photos[0].cut_id, "05", "unused photographs come before recently posted ones");
  assert.deepEqual(context.build(input), report, "selection is deterministic");
});

test("unpublished/future or another person's photos never appear", () => {
  const input = fixture(), own = input.catalog.issues[0];
  const foreign = structuredClone(own), future = structuredClone(own), draft = structuredClone(own);
  foreign.slug = "foreign-issue"; foreign.model_id = "shioba-rio";
  future.slug = "future-issue"; future.published_at = "2026-11-19T00:00:00Z";
  draft.slug = "draft-issue"; draft.state = "draft";
  input.catalog.issues.push(foreign, future, draft);
  const report = context.build(input);
  assert.equal(report.photo_candidate_count, 10);
  assert(report.photos.every(row => row.issue_slug === own.slug));
  own.model_snapshot.id = "shioba-rio";
  assert.equal(context.build(input).photos.length, 0);
});

test("future posts disclose no text/ref but block a second record for the same JST day", () => {
  const input = fixture();
  input.stories.stories.push(story(input, 1, "2026-11-18T20:00:00+09:00"));
  const report = context.build(input);
  assert.equal(report.today_has_record, true);
  assert.equal(report.future_today_record_count, 1);
  assert.deepEqual(report.today_story_ids, []);
  assert.deepEqual(report.recent_stories, []);
});

test("stale review bindings and noncanonical source paths cannot become photo choices", () => {
  const input = fixture(), images = input.catalog.issues[0].images;
  images["01"].review.image_sha256 = SHA(88);
  images["02"].review.issue_sha256 = SHA(89);
  images["03"].review.cut_id = "04";
  images["04"].path = "assets/generated/someone-else.jpg";
  images["05"].review.decision = "rejected";
  images["06"].review.reviewed_at = "2026-11-18T10:00:00+09:00";
  images["07"].review.master_sha256 = SHA(800);
  assert.deepEqual(context.build(input).photos.map(row => row.cut_id), ["08", "09", "10"]);
});

test("stale source text/ref is omitted while its day's recorded duplicate remains visible", () => {
  const input = fixture(), old = story(input, 1, "2026-11-18T07:30:00+09:00");
  old.source.image_sha256 = SHA(500);
  input.stories.stories.push(old);
  const report = context.build(input);
  assert.deepEqual(report.recent_stories, []);
  assert.equal(report.excluded_story_record_count, 1);
  assert.deepEqual(report.today_story_ids, [old.id]);
});

test("unknown/nonadult creators and duplicate identities fail closed", () => {
  const input = fixture();
  assert.throws(() => context.build({ ...input, modelId: "unknown" }), /Unknown/);
  input.catalog.models[0].age = 19;
  assert.throws(() => context.build(input), /adult/);
  input.catalog.models[0].age = 27;
  input.catalog.models.push(structuredClone(input.catalog.models[0]));
  assert.throws(() => context.build(input), /adult/);
});

test("timestamps reject missing timezone, nonexistent calendar dates and invalid offsets", () => {
  for (const now of [undefined, "2026-11-18T09:00:00", "2026-02-30T00:00:00Z",
    "2026-11-18T24:00:00Z", "2026-11-18T00:00:00+25:00", "2026-11-18T00:00:00+09:70"])
    assert.throws(() => context.build({ ...fixture(), now }), /timestamp/);
  const input = fixture();
  input.catalog.issues[0].published_at = "invalid";
  assert.throws(() => context.build(input), /timestamp/);
  const next = fixture();
  next.stories.stories.push(story(next, 1, "2026-02-30T12:00:00Z"));
  assert.throws(() => context.build(next), /timestamp/);
  const badReview = fixture();
  badReview.catalog.issues[0].images["01"].review.reviewed_at = "not-a-date";
  assert.throws(() => context.build(badReview), /timestamp/);
});

test("same source bytes are offered once, and all accepted photo references stay exact", () => {
  const input = fixture(), issue = input.catalog.issues[0], photos = context.build(input).photos;
  const second = structuredClone(issue);
  second.slug = "another-saya-issue";
  for (const cut of Object.keys(second.images))
    second.images[cut].path = "assets/generated/photo-issue-" + second.slug + "-" + cut + ".jpg";
  input.catalog.issues.push(second);
  assert.equal(context.build(input).photo_candidate_count, 10);
  for (const photo of photos) {
    assert.equal(photo.path, issue.images[photo.cut_id].path);
    assert.equal(photo.image_sha256, issue.images[photo.cut_id].sha256);
    assert.equal(photo.issue_sha256, issue.issue_sha256);
    assert.equal(photo.alt, issue.shots.find(row => row.id === photo.cut_id).alt);
    assert.equal(photo.url, "/photo/weekly/" + issue.slug + "/" + photo.cut_id + ".jpg?v=" + photo.image_sha256.slice(0, 12));
  }
});

test("malformed schemas, duplicate cuts and duplicate own story IDs fail closed", () => {
  const input = fixture();
  assert.throws(() => context.build({ ...input, stories: [] }), /inputs/);
  input.catalog.issues[0].shots.push(input.catalog.issues[0].shots[0]);
  assert.throws(() => context.build(input), /cut/);
  const next = fixture(), post = story(next, 1, "2026-11-18T07:30:00+09:00");
  next.stories.stories.push(post, structuredClone(post));
  assert.throws(() => context.build(next), /story ID/);
});

test("real catalogs produce only each adult's small whitelisted context", () => {
  const root = path.join(__dirname, ".."), catalog = JSON.parse(fs.readFileSync(path.join(root, "content/photo_issues.json"), "utf8"));
  const stories = JSON.parse(fs.readFileSync(path.join(root, "content/photo_stories.json"), "utf8"));
  for (const model of catalog.models) {
    const report = context.build({ catalog, stories, modelId: model.id, now: NOW });
    assert.equal(report.model.id, model.id);
    assert.equal(report.photos.length, 5);
    assert(report.recent_stories.length <= 3);
    for (const photo of report.photos) {
      const issue = catalog.issues.find(row => row.slug === photo.issue_slug);
      assert.equal(issue.model_id, model.id);
      assert.equal(issue.images[photo.cut_id].sha256, photo.image_sha256);
    }
  }
});

const REF = "e1ba82ef867f547039c766e20ed8c2c6c50e21d1";
function reader(input, wrap = value => value) {
  const calls = [], blobs = { "content/photo_issues.json": "a".repeat(40), "content/photo_stories.json": "b".repeat(40) };
  return { calls, blobs, fetchFile: async args => {
    calls.push(args);
    return wrap({ content: JSON.stringify(args.path.endsWith("photo_issues.json") ? input.catalog : input.stories),
      encoding: "utf-8", sha: blobs[args.path], display_title: path.basename(args.path) });
  } };
}

test("read uses exactly two fixed-repository UTF-8 fetches at one immutable ref", async () => {
  const input = fixture();
  for (const wrap of [value => value, value => ({ result: value }),
    value => ({ content: [{ type: "text", text: "Action completed." }], structuredContent: value, isError: false })]) {
    const transport = reader(input, wrap);
    const result = await context.read({ fetchFile: transport.fetchFile, ref: REF, modelId: input.modelId, now: NOW });
    assert.deepEqual(result.source, { repository: "RollNuts/mihassei-news", ref: REF, blob_shas: transport.blobs });
    assert.deepEqual(result.photos, context.build(input).photos);
    assert.equal(transport.calls.length, 2);
    assert(transport.calls.every(args => args.repository_full_name === "RollNuts/mihassei-news"
      && args.ref === REF && args.encoding === "utf-8" && Object.keys(args).length === 4));
    assert.deepEqual(transport.calls.map(args => args.path), ["content/photo_issues.json", "content/photo_stories.json"]);
  }
});

test("read fails closed for provider errors, unsupported encodings and incomplete metadata", async () => {
  const input = fixture();
  for (const wrap of [value => ({ isError: true, structuredContent: value }),
    value => ({ error_code: "PERMISSION_DENIED", result: value }),
    value => ({ result: { isError: true, structuredContent: value } }),
    value => ({ ...value, encoding: "base64" }), value => ({ ...value, encoding: "unknown" }),
    value => ({ ...value, sha: "missing" }), value => ({ ...value, path: "another-file.json" }),
    value => ({ ...value, content: "{broken" })]) {
    const transport = reader(input, wrap);
    await assert.rejects(context.read({ fetchFile: transport.fetchFile, ref: REF, modelId: input.modelId, now: NOW }));
    assert.equal(transport.calls.length, 2);
  }
  await assert.rejects(context.read({ fetchFile: async () => { throw Error("unavailable"); },
    ref: REF, modelId: input.modelId, now: NOW }), /fetch_file failed/);
});

test("read rejects a moving ref or invalid clock before contacting GitHub", async () => {
  const input = fixture(), transport = reader(input);
  await assert.rejects(context.read({ fetchFile: transport.fetchFile, ref: "main", modelId: input.modelId, now: NOW }), /immutable/);
  await assert.rejects(context.read({ fetchFile: transport.fetchFile, ref: REF, modelId: input.modelId, now: "2026-02-30T00:00:00Z" }), /timestamp/);
  assert.equal(transport.calls.length, 0);
});

test("read works in a plain new Function runtime without Node imports", async () => {
  const input = fixture(), transport = reader(input);
  const plain = new Function(fs.readFileSync(path.join(__dirname, "cast_story_context.js"), "utf8") + "\nreturn CastStoryContext;")();
  const report = await plain.read({ fetchFile: transport.fetchFile, ref: REF, modelId: input.modelId, now: NOW });
  assert.equal(report.model.id, input.modelId);
  assert.equal(report.source.ref, REF);
});
