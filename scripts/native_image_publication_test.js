"use strict";
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

// Production helpers execute in plain V8. Only this test's explicit byte-runtime
// adapter uses Node. Synthetic buffers/unsigned receipt fixtures are not photos
// or evidence of an ImageGen run; actual PNG conversion has its existing suite.
const stage = new Function(fs.readFileSync(path.join(__dirname, "native_image_stage.js"), "utf8")
  + "\nreturn NativeImageStage;")();
const patch = new Function(fs.readFileSync(path.join(__dirname, "generated_manifest_patch.js"), "utf8")
  + "\nreturn GeneratedManifestPatch;")();
const hash = bytes => crypto.createHash("sha256").update(bytes).digest("hex");
const gitBlob = bytes => crypto.createHash("sha1").update("blob " + bytes.length + "\0").update(bytes).digest("hex");
const clone = value => JSON.parse(JSON.stringify(value));
const PROTECTED = ["content/articles.json", "assets/generated/manifest.json", "content/visual_reviews.json"];
const articleHash = article => hash(JSON.stringify(Object.fromEntries(
  ["category", "paragraphs", "scene_prompt", "slug", "summary", "title"].map(key => [key, article[key] ?? ""]))));

function fixture(options = {}) {
  const repository = "fixture-owner/news-publication", slug = "new-native-photo";
  const baseCommit = "1".repeat(40), baseTree = "2".repeat(40), candidateCommit = "3".repeat(40);
  const head = options.sameHeadAsPlan ? baseCommit : "4".repeat(40), treeSha = options.sameHeadAsPlan ? baseTree : "5".repeat(40);
  const proofCommit = "6".repeat(40), claimCommit = "7".repeat(40), prefix = "maintenance/native-image-candidates/" + slug;
  const article = {slug, category: "社会", title: "Synthetic publication fixture", summary: "An explicit reviewed scene.",
    paragraphs: ["A synthetic fixture for source-to-publication binding."], scene_prompt: "An unsigned synthetic fixture; no generation."};
  const articleSha256 = articleHash(article);
  const baseRaw = {
    [PROTECTED[0]]: JSON.stringify([article]),
    [PROTECTED[1]]: '{"version":1,"images":{"old-photo":{"seed":937335361030604073,"previous_generation":{"seed":8233372666452194521}}},"rejected":{"old-trial":{"trial_seed":4016510582796096107}}}\n',
    [PROTECTED[2]]: '{"version":1,"reviews":{"old-photo":{"counter":9085058054470321079,"note":"Keep every character."}},"extra":18446744073709551615}\n'
  };
  const currentRaw = {...baseRaw};
  if (!options.sameHeadAsPlan) currentRaw[PROTECTED[1]] = baseRaw[PROTECTED[1]].replace('"images":{', '"images":{"other-new-photo":{"seed":18446744073709551615},');
  if (options.currentArticle) currentRaw[PROTECTED[0]] = JSON.stringify([options.currentArticle(clone(article))]);
  if (options.currentManifest) currentRaw[PROTECTED[1]] = options.currentManifest(currentRaw[PROTECTED[1]]);
  if (options.currentReviews) currentRaw[PROTECTED[2]] = options.currentReviews(currentRaw[PROTECTED[2]]);
  const protectedHashes = Object.fromEntries(PROTECTED.map(name => [name, hash(baseRaw[name])]));
  const plan = {repository, slug, articleSha256, baseCommitSha: baseCommit, baseTreeSha: baseTree,
    prompt: article.scene_prompt, logicalRunId: "news-photo-v1:" + articleSha256, protectedHashes};
  plan.identity = hash(JSON.stringify([repository, slug, articleSha256, plan.logicalRunId, plan.prompt]));
  plan.branch = "native-image-candidate/" + slug + "/" + articleSha256.slice(0, 16) + "-" + hash(plan.logicalRunId).slice(0, 12);
  plan.directory = prefix;

  const partCount = options.parts || 6;
  const source = Buffer.alloc((partCount - 1) * stage.PART_SIZE + 371, 0x5a);
  const jpeg = Buffer.alloc(16003, 0x6a), cabx = Buffer.from("unsigned synthetic metadata");
  const sourceSha = hash(source), jpegSha = hash(jpeg);
  const generationId = "22222222-2222-4222-8222-222222222222";
  const generationReceipt = {generation_id: generationId, generation_receipt: "xmp:iid:" + generationId,
    generation_id_source: "Unsigned synthetic parser fixture; no provider attestation.",
    source_c2pa: {manifest_uri: "urn:c2pa:fixture", instanceID: "xmp:iid:" + generationId,
      actions: [{label: "c2pa.actions.v2", value: {actions: [{when: "2026-11-11T07:00:00.123456789Z"}]}}],
      signature_validation: "not_performed", content_binding_validation: "not_performed"}, caBX_bytes: new Uint8Array(cabx)};
  const converted = {bytes: new Uint8Array(jpeg), base64: jpeg.toString("base64"), sha256: jpegSha,
    source_png_sha256: sourceSha, sourceWidth: 1672, sourceHeight: 941, width: 1200, height: 675, quality: 84,
    receipt: generationReceipt};
  const runtime = {sha256: hash, utf8: text => new Uint8Array(Buffer.from(text)),
    fromBase64: text => new Uint8Array(Buffer.from(text.replace(/^data:[^,]*,/, ""), "base64")),
    toBase64: bytes => Buffer.from(bytes).toString("base64"),
    convert(bytes) { assert.equal(hash(bytes), sourceSha, "converter receives the reconstructed original bytes");return converted; }};

  const files = new Map();
  function put(ref, name, content) {
    const bytes = Buffer.isBuffer(content) ? Buffer.from(content) : Buffer.from(content);
    const record = {bytes, sha: gitBlob(bytes)};
    files.set(ref + ":" + name, record);return record;
  }
  for (const name of PROTECTED) {
    put(baseCommit, name, baseRaw[name]);put(candidateCommit, name, baseRaw[name]);put(head, name, currentRaw[name]);
  }
  const parts = [];
  for (let offset = 0; offset < source.length; offset += stage.PART_SIZE) {
    const bytes = source.subarray(offset, offset + stage.PART_SIZE), name = prefix + "/source.png.part" + String(parts.length).padStart(3, "0");
    const blob = put(candidateCommit, name, bytes);
    parts.push({path: name, bytes: bytes.length, sha256: hash(bytes), blob_sha: blob.sha});
  }
  const jpegPath = prefix + "/candidate.jpg", jpegBlob = put(candidateCommit, jpegPath, jpeg);
  const proofPath = "maintenance/native-image-preflight/receipt.json";
  const proofReceipt = {version: 1, kind: "native_image_transport_preflight", status: "probe_only_not_an_article_candidate",
    repository, candidate_identity: plan.identity, logical_run_id: plan.logicalRunId,
    observed_at: "2026-11-11T06:58:00.000Z", route: "synthetic test transport",
    source: {sha256: "a".repeat(64)}, jpeg: {sha256: "b".repeat(64)}, baseline: {commit_sha: baseCommit, protected_hashes: protectedHashes}};
  put(proofCommit, proofPath, JSON.stringify(proofReceipt));
  const proof = {kind: proofReceipt.kind, status: "passed", branch: "native-image-preflight/transport", commit_sha: proofCommit,
    receipt_path: proofPath, receipt_sha256: hash(JSON.stringify(proofReceipt)), observed_at: proofReceipt.observed_at,
    route: proofReceipt.route, source_sha256: proofReceipt.source.sha256, jpeg_sha256: proofReceipt.jpeg.sha256};
  const claimPath = "maintenance/native-image-generation-claims/" + plan.identity + ".json";
  const claimReceipt = {version: 1, kind: "native_image_generation_claim", status: "reserved_generation_unconfirmed", repository,
    identity: plan.identity, slug, article_sha256: articleSha256, logical_run_id: plan.logicalRunId,
    prompt_sha256: hash(plan.prompt), reserved_at: "2026-11-11T06:59:00.000Z",
    baseline: {commit_sha: baseCommit, tree_sha: baseTree, protected_hashes: protectedHashes}, transport_preflight: proof};
  put(claimCommit, claimPath, JSON.stringify(claimReceipt));
  const claim = {kind: claimReceipt.kind, status: claimReceipt.status, branch: "native-image-generation-claim/" + plan.identity,
    receipt_path: claimPath, receipt_sha256: hash(JSON.stringify(claimReceipt)), commit_sha: claimCommit};
  const {caBX_bytes, ...plainReceipt} = generationReceipt;
  const receipt = {version: 1, kind: "native_image_candidate", status: "awaiting_visual_review", identity: plan.identity,
    repository, slug, article_sha256: articleSha256, logical_run_id: plan.logicalRunId,
    baseline: {commit_sha: baseCommit, tree_sha: baseTree, protected_hashes: protectedHashes},
    generation: {capture_kind: "native_tool_result", prompt: plan.prompt, prompt_sha256: hash(plan.prompt),
      observed_at: "2026-11-11T07:00:00.123Z", actual_output_hint: "synthetic fixture", recovery: null,
      transport_preflight: proof, generation_claim: claim},
    source: {sha256: sourceSha, bytes: source.length, width: 1672, height: 941, parts,
      c2pa: {...clone(plainReceipt), caBX_sha256: hash(cabx), caBX_base64: cabx.toString("base64")}},
    jpeg: {path: jpegPath, sha256: jpegSha, bytes: jpeg.length, width: 1200, height: 675, quality: 84, blob_sha: jpegBlob.sha},
    transform: {helper: "scripts/native_image_bytes.js", resize: "center-cover-bilinear", alpha_background: "white", width: 1200, height: 675, jpeg_quality: 84}};
  if (options.receipt) options.receipt(receipt);
  const receiptPath = prefix + "/receipt.json";
  put(candidateCommit, receiptPath, JSON.stringify(receipt));
  const review = {decision: "accepted", article_sha256: articleSha256, image_sha256: jpegSha,
    reviewed_at: "2026-11-11T07:01:00.000Z", note: "Explicit synthetic reviewer input; no actual photograph is approved by this test."};
  const reviewSource = options.reviewSource || JSON.stringify(review).replace(/}$/, ',"observed_counter":18446744073709551615}');
  const input = {plan, candidateCommitSha: candidateCommit, pullRequestNumber: 881, expectedHeadSha: head, articleSha256, reviewSource};
  const currentTree = PROTECTED.map(name => ({path: name, mode: "100644", type: "blob", sha: files.get(head + ":" + name).sha}));
  if (options.contaminated) currentTree.push(...parts.map(part => ({path: part.path, mode: "100644", type: "blob", sha: part.blob_sha})),
    {path: jpegPath, mode: "100644", type: "blob", sha: jpegBlob.sha},
    {path: receiptPath, mode: "100644", type: "blob", sha: files.get(candidateCommit + ":" + receiptPath).sha});
  if (options.existingJpeg) currentTree.push({path: "assets/generated/" + slug + ".jpg", mode: "100644", type: "blob", sha: jpegBlob.sha});
  const pr = {number: 881, state: "open", merged: false, draft: true,
    head: {sha: head, ref: "news/new-native-photo", repo: {full_name: repository}}, base: {ref: "main", repo: {full_name: repository}}};
  if (options.pr) options.pr(pr);
  const calls = [], writes = [], root = "https://api.github.com/repos/" + repository;
  let prReads = 0;
  const ok = value => ({structuredContent: value});
  const github = {
    async fetch({url}) {
      calls.push({method: "fetch", url});
      if (url === root + "/pulls/881") {
        prReads++;
        if (options.moveHead && prReads === 2) pr.head.sha = "f".repeat(40);
        return ok({content: JSON.stringify(pr)});
      }
      if (url === root + "/git/commits/" + head) return ok({content: JSON.stringify({sha: head, tree: {sha: treeSha}})});
      if (url === root + "/git/trees/" + treeSha + "?recursive=1")
        return ok({content: JSON.stringify({sha: treeSha, truncated: options.truncated === true, tree: currentTree})});
      if (url === root + "/git/ref/heads/" + claim.branch)
        return ok({content: JSON.stringify({ref: "refs/heads/" + claim.branch, object: {sha: options.changedClaim ? "f".repeat(40) : claimCommit}})});
      throw Error("Unexpected GET " + url);
    },
    async fetch_file(args) {
      calls.push({method: "fetch_file", ...args});
      assert.equal(args.repository_full_name, repository);
      const item = files.get(args.ref + ":" + args.path);
      if (!item || options.missingPath === args.path) throw Error("Unavailable fixed candidate file: " + args.path);
      const bytes = options.corruptPath === args.path ? Buffer.from("corrupted") : item.bytes;
      return ok({encoding: args.encoding, content: bytes.toString(args.encoding === "base64" ? "base64" : "utf8"),
        sha: options.wrongBlobPath === args.path ? "f".repeat(40) : item.sha});
    }
  };
  for (const method of ["create_blob", "create_tree", "create_commit", "create_branch", "update_ref"])
    github[method] = async args => {writes.push({method, args});throw Error("Preparation must never mutate GitHub");};
  const service = stage.create({runtime, github, repository, ...(options.noPatch ? {} : {manifestPatch: patch})});
  return {service, input, calls, writes, files, currentRaw, baseRaw, receipt, jpegBlob, sourceSha, jpegSha, head, treeSha, prefix,
    source, jpeg, pr, currentTree, runtime, github, repository};
}

