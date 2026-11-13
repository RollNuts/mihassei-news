/* Independent, pure-JS candidate-staging tests. Every GitHub call is an in-memory
 * fake; these tests never contact GitHub, publish an image, or generate imagery.
 * Pass NativeImageStage and an initialized NativeImageBytes runtime. The PNG is
 * pngjs/PngSuite's 32x32 fixture, not an ImageGen output or provider receipt.
 */
var NativeImageStageTests = async function (stageAPI, runtime) {
  "use strict";
  const checks = [];
  function assert(condition, label) {
    if (!condition) throw Error("FAIL: " + label);
    checks.push(label);
  }
  function demand(condition, message) {
    if (!condition) throw Error("Mock contract: " + message);
  }
  function utf8Text(bytes) {
    let encoded = "";
    for (const byte of bytes) encoded += "%" + byte.toString(16).padStart(2, "0");
    return decodeURIComponent(encoded);
  }
  function equalBytes(a, b) {
    return a.length === b.length && a.every((value, index) => value === b[index]);
  }
  async function failure(fn, label) {
    try {
      const value = await fn();
      throw Error("FAIL: " + label + " unexpectedly returned " + JSON.stringify(value));
    } catch (error) {
      if (String(error.message || error).startsWith("FAIL:")) throw error;
      checks.push(label);
      return error;
    }
  }
  const repository = "fixture-owner/native-stage-tests";
  const slug = "native-stage-fixture";
  const baseCommitSha = "1".repeat(40);
  const baseTreeSha = "2".repeat(40);
  const hugeSeed = "9085058054470321079";
  const article = {
    slug,
    title: "新しい写真の保存テスト",
    summary: "候補だけを保存し、公開済みの記録は維持する。",
    category: "社会",
    paragraphs: ["これは実APIを呼ばない保存処理のテストです。"],
    scene_prompt: "Unsigned synthetic staging fixture."
  };
  const fingerprintFields = {};
  for (const key of ["category", "paragraphs", "scene_prompt", "slug", "summary", "title"])
    fingerprintFields[key] = article[key];
  const articleSha256 = runtime.sha256(runtime.utf8(JSON.stringify(fingerprintFields)));
  const protectedRaw = {
    "content/articles.json": "[\n  " + JSON.stringify(article) + "\n]\n",
    "assets/generated/manifest.json":
      '{ "version": 1, "images": { "' + slug + '": {"article_sha256":"' +
      articleSha256 + '","seed":' + hugeSeed + ',"sha256":"' + "a".repeat(64) +
      '","origin":"github-cpu-sd15"} }, "rejected": {} }\n',
    "content/visual_reviews.json":
      '{"version":1,"reviews":{"' + slug + '":{"decision":"accepted",' +
      '"article_sha256":"' + articleSha256 + '","image_sha256":"' +
      "a".repeat(64) + '","note":"Existing review must remain byte-for-byte intact."}}}\n'
  };
  const pngBase64 = "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAABGdBTUEAAYagMeiWXwAAAEhJREFUeJzt1cEJADAMAkCF7JH9t3ITO0Qr9KH4zuErtA0EO4AKFPgcoO3kfUx4QIECD0qHH8KEBxQo8KB0OCOpQIG7cHejwAGCsfleD0DPSwAAAABJRU5ErkJggg==";
  const pngBytes = runtime.fromBase64(pngBase64);
  const pngDataURL = "data:image/png;base64," + pngBase64;
  const observedAt = "2026-11-11T07:00:00.123Z";
  const prompt = "Synthetic candidate-storage fixture; no provider generation claimed.";
  const prefix = "maintenance/native-image-candidates/" + slug + "/";
  const receiptPath = prefix + "receipt.json";
  const jpegPath = prefix + "candidate.jpg";
  const preflightBranch = "native-image-preflight/transport";
  const preflightPrefix = "maintenance/native-image-preflight/";
  const claimBranchPrefix = "native-image-generation-claim/";
  const claimPrefix = "maintenance/native-image-generation-claims/";

  function makeMock(options) {
    const faults = Object.assign({}, options || {});
    const calls = [];
    const blobs = new Map();
    const trees = new Map();
    const commits = new Map();
    const branches = new Map();
    const files = new Map();
    let sequence = 0;
    function newSha(kind) { return runtime.sha256(kind + ":" + (++sequence)).slice(0, 40); }
    function ok(content) { return {isError: false, structuredContent: content}; }
    function apiError(code, message) {
      return {isError: true, structuredContent: {error_code: code, error_data: {message}}};
    }
    for (const [path, text] of Object.entries(protectedRaw)) {
      const sha = newSha("base-blob");
      const blob = {sha, bytes: runtime.utf8(text)};
      blobs.set(sha, blob);
      files.set(path, blob);
    }
    trees.set(baseTreeSha, files);
    commits.set(baseCommitSha, {tree: baseTreeSha, parent: null});
    function log(name, args) {
      demand(args.repository_full_name === repository || (name === "fetch" &&
        (args.url === "https://api.github.com/repos/" + repository + "/git/ref/heads/" + preflightBranch ||
        args.url.startsWith("https://api.github.com/repos/" + repository + "/git/ref/heads/" + claimBranchPrefix))),
        "repository must be explicit");
      calls.push({name, args});
    }
    function resolve(ref) {
      const commitSha = branches.get(ref) || ref;
      const commit = commits.get(commitSha);
      return commit ? trees.get(commit.tree) : null;
    }
    const github = {
      async fetch_file(args) {
        log("fetch_file", args);
        const tree = resolve(args.ref);
        if (!tree) {
          if (faults.ambiguous404) return apiError("NOT_FOUND", "Not Found");
          if (faults.foreignRef404) return apiError("NOT_FOUND", "No commit found for the ref unrelated-branch");
          return apiError("NOT_FOUND", "No commit found for the ref " + args.ref);
        }
        if (branches.has(args.ref) && args.path.startsWith(prefix) && faults.readbackOnce) {
          faults.readbackOnce = false;
          return apiError("SERVICE_UNAVAILABLE", "Injected candidate readback failure");
        }
        const blob = tree.get(args.path);
        if (!blob) return apiError("NOT_FOUND", "Repository path does not exist");
        const encoding = args.encoding || "utf-8";
        if (faults.preflightCorrupt && args.ref === preflightBranch && args.path === preflightPrefix + "probe.png")
          return ok({content: "AAAA", encoding, sha: blob.sha});
        // Reproduce the Contents API/connector's observed large-file empty result.
        if (encoding === "base64" && blob.bytes.length > 1024 * 1024)
          return ok({content: "", encoding, sha: blob.sha, size: blob.bytes.length});
        return ok({
          content: encoding === "base64" ? runtime.toBase64(blob.bytes) : utf8Text(blob.bytes),
          encoding, sha: blob.sha, size: blob.bytes.length
        });
      },
      async create_blob(args) {
        log("create_blob", args);
        demand(typeof args.content === "string", "blob content must be a string");
        const bytes = args.encoding === "base64"
          ? runtime.fromBase64(args.content) : runtime.utf8(args.content);
        const sha = newSha("blob");
        blobs.set(sha, {sha, bytes});
        return ok({sha});
      },
      async create_tree(args) {
        log("create_tree", args);
        demand(args.base_tree_sha === baseTreeSha, "tree must inherit the supplied base tree");
        demand(Array.isArray(args.tree_elements), "tree_elements must be an array");
        if (faults.treeOnce) {
          faults.treeOnce = false;
          return apiError("SERVICE_UNAVAILABLE", "Injected tree creation failure");
        }
        const tree = new Map(trees.get(args.base_tree_sha));
        for (const entry of args.tree_elements) {
          demand(entry.path.startsWith(prefix) || entry.path.startsWith(preflightPrefix) || entry.path.startsWith(claimPrefix),
            "tree must only add isolated candidate, transport-probe or generation-claim files");
          demand(entry.mode === "100644" && entry.type === "blob", "regular file entries only");
          demand(blobs.has(entry.sha), "tree must reference a created blob SHA");
          tree.set(entry.path, blobs.get(entry.sha));
        }
        const sha = newSha("tree");
        trees.set(sha, tree);
        return ok({sha});
      },
      async create_commit(args) {
        log("create_commit", args);
        demand(args.parent_sha === baseCommitSha || args.parent_sha === branches.get(preflightBranch),
          "commit must parent the immutable base or advance the isolated preflight branch");
        demand(trees.has(args.tree_sha), "commit must reference a created tree");
        demand(!args.additional_parent_shas || args.additional_parent_shas.length === 0,
          "candidate commit must not introduce unrelated parents");
        const sha = newSha("commit");
        commits.set(sha, {tree: args.tree_sha, parent: args.parent_sha});
        return ok({sha});
      },
      async create_branch(args) {
        log("create_branch", args);
        demand(typeof args.branch_name === "string" && (args.branch_name.startsWith("native-image-candidate/")
          || args.branch_name === preflightBranch || args.branch_name.startsWith(claimBranchPrefix)),
          "only isolated candidate/preflight/claim branches may be created");
        demand(commits.has(args.sha), "branch must point to an existing commit SHA");
        if (faults.branchOnce) {
          faults.branchOnce = false;
          return apiError("SERVICE_UNAVAILABLE", "Injected branch creation failure");
        }
        if (branches.has(args.branch_name)) return apiError("ALREADY_EXISTS", "Reference already exists");
        branches.set(args.branch_name, args.sha);
        if (args.branch_name.startsWith(claimBranchPrefix) && faults.claimLostResponse)
          return apiError("SERVICE_UNAVAILABLE", "Injected lost generation-claim response");
        if (args.branch_name.startsWith(claimBranchPrefix) && faults.claimBadResponse)
          return ok({branch: "unrelated-claim"});
        if (faults.lostBranchResponseOnce) {
          faults.lostBranchResponseOnce = false;
          return apiError("SERVICE_UNAVAILABLE", "Injected lost response after branch was created");
        }
        // The real connector confirms only the branch name; GET ref supplies SHA.
        return ok({branch: args.branch_name});
      },
      async fetch(args) {
        log("fetch", args);
        const branch = args.url.split("/git/ref/heads/")[1];
        let sha = branches.get(branch);
        if (!sha) return apiError("NOT_FOUND", "Not Found");
        if (branch.startsWith(claimBranchPrefix) && faults.claimWrongHead) sha = "f".repeat(40);
        return ok({content: JSON.stringify({ref: "refs/heads/" + branch, object: {sha}})});
      },
      async update_ref(args) {
        log("update_ref", args);
        demand(args.branch_name === preflightBranch && args.force === false,
          "only the transport probe may be advanced, never force pushed");
        demand(branches.get(args.branch_name) === args.expected_sha, "preflight update needs the observed head");
        demand(commits.get(args.sha)?.parent === args.expected_sha, "preflight update must fast-forward");
        branches.set(args.branch_name, args.sha);
        return ok({ref: "refs/heads/" + args.branch_name, object: {sha: args.sha}});
      }
    };
    return {
      github, calls, branches, resolve,
      writes: () => calls.filter(call => call.name.startsWith("create_") || call.name === "update_ref"),
      count: name => calls.filter(call => call.name === name).length,
      readText: (ref, path) => utf8Text(resolve(ref).get(path).bytes)
    };
  }

  async function scenario(run, faults) {
    const mock = makeMock(faults);
    let conversions = 0;
    const measuredRuntime = Object.assign({}, runtime, {
      convert(input) { conversions++; return runtime.convert(input); }
    });
    const service = await stageAPI.create({runtime: measuredRuntime, github: mock.github, repository});
    const args = {slug, articleSha256, baseCommitSha, baseTreeSha, prompt, logicalRunId: run};
    const plan = await service.prepare(args);
    const branch = "native-image-candidate/" + slug + "/" +
      articleSha256.slice(0, 16) + "-" + runtime.sha256(runtime.utf8(run)).slice(0, 12);
    const input = {imageGenResult: {image_url: pngDataURL}, observedAt};
    return {mock, service, plan, branch, input, args, measuredRuntime, conversions: () => conversions};
  }
  function protectedUnchanged(context, label) {
    const tree = context.mock.resolve(context.branch);
    assert(!!tree, label + ": candidate branch resolves");
    for (const [path, text] of Object.entries(protectedRaw))
      assert(utf8Text(tree.get(path).bytes) === text, label + ": exact raw " + path);
    assert(context.mock.readText(context.branch, "assets/generated/manifest.json").includes(hugeSeed),
      label + ": 64-bit seed literal preserved");
  }

  // A fresh operation stages bytes only after the isolated branch was probed.
  const normal = await scenario("normal");
  assert((await normal.service.probe(normal.plan)).status === "absent", "exact missing-ref response is absent");
  assert(normal.mock.writes().length === 0, "prepare and absent probe perform no writes");
  const saved = await normal.service.stage(normal.plan, normal.input);
  assert(saved.status === "saved" && saved.branch === normal.branch, "normal stage returns verified saved branch");
  const receipt = JSON.parse(normal.mock.readText(normal.branch, receiptPath));
  assert(receipt.status === "awaiting_visual_review", "staging does not mark a candidate accepted");
  assert(receipt.source.c2pa === null, "absent source metadata does not invent a generation receipt");
  assert(Array.isArray(receipt.source.parts) && receipt.source.parts.length === 1, "small source uses one PNG part");
  assert(receipt.source.parts[0].path === prefix + "source.png.part000", "source part path is deterministic");
  assert(equalBytes(normal.mock.resolve(normal.branch).get(receipt.source.parts[0].path).bytes, pngBytes),
    "stored original PNG bytes are exact");
  assert(normal.mock.resolve(normal.branch).has(jpegPath), "candidate JPEG is stored alongside receipt");
  protectedUnchanged(normal, "normal stage");

  // A new invocation can find the candidate before any image generation/conversion.
  const writesBeforeReuse = normal.mock.writes().length;
  const freshService = await stageAPI.create({runtime: normal.measuredRuntime, github: normal.mock.github, repository});
  const freshPlan = await freshService.prepare(normal.args);
  const reused = await freshService.probe(freshPlan);
  assert(reused.status === "saved" && reused.branch === normal.branch, "fresh invocation reuses an existing candidate");
  assert(normal.mock.writes().length === writesBeforeReuse, "reuse does not create Git objects or branches");
  assert(!!reused.receipt && reused.receipt.source.sha256 === runtime.sha256(pngBytes),
    "reuse verifies stored bytes without a fresh ImageGen result");

  const wrongArticle = await scenario("wrong-article-fingerprint");
  await failure(() => wrongArticle.service.prepare(Object.assign({}, wrongArticle.args, {
    articleSha256: "0".repeat(64)
  })), "prepare rejects a fingerprint that does not match the base article");
  assert(wrongArticle.mock.writes().length === 0, "wrong article fingerprint causes no writes");

  for (const [name, result] of [
    ["missing image_url", {}],
    ["remote URL instead of image bytes", {image_url: "https://example.invalid/source.png"}],
    ["malformed PNG data", {image_url: "data:image/png;base64,AAAA"}]
  ]) {
    const context = await scenario("invalid-" + name);
    await failure(() => context.service.stage(context.plan, {imageGenResult: result, observedAt}),
      "invalid result rejected: " + name);
    assert(context.mock.writes().length === 0, "invalid result causes no writes: " + name);
  }

  for (const key of ["expectedSourcePngSha256", "expectedJpegSha256"]) {
    const context = await scenario("mismatch-" + key);
    await failure(() => context.service.stage(context.plan, Object.assign({}, context.input, {[key]: "0".repeat(64)})),
      key + " mismatch rejected");
    assert(context.mock.writes().length === 0, key + " mismatch causes no writes");
  }

  for (const [label, faults] of [
    ["branch creation", {branchOnce: true}],
    ["post-commit readback", {readbackOnce: true}]
  ]) {
    const context = await scenario("recover-" + label, faults);
    const error = await failure(() => context.service.stage(context.plan, context.input),
      label + " failure does not report saved");
    assert(!!error.checkpoint, label + " failure exposes a resumable checkpoint");
    const checkpointJSON = JSON.stringify(error.checkpoint);
    assert(!checkpointJSON.includes(pngBase64) && !checkpointJSON.includes("data:image/png;base64,"),
      label + " checkpoint does not expose captured image data");
    const blobsBeforeResume = context.mock.count("create_blob");
    const restarted = await stageAPI.create({runtime: context.measuredRuntime, github: context.mock.github, repository});
    const resumed = await restarted.resume(context.plan, JSON.parse(checkpointJSON));
    assert(resumed.status === "saved" && resumed.branch === context.branch,
      label + " checkpoint resumes to verified saved");
    assert(context.mock.count("create_blob") === blobsBeforeResume,
      label + " resume reuses uploaded blobs");
    protectedUnchanged(context, label + " recovery");
  }

  // Nested stage -> resume must propagate its newest state, not the outer copy.
  const nested = await scenario("nested-checkpoint", {treeOnce: true, branchOnce: true});
  const treeFailure = await failure(() => nested.service.stage(nested.plan, nested.input),
    "initial tree failure exposes completed-file checkpoint");
  assert(treeFailure.checkpoint.filesComplete === true &&
    !treeFailure.checkpoint.treeSha && !treeFailure.checkpoint.commitSha,
    "tree failure checkpoint retains files without inventing tree or commit");
  const nestedService = await stageAPI.create({runtime: nested.measuredRuntime, github: nested.mock.github, repository});
  const branchFailure = await failure(() => nestedService.stage(nested.plan, Object.assign({}, nested.input, {
    checkpoint: JSON.parse(JSON.stringify(treeFailure.checkpoint))
  })), "nested resume preserves a subsequent branch failure");
  const commitCall = nested.mock.calls.filter(call => call.name === "create_commit").slice(-1)[0];
  const branchCall = nested.mock.calls.filter(call => call.name === "create_branch").slice(-1)[0];
  assert(!!commitCall && !!branchCall &&
    branchFailure.checkpoint.treeSha === commitCall.args.tree_sha &&
    branchFailure.checkpoint.commitSha === branchCall.args.sha,
    "nested failure checkpoint retains newly created tree and commit SHAs");
  const finalService = await stageAPI.create({runtime: nested.measuredRuntime, github: nested.mock.github, repository});
  const nestedSaved = await finalService.resume(nested.plan,
    JSON.parse(JSON.stringify(branchFailure.checkpoint)));
  assert(nestedSaved.status === "saved" && nestedSaved.branch === nested.branch,
    "new stager finishes from the latest checkpoint alone");

  // A transport error can occur after the server has committed the branch.
  const lost = await scenario("lost-branch-response", {lostBranchResponseOnce: true});
  const lostSaved = await lost.service.stage(lost.plan, lost.input);
  assert(lostSaved.status === "saved" && lostSaved.ref === lost.branch && lost.mock.branches.has(lost.branch),
    "lost branch-create response is recovered by verified branch readback");
  assert(lost.mock.count("create_branch") === 1,
    "lost-response recovery does not attempt another branch write");
  assert(!Object.prototype.hasOwnProperty.call(lostSaved, "commitSha"),
    "readback-only rescue does not claim an unverified commit SHA");

  for (const [label, faults] of [
    ["generic NOT_FOUND", {ambiguous404: true}],
    ["missing-ref response for another branch", {foreignRef404: true}]
  ]) {
    const ambiguous = await scenario("ambiguous-404-" + label, faults);
    let probeResult;
    try { probeResult = await ambiguous.service.probe(ambiguous.plan); }
    catch (error) { probeResult = {status: "error"}; }
    assert(probeResult.status !== "absent" && probeResult.status !== "saved",
      label + " cannot establish requested branch absence");
    await failure(() => ambiguous.service.stage(ambiguous.plan, ambiguous.input),
      label + " blocks staging");
    assert(ambiguous.mock.writes().length === 0, label + " causes no writes");
  }

  // A >1 MiB valid PNG exercises the actual large-Contents-response boundary.
  function concat() {
    const parts = Array.from(arguments);
    const out = new Uint8Array(parts.reduce((n, part) => n + part.length, 0));
    let offset = 0;
    for (const part of parts) { out.set(part, offset); offset += part.length; }
    return out;
  }
  function u32(n) { return new Uint8Array([n >>> 24, n >>> 16 & 255, n >>> 8 & 255, n & 255]); }
  function crc(bytes) {
    let c = 0xffffffff;
    for (const byte of bytes) { c ^= byte; for (let i = 0; i < 8; i++) c = c >>> 1 ^ ((c & 1) ? 0xedb88320 : 0); }
    return (c ^ 0xffffffff) >>> 0;
  }
  const padding = new Uint8Array(1024 * 1024 + 257);
  const ancillaryBody = concat(runtime.utf8("npAD"), padding);
  const ancillaryChunk = concat(u32(padding.length), ancillaryBody, u32(crc(ancillaryBody)));
  const largePng = concat(pngBytes.subarray(0, pngBytes.length - 12), ancillaryChunk,
    pngBytes.subarray(pngBytes.length - 12));
  const multipart = await scenario("multipart");
  const largeSaved = await multipart.service.stage(multipart.plan, {
    imageGenResult: {image_url: "data:image/png;base64," + runtime.toBase64(largePng)},
    observedAt,
    expectedSourcePngSha256: runtime.sha256(largePng),
    expectedJpegSha256: "fed04331a8d183970996abc287bcbde743a3e7acacac05d4a9c732bdc0598331"
  });
  assert(largeSaved.status === "saved", "large PNG saves through chunked source storage");
  const largeReceipt = JSON.parse(multipart.mock.readText(multipart.branch, receiptPath));
  assert(largeReceipt.source.parts.length >= 3, "source larger than 1 MiB is split into 512 KiB parts");
  const storedParts = largeReceipt.source.parts.map((part, index) => {
    assert(part.path === prefix + "source.png.part" + String(index).padStart(3, "0"),
      "multipart order and path " + index);
    const stored = multipart.mock.resolve(multipart.branch).get(part.path);
    assert(stored.bytes.length <= 512 * 1024 && stored.bytes.length === part.bytes,
      "multipart byte size " + index);
    assert(runtime.sha256(stored.bytes) === part.sha256 && stored.sha === part.blob_sha,
      "multipart SHA and blob identity " + index);
    return stored.bytes;
  });
  assert(equalBytes(concat.apply(null, storedParts), largePng), "multipart reconstructs every original PNG byte");
  const largeWrites = multipart.mock.writes().length;
  assert((await multipart.service.probe(multipart.plan)).status === "saved", "multipart probe verifies saved candidate");
  assert(multipart.mock.writes().length === largeWrites, "multipart probe is read-only");
  protectedUnchanged(multipart, "multipart stage");

  // The new entry point tests the real configured transport before invoking its
  // generator. This source remains explicitly synthetic test data in the fake.
  function gateOptions(context, counters) {
    const existingFile = {path: "/synthetic-existing/probe.png"};
    return {
      probeSource: existingFile, probeSourceRef: existingFile.path,
      expectedProbeSha256: runtime.sha256(pngBytes), route: "fixture file callback → code runtime → fake GitHub",
      async readBytes(source) {
        counters.reads++;
        if (source === existingFile) return {image_url: pngDataURL};
        demand(source === context.input.imageGenResult, "both sources must use the same actual transfer callback");
        return source;
      },
      async generate(args) {
        counters.generated++;
        demand(args.prompt === context.plan.prompt && args.transparent_background === false,
          "generator must receive the planned prompt");
        const receipt = JSON.parse(context.mock.readText(preflightBranch, preflightPrefix + "receipt.json"));
        demand(receipt.kind === "native_image_transport_preflight", "generation follows a saved isolated preflight");
        return context.input.imageGenResult;
      }
    };
  }
  for (const [label, faults, brokenReader] of [
    ["unavailable byte bridge", {}, true],
    ["failed GitHub save", {treeOnce: true}, false],
    ["mismatched branch readback", {preflightCorrupt: true}, false]
  ]) {
    const context = await scenario("gate-" + label, faults);
    const counters = {reads: 0, generated: 0}, options = gateOptions(context, counters);
    if (brokenReader) options.readBytes = async () => { throw Error("No executor-to-connector byte bridge"); };
    await failure(() => context.service.generateAndStage(context.plan, options), label + " blocks the generating entry point");
    assert(counters.generated === 0, label + " leaves generator calls at zero");
    assert(!context.mock.branches.has(context.branch), label + " cannot create an article candidate");
  }
  const gated = await scenario("gated-save"), counters = {reads: 0, generated: 0};
  const gatedSaved = await gated.service.generateAndStage(gated.plan, gateOptions(gated, counters));
  assert(gatedSaved.status === "saved" && counters.generated === 1 && counters.reads === 2,
    "successful gate reads probe and generated output through one callback and generates once");
  const probeReceipt = JSON.parse(gated.mock.readText(preflightBranch, preflightPrefix + "receipt.json"));
  const gatedReceipt = JSON.parse(gated.mock.readText(gated.branch, receiptPath));
  const claimBranch = claimBranchPrefix + gated.plan.identity;
  const claimReceipt = JSON.parse(gated.mock.readText(claimBranch, claimPrefix + gated.plan.identity + ".json"));
  assert(probeReceipt.kind === "native_image_transport_preflight" &&
    probeReceipt.status === "probe_only_not_an_article_candidate" && !probeReceipt.generation,
    "preflight fixture is never labelled a generated article image");
  assert(gatedReceipt.kind === "native_image_candidate" && gatedReceipt.status === "awaiting_visual_review" &&
    gatedReceipt.generation.transport_preflight.status === "passed" &&
    gatedReceipt.generation.generation_claim.branch === claimBranch,
    "actual candidate remains unapproved and records its transport check and durable generation claim");
  assert(claimReceipt.kind === "native_image_generation_claim" &&
    claimReceipt.status === "reserved_generation_unconfirmed" && !claimReceipt.generation,
    "reservation is not a generated image or proof of generation completion");
  assert(!gated.mock.resolve(gated.branch).has(preflightPrefix + "probe.png") &&
    !gated.mock.resolve(gated.branch).has(claimPrefix + gated.plan.identity + ".json"),
    "transport fixture and claim remain outside the article candidate tree");
  protectedUnchanged(gated, "gated save");
  const beforeReuse = gated.mock.writes().length;
  const reusedGate = await gated.service.generateAndStage(gated.plan, {});
  assert(reusedGate.status === "saved" && counters.generated === 1 && gated.mock.writes().length === beforeReuse,
    "saved candidate reuse does not need capabilities or generate a second image");

  const secondPlan = await gated.service.prepare({...gated.args, logicalRunId: "another-generating-invocation"});
  const nextContext = {...gated, plan: secondPlan}, secondCounters = {reads: 0, generated: 0};
  await gated.service.generateAndStage(secondPlan, gateOptions(nextContext, secondCounters));
  assert(secondCounters.reads === 2 && secondCounters.generated === 1 && gated.mock.count("update_ref") === 1,
    "next invocation repeats real transport rather than caching the earlier pass");
  assert([...gated.mock.branches.keys()].filter(name => name.startsWith("native-image-preflight/")).length === 1,
    "repeated transport checks advance one preflight branch without branch proliferation");

  const transportOnly = await scenario("transport-only"), transportCounters = {reads: 0, generated: 0};
  const transportOptions = gateOptions(transportOnly, transportCounters);
  delete transportOptions.generate;
  const transportProof = await transportOnly.service.checkTransport(transportOnly.plan, transportOptions);
  assert(transportProof.status === "passed" && transportCounters.reads === 1 && transportCounters.generated === 0 &&
    [...transportOnly.mock.branches.keys()].join() === preflightBranch,
    "standalone transport check requires no generator and creates neither a claim nor an article candidate");

  const completedDuringProbe = await scenario("candidate-saved-during-preflight"), completedCounters = {reads: 0, generated: 0};
  const completedOptions = gateOptions(completedDuringProbe, completedCounters), completedRead = completedOptions.readBytes;
  completedOptions.readBytes = async source => {
    await completedDuringProbe.service.stage(completedDuringProbe.plan, completedDuringProbe.input);
    return await completedRead(source);
  };
  const completed = await completedDuringProbe.service.generateAndStage(completedDuringProbe.plan, completedOptions);
  assert(completed.status === "saved" && completed.reused && completedCounters.generated === 0 &&
    !completedDuringProbe.mock.branches.has(claimBranchPrefix + completedDuringProbe.plan.identity),
    "candidate completed during preflight is re-probed and recovered without generation or reservation");

  const parallel = await scenario("two-workers-one-plan"), firstCounters = {reads: 0, generated: 0}, otherCounters = {reads: 0, generated: 0};
  const firstOptions = gateOptions(parallel, firstCounters), firstGenerate = firstOptions.generate;
  let announceGeneration, releaseGeneration;
  const generationStarted = new Promise(resolve => { announceGeneration = resolve; });
  const generationHeld = new Promise(resolve => { releaseGeneration = resolve; });
  firstOptions.generate = async args => {
    const output = await firstGenerate(args);
    announceGeneration();
    await generationHeld;
    return output;
  };
  const firstWorker = parallel.service.generateAndStage(parallel.plan, firstOptions);
  await Promise.race([generationStarted, firstWorker.then(() => { throw Error("First worker unexpectedly completed before release"); })]);
  const otherWorker = stageAPI.create({runtime: parallel.measuredRuntime, github: parallel.mock.github, repository});
  let conflict;
  try {
    conflict = await failure(() => otherWorker.generateAndStage(parallel.plan, gateOptions(parallel, otherCounters)),
      "independent worker cannot generate while the owner is generating the same plan");
  } finally { releaseGeneration(); }
  const parallelSaved = await firstWorker;
  assert(parallelSaved.status === "saved" && firstCounters.generated + otherCounters.generated === 1 &&
    conflict.generationClaim.branch === claimBranchPrefix + parallel.plan.identity,
    "durable create-only claim permits exactly one generator across independent workers");

  for (const [label, faults] of [
    ["lost claim response", {claimLostResponse: true}],
    ["invalid claim response", {claimBadResponse: true}],
    ["mismatched claim ref SHA", {claimWrongHead: true}]
  ]) {
    const context = await scenario(label, faults), counts = {reads: 0, generated: 0};
    const error = await failure(() => context.service.generateAndStage(context.plan, gateOptions(context, counts)),
      label + " cannot establish generation ownership");
    assert(counts.generated === 0 && error.generationClaim.branch === claimBranchPrefix + context.plan.identity &&
      context.mock.branches.has(error.generationClaim.branch) && !context.mock.branches.has(context.branch),
      label + " keeps its reservation reference without generating or creating a candidate");
  }

  const interruptedGate = await scenario("generated-transfer-interruption"), interruptedCounters = {reads: 0, generated: 0};
  const interruptedOptions = gateOptions(interruptedGate, interruptedCounters), readNormally = interruptedOptions.readBytes;
  interruptedOptions.readBytes = async source => {
    if (source === interruptedGate.input.imageGenResult) throw Error("Generated result transfer interrupted");
    return await readNormally(source);
  };
  const interruption = await failure(() => interruptedGate.service.generateAndStage(interruptedGate.plan, interruptedOptions),
    "post-generation transfer interruption retains recovery evidence");
  assert(interruption.imageGenResult === interruptedGate.input.imageGenResult && interruption.transportPreflight.status === "passed",
    "failed save exposes the actual generated result without relabelling the fixture");
  await failure(() => interruptedGate.service.generateAndStage(interruptedGate.plan, interruptedOptions),
    "same-execution retry must recover the prior output");
  assert(interruptedCounters.generated === 1, "transfer failure cannot cause a duplicate generation in the same execution");
  const freshInterrupted = stageAPI.create({runtime: interruptedGate.measuredRuntime, github: interruptedGate.mock.github, repository});
  await failure(() => freshInterrupted.generateAndStage(interruptedGate.plan, interruptedOptions),
    "fresh runtime must recover a failed generated transfer instead of claiming it again");
  assert(interruptedCounters.generated === 1, "durable reservation prevents regeneration after a fresh runtime loses transient state");

  let claimRead = false;
  const lookupFailure = stageAPI.create({runtime: interruptedGate.measuredRuntime, repository,
    github: {...interruptedGate.mock.github, async fetch_file(args) {
      if (claimRead && args.ref === interruptedGate.branch)
        throw Error("Candidate lookup interrupted after observing the existing claim");
      if (args.ref === claimBranchPrefix + interruptedGate.plan.identity) claimRead = true;
      return await interruptedGate.mock.github.fetch_file(args);
    }}
  });
  const lookupError = await failure(() => lookupFailure.generateAndStage(interruptedGate.plan, interruptedOptions),
    "lookup failure after an existing claim stops generation");
  assert(interruptedCounters.generated === 1 &&
    lookupError.generationClaim.branch === claimBranchPrefix + interruptedGate.plan.identity,
    "claim reference survives a candidate-recovery lookup failure");

  return {passed: checks.length, checks};
};
if (typeof module !== "undefined" && module.exports) module.exports = NativeImageStageTests;