test("881 regression: prepares only 3 public files from the current PR, preserving immutable candidate blobs", async t => {
  const f = fixture(), before = new Map([...f.files].map(([key, value]) => [key, Buffer.from(value.bytes)]));
  const start = performance.now(), packet = await f.service.preparePublication(f.input);
  t.diagnostic(`6-part preparation: ${f.calls.length} read callbacks, ${(performance.now() - start).toFixed(1)} ms with in-memory callbacks; no network timing claim`);
  assert.equal(f.calls.length, 24);
  assert.equal(packet.status, "prepared_not_published");
  assert.equal(packet.expectedHeadSha, f.head);
  assert.equal(packet.baseTreeSha, f.treeSha);
  assert.notEqual(packet.expectedHeadSha, f.input.candidateCommitSha);
  assert.deepEqual(packet.files.map(file => file.path), ["assets/generated/new-native-photo.jpg", PROTECTED[1], PROTECTED[2]]);
  assert.equal(packet.files[0].sha, f.jpegBlob.sha);
  assert.equal(packet.candidate.jpegBlobSha, f.jpegBlob.sha);
  assert.equal(packet.candidate.sourcePngSha256, f.sourceSha);
  assert.equal(packet.files.some(file => file.path.startsWith("maintenance/")), false);
  assert.equal(packet.files.some(file => file.path === PROTECTED[0]), false, "does not rewrite the article or its strategy");
  assert.equal(f.writes.length, 0);
  for (const [key, bytes] of before) assert.deepEqual(f.files.get(key).bytes, bytes, "all source, receipt and public records remain unchanged");
  for (const call of f.calls.filter(call => call.method === "fetch_file")) assert.match(call.ref, /^[0-9a-f]{40}$/);
  const record = JSON.parse(packet.files[1].content).images["new-native-photo"];
  assert.equal(record.source_capture.candidate_commit, f.input.candidateCommitSha);
  assert.equal(record.source_capture.receipt_path, f.prefix + "/receipt.json");
  assert.equal(record.source_capture.receipt_sha256, hash(f.files.get(f.input.candidateCommitSha + ":" + f.prefix + "/receipt.json").bytes));
  assert.deepEqual(record.source_capture.original_png_parts, f.receipt.source.parts);
  assert.equal(record.generation_prompt, f.input.plan.prompt);
  assert.equal(record.generated_at, f.receipt.generation.observed_at);
  assert.deepEqual(record.source_c2pa, f.receipt.source.c2pa.source_c2pa);
  assert.equal(record.source_c2pa.signature_validation, "not_performed");
  assert.equal(record.source_c2pa.actions[0].value.actions[0].when, "2026-11-11T07:00:00.123456789Z");
});

test("856 regression: old manifest/review literals and the supplied review source survive without Number rounding", async () => {
  const f = fixture(), packet = await f.service.preparePublication(f.input);
  assert.notEqual(JSON.stringify(JSON.parse(f.currentRaw[PROTECTED[1]])), f.currentRaw[PROTECTED[1]], "fixture demonstrates the unsafe full reserialization");
  const manifestSource = packet.files[1].content, record = JSON.parse(manifestSource).images["new-native-photo"];
  assert.equal(manifestSource.replace(',\n    "new-native-photo": ' + JSON.stringify(record, null, 2) + '\n  ', ""), f.currentRaw[PROTECTED[1]]);
  assert.equal(packet.files[2].content.replace(',\n    "new-native-photo": ' + f.input.reviewSource + '\n  ', ""), f.currentRaw[PROTECTED[2]]);
  assert.ok(packet.files[2].content.includes(f.input.reviewSource));
  for (const literal of ["937335361030604073", "8233372666452194521", "4016510582796096107", "18446744073709551615"])
    assert.ok(manifestSource.includes(literal));
  assert.ok(packet.files[2].content.includes("9085058054470321079"));
  assert.ok(manifestSource.includes('"other-new-photo"'), "preserves unrelated photos added since candidate staging");
});

test("8-part read count is bounded; immutable reads are cached when PR head is the original plan commit", async t => {
  for (const [parts, sameHeadAsPlan, expected] of [[8, false, 26], [8, true, 23]]) {
    const f = fixture({parts, sameHeadAsPlan}), start = performance.now();
    await f.service.preparePublication(f.input);
    assert.equal(f.calls.length, expected);
    const keys = f.calls.filter(call => call.method === "fetch_file").map(call => [call.ref, call.path, call.encoding].join(":"));
    assert.equal(new Set(keys).size, keys.length, "immutable file reads are not duplicated");
    assert.equal(f.calls.filter(call => call.url?.endsWith("/pulls/881")).length, 2, "live PR head is deliberately not cached");
    t.diagnostic(`${parts} parts, same original head=${sameHeadAsPlan}: ${f.calls.length} read callbacks, ${(performance.now() - start).toFixed(1)} ms in memory`);
  }
});

test("rejects stale or changed PR heads, wrong repository/base, closed PRs and candidate publication branches", async () => {
  const cases = [
    {pr: pr => {pr.head.sha = "e".repeat(40);}}, {moveHead: true}, {pr: pr => {pr.state = "closed";}},
    {pr: pr => {pr.head.repo.full_name = "another/repository";}}, {pr: pr => {pr.base.ref = "other";}},
    {pr: pr => {pr.head.ref = "native-image-candidate/new-native-photo/test";}}
  ];
  for (const options of cases) {
    const f = fixture(options);await assert.rejects(f.service.preparePublication(f.input), /Working publication/);assert.equal(f.writes.length, 0);
  }
});

test("rejects copied candidate chunks and existing public JPEG, image, review or rejection records", async () => {
  for (const options of [
    {contaminated: true}, {existingJpeg: true},
    {currentManifest: source => source.replace('"images":{', '"images":{"new-native-photo":{},')},
    {currentManifest: source => source.replace('"rejected":{', '"rejected":{"new-native-photo":{},')},
    {currentReviews: source => source.replace('"reviews":{', '"reviews":{"new-native-photo":{},')}
  ]) {
    const f = fixture(options);await assert.rejects(f.service.preparePublication(f.input), /candidate sources|already exists|Existing image/);assert.equal(f.writes.length, 0);
  }
});

test("rejects changed articles, scene prompts and any explicit non-photo strategy without changing the article", async () => {
  for (const change of [
    article => ({...article, title: "Changed after review"}),
    article => ({...article, scene_prompt: "Different scene"}),
    article => ({...article, visual_strategy: "illustration"}),
    article => ({...article, visual_strategy: null})
  ]) {
    const f = fixture({currentArticle: change});
    await assert.rejects(f.service.preparePublication(f.input), /Working article or scene changed/);assert.equal(f.writes.length, 0);
  }
  const f = fixture({currentArticle: article => ({...article, visual_strategy: "cpu-photo"})});
  assert.equal((await f.service.preparePublication(f.input)).status, "prepared_not_published");
});

test("does not invent acceptance: rejects missing, pending, stale, future, undated, empty or reuse review records", async () => {
  const valid = JSON.parse(fixture().input.reviewSource);
  for (const change of [
    value => {delete value.decision;}, value => {value.decision = "pending";},
    value => {value.article_sha256 = "f".repeat(64);}, value => {value.image_sha256 = "f".repeat(64);},
    value => {value.reviewed_at = "2999-01-01T00:00:00Z";}, value => {value.reviewed_at = "2026-11-11T07:01:00";},
    value => {value.reviewed_at = "2026-02-30T07:01:00Z";}, value => {value.reviewed_at = "2026-11-11T24:00:00Z";},
    value => {value.reviewed_at = "2026-11-11T06:59:59Z";}, value => {value.note = " ";}, value => {value.reuse = false;}
  ]) {
    const review = clone(valid);change(review);
    const f = fixture({reviewSource: JSON.stringify(review)});
    await assert.rejects(f.service.preparePublication(f.input), /review|provenance/i);assert.equal(f.writes.length, 0);
  }
  const f = fixture();await assert.rejects(f.service.preparePublication({...f.input, reviewSource: undefined}), /actual review source/);
});

test("rejects incomplete or false receipt provenance, original dimensions, baseline, C2PA and reservation", async () => {
  for (const change of [
    receipt => {receipt.source.width++;}, receipt => {receipt.baseline.commit_sha = "f".repeat(40);},
    receipt => {receipt.source.c2pa = null;}, receipt => {receipt.generation.generation_claim = null;},
    receipt => {receipt.generation.transport_preflight = null;}, receipt => {receipt.generation.capture_kind = "recovered_native_output";},
    receipt => {receipt.generation.prompt = "Unrecorded replacement prompt";},
    receipt => {receipt.transform.resize = "unrecorded crop";}
  ]) {
    const f = fixture({receipt: change});await assert.rejects(f.service.preparePublication(f.input));assert.equal(f.writes.length, 0);
  }
  const f = fixture({changedClaim: true});await assert.rejects(f.service.preparePublication(f.input), /reservation branch changed/);
});

test("rejects missing/corrupt source bytes, mismatched actual Git blobs, incomplete trees and invalid immutable refs", async () => {
  const candidatePath = "maintenance/native-image-candidates/new-native-photo/";
  for (const options of [
    {missingPath: candidatePath + "source.png.part005"}, {corruptPath: candidatePath + "source.png.part003"},
    {wrongBlobPath: candidatePath + "candidate.jpg"}, {wrongBlobPath: candidatePath + "source.png.part000"},
    {truncated: true}, {noPatch: true}
  ]) {
    const f = fixture(options);await assert.rejects(f.service.preparePublication(f.input));assert.equal(f.writes.length, 0);
  }
  for (const change of [
    {candidateCommitSha: "main"}, {candidateCommitSha: "3".repeat(39)}, {expectedHeadSha: "refs/heads/main"},
    {pullRequestNumber: "881"}, {articleSha256: "f".repeat(64)}
  ]) {
    const f = fixture();await assert.rejects(f.service.preparePublication({...f.input, ...change}));assert.equal(f.calls.length, 0);
  }
});

test("a failed read keeps the same candidate/PR usable for retry and old create callers need no new dependency", async () => {
  const f = fixture(), fetchFile = f.github.fetch_file;
  let failOnce = true;
  f.github.fetch_file = async args => {
    if (failOnce && args.path.endsWith("source.png.part005")) {failOnce = false;throw Error("Injected retrieval outage");}
    return fetchFile(args);
  };
  await assert.rejects(f.service.preparePublication(f.input), /Injected retrieval outage/);
  const packet = await f.service.preparePublication(f.input);
  assert.equal(packet.candidate.commitSha, f.input.candidateCommitSha);
  assert.equal(packet.expectedHeadSha, f.input.expectedHeadSha);
  assert.equal(f.writes.length, 0);
  const oldCaller = stage.create({runtime: f.runtime, github: f.github, repository: f.repository});
  assert.equal(typeof oldCaller.load, "function");
  await assert.rejects(oldCaller.preparePublication(f.input), /GeneratedManifestPatch/);
});
