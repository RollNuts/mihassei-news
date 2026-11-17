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
    const cloud = {
      repository: {full_name: repository, private: false, visibility: "public"},
      workflow: {path: ".github/workflows/free-cpu-scenes.yml", state: "active"},
      mainSha: baseCommitSha,
      commit: {sha: baseCommitSha, tree: {sha: baseTreeSha}},
      tree: {sha: baseTreeSha, truncated: false, tree: [
        {path: "assets/generated/existing.jpg", type: "blob", size: 4096},
        {path: "assets/generated/archive/existing.JPEG", type: "blob", size: 1024},
        {path: "assets/generated/manifest.json", type: "blob", size: 3000},
        {path: "assets/other.jpg", type: "blob", size: 999999}
      ]}
    };
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
        (args.url === "https://api.github.com/repos/" + repository ||
        args.url.startsWith("https://api.github.com/repos/" + repository + "/"))),
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
        const root = "https://api.github.com/repos/" + repository;
        function cloudResult(name) {
          if (cloud.error === name) return apiError("SERVICE_UNAVAILABLE", "Injected " + name + " lookup failure");
          if (cloud.malformed === name) return ok({content: "{unreadable"});
          return ok({content: JSON.stringify(cloud[name])});
        }
        if (args.url === root) return cloudResult("repository");
        if (args.url === root + "/actions/workflows/free-cpu-scenes.yml") return cloudResult("workflow");
        if (args.url === root + "/git/commits/" + cloud.mainSha) return cloudResult("commit");
        if (args.url === root + "/git/trees/" + cloud.commit.tree.sha + "?recursive=1") return cloudResult("tree");
        const branch = args.url.split("/git/ref/heads/")[1];
        let sha = branch === "main" ? cloud.mainSha : branches.get(branch);
        if (branch === "main" && cloud.error === "main") return apiError("SERVICE_UNAVAILABLE", "Injected main lookup failure");
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
      github, calls, branches, resolve, cloud,
      writes: () => calls.filter(call => call.name.startsWith("create_") || call.name === "update_ref"),
      count: name => calls.filter(call => call.name === name).length,
      readText: (ref, path) => utf8Text(resolve(ref).get(path).bytes)
    };
  }

  async function scenario(run, faults, createOptions) {
    const mock = makeMock(faults);
    let conversions = 0;
    const measuredRuntime = Object.assign({}, runtime, {
      convert(input) { conversions++; return runtime.convert(input); }
    });
    const service = await stageAPI.create({runtime: measuredRuntime, github: mock.github, repository, ...createOptions});
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
        demand(context.mock.calls.slice(-1)[0].name === "readCloudWorkflow" || context.mock.calls.slice(-1)[0].args.url ===
          "https://api.github.com/repos/" + repository + "/actions/workflows/free-cpu-scenes.yml",
          "generator must immediately follow a live workflow check");
        const receipt = JSON.parse(context.mock.readText(preflightBranch, preflightPrefix + "receipt.json"));
        demand(receipt.kind === "native_image_transport_preflight", "generation follows a saved isolated preflight");
        return context.input.imageGenResult;
      }
    };
  }

  async function callbackScenario(run, change) {
    let context;
    const requests = [];
    context = await scenario(run, {}, {readCloudWorkflow: async ({nonce}) => {
      requests.push(nonce);
      context.mock.calls.push({name: "readCloudWorkflow", args: {nonce}});
      const response = {version: 1, repository, path: context.mock.cloud.workflow.path,
        state: context.mock.cloud.workflow.state, nonce, checked_at: new Date().toISOString()};
      return change ? change(response) : response;
    }});
    // Reproduce the connector allowlist: no workflow URL can be fetched through
    // github.fetch in this runtime. Repository/ref/commit/tree reads still work.
    const fetchAllowed = context.mock.github.fetch;
    context.mock.github.fetch = async args => {
      if (args.url.includes("/actions/workflows/")) throw Error("Workflow URL is not allowed by connector");
      return fetchAllowed(args);
    };
    return {...context, requests};
  }
  const callbackReady = await callbackScenario("callback-success"), callbackCounts = {reads: 0, generated: 0};
  assert((await callbackReady.service.cloudStatus()).state === "active",
    "a verified read-only workflow callback supports a connector without workflow URL access");
  const callbackSaved = await callbackReady.service.generateAndStage(callbackReady.plan, gateOptions(callbackReady, callbackCounts));
  assert(callbackSaved.status === "saved" && callbackCounts.generated === 1 &&
    callbackReady.requests.length > callbackReady.mock.writes().length &&
    new Set(callbackReady.requests).size === callbackReady.requests.length &&
    callbackReady.requests.every(nonce => /^[0-9a-f]{64}$/.test(nonce)),
    "workflow callbacks receive a new nonce on every live check through generation and all writes");
  assert(callbackReady.mock.calls.every((call, index) => !(call.name.startsWith("create_") || call.name === "update_ref") ||
    callbackReady.mock.calls[index - 1]?.name === "readCloudWorkflow"),
    "every callback-protected mutation immediately follows its live workflow response");
  callbackReady.mock.cloud.workflow.state = "disabled_manually";
  const callbackWrites = callbackReady.mock.writes().length;
  await failure(() => callbackReady.service.cloudStatus(), "callback workflow stops are read live after a successful invocation");
  assert((await callbackReady.service.load(callbackReady.plan)).status === "saved" &&
    callbackReady.mock.writes().length === callbackWrites,
    "stopped callback workers retain read-only access to verified existing candidates");

  const callbackWrapped = await callbackScenario("callback-wrapped", response =>
    ({structuredContent: {content: JSON.stringify(response)}}));
  assert((await callbackWrapped.service.cloudStatus()).state === "active",
    "workflow callback can return the existing connector JSON envelope");

  const actualNow = Date.now, fixedNow = Date.now();
  try {
    Date.now = () => fixedNow;
    const withClockOffset = offset => response =>
      ({...response, checked_at: new Date(fixedNow + offset).toISOString()});
    const smallSkew = await callbackScenario("callback-clock-skew", withClockOffset(43));
    const skewCounts = {reads: 0, generated: 0};
    const skewSaved = await smallSkew.service.generateAndStage(smallSkew.plan, gateOptions(smallSkew, skewCounts));
    assert(skewSaved.status === "saved" && skewCounts.generated === 1,
      "a proxy clock 43 ms ahead does not block verified generation and candidate storage");
    for (const offset of [1000, -10000]) {
      const boundary = await callbackScenario("callback-clock-boundary-" + offset, withClockOffset(offset));
      assert((await boundary.service.cloudStatus()).state === "active",
        "verified workflow time accepts the inclusive clock boundary " + offset + " ms");
    }
    for (const offset of [1001, -10001]) {
      const outside = await callbackScenario("callback-clock-outside-" + offset, withClockOffset(offset));
      const counts = {reads: 0, generated: 0};
      await failure(() => outside.service.generateAndStage(outside.plan, gateOptions(outside, counts)),
        "workflow time outside the clock boundary stops generation at " + offset + " ms");
      assert(counts.generated === 0 && counts.reads === 0 && outside.mock.writes().length === 0,
        "out-of-window clock responses cannot read image bytes, generate or write");
    }
  } finally {
    Date.now = actualNow;
  }

  for (const [label, change] of [
    ["disabled state", response => ({...response, state: "disabled_manually"})],
    ["unknown state", response => ({...response, state: "unknown"})],
    ["callback failure", () => { throw Error("Read-only proxy unavailable"); }],
    ["provider error", () => ({isError: true, structuredContent: {error_code: "UNAVAILABLE"}})],
    ["unknown version", response => ({...response, version: 2})],
    ["wrong repository", response => ({...response, repository: "other/repository"})],
    ["wrong workflow", response => ({...response, path: ".github/workflows/other.yml"})],
    ["wrong nonce", response => ({...response, nonce: "stale-request"})],
    ["missing checked_at", response => ({...response, checked_at: undefined})],
    ["unqualified checked_at", response => ({...response, checked_at: "2026-11-16T12:00:00"})],
    ["stale checked_at", response => ({...response, checked_at: new Date(Date.now() - 10001).toISOString()})],
    ["future checked_at", response => ({...response, checked_at: new Date(Date.now() + 60000).toISOString()})],
    ["malformed JSON", () => ({content: "{unreadable"})]
  ]) {
    const context = await callbackScenario("callback-block-" + label, change), counts = {reads: 0, generated: 0};
    const error = await failure(() => context.service.generateAndStage(context.plan, gateOptions(context, counts)),
      label + " blocks the callback generation route");
    assert(error.message.includes("Cloud image operations stopped:") && counts.generated === 0 &&
      counts.reads === 0 && context.mock.writes().length === 0 && context.requests.length === 1 &&
      !context.mock.calls.some(call => call.name === "fetch" && call.args.url.includes("/actions/workflows/")),
      label + " permits neither generator/writes nor a fallback after callback failure");
    await failure(() => context.service.stage(context.plan, context.input), label + " also blocks callback recovery writes");
    assert(context.mock.writes().length === 0, label + " cannot bypass callback verification via stage");
  }

  const cloudReady = await scenario("cloud-status");
  const readyStatus = await cloudReady.service.cloudStatus();
  assert(readyStatus.state === "active" && readyStatus.public === true &&
    readyStatus.main_commit_sha === baseCommitSha && readyStatus.jpeg_bytes === 5120 &&
    readyStatus.jpeg_cap_bytes === 1536 * 1024 * 1024 && cloudReady.mock.writes().length === 0,
    "cloud status reads authoritative public metadata, workflow and generated JPEG sizes without writes");
  await cloudReady.service.cloudStatus();
  const cloudURLs = () => cloudReady.mock.calls.filter(call => call.name === "fetch").map(call => call.args.url);
  assert(cloudURLs().filter(url => url.includes("/git/trees/")).length === 1 &&
    cloudURLs().filter(url => url.endsWith("/actions/workflows/free-cpu-scenes.yml")).length === 2 &&
    cloudURLs().filter(url => url.endsWith("/git/ref/heads/main")).length === 2 &&
    cloudURLs().filter(url => url === "https://api.github.com/repos/" + repository).length === 2,
    "only immutable main JPEG totals are cached; visibility, main ref and stop authority are live");
  cloudReady.mock.cloud.mainSha = "3".repeat(40);
  cloudReady.mock.cloud.commit = {sha: cloudReady.mock.cloud.mainSha, tree: {sha: "4".repeat(40)}};
  cloudReady.mock.cloud.tree = {sha: "4".repeat(40), truncated: false,
    tree: [{path: "assets/generated/new.jpg", type: "blob", size: 8192}]};
  assert((await cloudReady.service.cloudStatus()).jpeg_bytes === 8192 &&
    cloudURLs().filter(url => url.includes("/git/trees/")).length === 2,
    "a changed main ref invalidates the JPEG snapshot and reads its immutable tree");

  for (const [label, change] of [
    ["disabled workflow", cloud => { cloud.workflow.state = "disabled_manually"; }],
    ["unknown workflow", cloud => { cloud.workflow.state = "unknown"; }],
    ["missing workflow state", cloud => { delete cloud.workflow.state; }],
    ["wrong workflow path", cloud => { cloud.workflow.path = ".github/workflows/other.yml"; }],
    ["private repository", cloud => { cloud.repository.private = true; cloud.repository.visibility = "private"; }],
    ["unknown publicity", cloud => { delete cloud.repository.private; }],
    ["unknown visibility", cloud => { delete cloud.repository.visibility; }],
    ["wrong repository", cloud => { cloud.repository.full_name = "other/repository"; }],
    ["JPEG cap exceeded", cloud => { cloud.tree.tree[0].size = 1536 * 1024 * 1024; }],
    ["unknown JPEG size", cloud => { delete cloud.tree.tree[0].size; }],
    ["invalid JPEG size", cloud => { cloud.tree.tree[0].size = -1; }],
    ["truncated tree", cloud => { cloud.tree.truncated = true; }],
    ["unknown tree completeness", cloud => { delete cloud.tree.truncated; }],
    ...["repository", "main", "commit", "tree", "workflow"].map(name =>
      [name + " API failure", cloud => { cloud.error = name; }]),
    ["malformed workflow response", cloud => { cloud.malformed = "workflow"; }]
  ]) {
    const context = await scenario("cloud-block-" + label), counts = {reads: 0, generated: 0};
    change(context.mock.cloud);
    const options = gateOptions(context, counts);
    const error = await failure(() => context.service.generateAndStage(context.plan, options),
      label + " blocks new generation");
    assert(error.message.includes("Cloud image operations stopped:") &&
      counts.generated === 0 && counts.reads === 0 && context.mock.writes().length === 0,
      label + " stops before the byte callback, generator and remote writes");
    await failure(() => context.service.stage(context.plan, context.input), label + " blocks recovery writes");
    await failure(() => context.service.checkTransport(context.plan, options), label + " blocks transport diagnostic writes");
    assert(context.mock.writes().length === 0 && counts.reads === 0,
      label + " cannot write through stage or checkTransport");
  }
  const capBoundary = await scenario("cloud-cap-boundary");
  capBoundary.mock.cloud.tree.tree[0].size = 1536 * 1024 * 1024 - 1024;
  assert((await capBoundary.service.cloudStatus()).jpeg_bytes === 1536 * 1024 * 1024,
    "the existing inclusive 1.5 GiB JPEG cap permits its exact boundary");

  const stopBetweenWrites = await scenario("stop-between-writes");
  const writeBlob = stopBetweenWrites.mock.github.create_blob;
  stopBetweenWrites.mock.github.create_blob = async args => {
    const result = await writeBlob(args);
    stopBetweenWrites.mock.cloud.workflow.state = "disabled_manually";
    return result;
  };
  const stoppedWrite = await failure(() => stopBetweenWrites.service.stage(stopBetweenWrites.plan, stopBetweenWrites.input),
    "a stop arriving after the first blob blocks the next remote write");
  assert(stopBetweenWrites.mock.writes().length === 1 && stoppedWrite.checkpoint.identity === stopBetweenWrites.plan.identity,
    "mid-upload stop retains the original checkpoint and makes no further writes");

  const stoppedResume = await scenario("stop-before-resume", {treeOnce: true});
  const resumeCheckpoint = (await failure(() => stoppedResume.service.stage(stoppedResume.plan, stoppedResume.input),
    "resume fixture retains uploaded files")).checkpoint;
  const writesBeforeStop = stoppedResume.mock.writes().length;
  stoppedResume.mock.cloud.workflow.state = "disabled_manually";
  const stoppedResumeError = await failure(() => stoppedResume.service.resume(stoppedResume.plan, resumeCheckpoint),
    "resume enforces live workflow stop before its next mutation");
  assert(stoppedResume.mock.writes().length === writesBeforeStop && stoppedResumeError.checkpoint.filesComplete &&
    stoppedResumeError.checkpoint.identity === stoppedResume.plan.identity,
    "stopped resume preserves its original complete-file checkpoint without writes");

  const stoppedBeforeGenerate = await scenario("stop-after-claim"), stoppedCounts = {reads: 0, generated: 0};
  const fetchBeforeStop = stoppedBeforeGenerate.mock.github.fetch;
  stoppedBeforeGenerate.mock.github.fetch = async args => {
    const result = await fetchBeforeStop(args);
    if (args.url.endsWith("/git/ref/heads/" + claimBranchPrefix + stoppedBeforeGenerate.plan.identity))
      stoppedBeforeGenerate.mock.cloud.workflow.state = "disabled_manually";
    return result;
  };
  const stoppedClaim = await failure(() => stoppedBeforeGenerate.service.generateAndStage(stoppedBeforeGenerate.plan,
    gateOptions(stoppedBeforeGenerate, stoppedCounts)), "live stop after claim ownership blocks the generator");
  assert(stoppedCounts.generated === 0 && stoppedClaim.generationClaim.branch ===
    claimBranchPrefix + stoppedBeforeGenerate.plan.identity && stoppedBeforeGenerate.mock.branches.has(stoppedClaim.generationClaim.branch),
    "pre-generation stop preserves the existing claim without replacing its identity");

  const stoppedAfterGenerate = await scenario("stop-after-generation"), outputCounts = {reads: 0, generated: 0};
  const outputOptions = gateOptions(stoppedAfterGenerate, outputCounts), generateOutput = outputOptions.generate;
  let writesBeforeOutputStop;
  outputOptions.generate = async args => {
    const output = await generateOutput(args);
    writesBeforeOutputStop = stoppedAfterGenerate.mock.writes().length;
    stoppedAfterGenerate.mock.cloud.workflow.state = "disabled_manually";
    return output;
  };
  const outputStop = await failure(() => stoppedAfterGenerate.service.generateAndStage(stoppedAfterGenerate.plan, outputOptions),
    "a stop after generation blocks all candidate uploads");
  assert(outputCounts.generated === 1 && stoppedAfterGenerate.mock.writes().length === writesBeforeOutputStop &&
    outputStop.imageGenResult === stoppedAfterGenerate.input.imageGenResult &&
    outputStop.checkpoint.identity === stoppedAfterGenerate.plan.identity && !stoppedAfterGenerate.mock.branches.has(stoppedAfterGenerate.branch),
    "post-generation stop retains real output, claim and checkpoint without more writes or generation");

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
  assert(gated.mock.calls.every((call, index) => !(call.name.startsWith("create_") || call.name === "update_ref") ||
    gated.mock.calls[index - 1]?.args.url === "https://api.github.com/repos/" + repository + "/actions/workflows/free-cpu-scenes.yml"),
    "every successful remote mutation immediately follows a live workflow state response");
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
  gated.mock.cloud.workflow.state = "disabled_manually";
  const reusedGate = await gated.service.generateAndStage(gated.plan, {});
  assert(reusedGate.status === "saved" && counters.generated === 1 && gated.mock.writes().length === beforeReuse,
    "saved candidate reuse remains read-only while stopped without generating a second image");
  assert((await gated.service.load(gated.plan)).status === "saved" && gated.mock.writes().length === beforeReuse,
    "stopped workers can still load verified existing candidate bytes");
  gated.mock.cloud.workflow.state = "active";

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

  // External tools run outside this JavaScript callback runtime. Reservation
  // grants one handoff only after the same real transport and durable claim.
  const external = await scenario("external-two-phase"), externalCounts = {reads: 0, generated: 0};
  const externalOptions = gateOptions(external, externalCounts);
  delete externalOptions.generate;
  const reserved = await external.service.reserveExternalGeneration(external.plan, externalOptions);
  assert(reserved.status === "reserved" && reserved.plan.identity === external.plan.identity &&
    reserved.transportPreflight.status === "passed" && externalCounts.reads === 1 && externalCounts.generated === 0,
    "external reservation runs the real byte preflight without requiring or invoking a generator");
  const externalClaim = reserved.generationClaim;
  const externalClaimReceipt = JSON.parse(external.mock.readText(externalClaim.branch, externalClaim.receipt_path));
  assert(externalClaim.branch === claimBranchPrefix + external.plan.identity &&
    external.mock.branches.get(externalClaim.branch) === externalClaim.commit_sha &&
    runtime.sha256(runtime.utf8(external.mock.readText(externalClaim.commit_sha, externalClaim.receipt_path))) ===
      externalClaim.receipt_sha256 && externalClaimReceipt.identity === external.plan.identity &&
    externalClaimReceipt.article_sha256 === external.plan.articleSha256 &&
    externalClaimReceipt.prompt_sha256 === runtime.sha256(runtime.utf8(external.plan.prompt)) &&
    externalClaimReceipt.baseline.commit_sha === external.plan.baseCommitSha &&
    externalClaimReceipt.baseline.tree_sha === external.plan.baseTreeSha &&
    JSON.stringify(externalClaimReceipt.transport_preflight) === JSON.stringify(reserved.transportPreflight),
    "external reservation verifies an immutable and live claim bound to the plan, prompt, baseline and transport proof");
  assert(external.mock.calls.slice(-1)[0].args.url ===
    "https://api.github.com/repos/" + repository + "/actions/workflows/free-cpu-scenes.yml" &&
    !external.mock.branches.has(external.branch),
    "external handoff immediately follows the final live cloud guard and creates no article candidate");
  const claimCreates = () => external.mock.calls.filter(call => call.name === "create_branch" &&
    call.args.branch_name.startsWith(claimBranchPrefix)).length;
  await failure(() => external.service.reserveExternalGeneration(external.plan, gateOptions(external, externalCounts)),
    "same service cannot reserve the same external generation again");
  const externalFresh = stageAPI.create({runtime: external.measuredRuntime, github: external.mock.github, repository});
  await failure(() => externalFresh.reserveExternalGeneration(external.plan, gateOptions(external, externalCounts)),
    "fresh service cannot acquire the existing external generation claim");
  assert(claimCreates() === 1 && externalCounts.generated === 0 && !external.mock.branches.has(external.branch),
    "repeated external reservations neither replace the durable claim nor invoke a supplied generator");

  const externalInput = {...external.input, observedAt: new Date().toISOString(),
    transportPreflight: reserved.transportPreflight, generationClaim: externalClaim};
  const alternatePlan = await externalFresh.prepare({...external.args, logicalRunId: "external-other-plan"});
  for (const [label, plan, input] of [
    ["cross-plan reservation", alternatePlan, externalInput],
    ["altered prompt", {...external.plan, prompt: external.plan.prompt + " altered"}, externalInput],
    ["altered article", {...external.plan, articleSha256: "f".repeat(64)}, externalInput],
    ["altered baseline", {...external.plan, baseTreeSha: "4".repeat(40)}, externalInput],
    ["altered protected hash", {...external.plan, protectedHashes: {...external.plan.protectedHashes,
      "content/articles.json": "f".repeat(64)}}, externalInput],
    ["tampered transport proof", external.plan, {...externalInput,
      transportPreflight: {...reserved.transportPreflight, receipt_sha256: "0".repeat(64)}}],
    ["tampered claim receipt", external.plan, {...externalInput,
      generationClaim: {...externalClaim, receipt_sha256: "0".repeat(64)}}],
    ["tampered claim commit", external.plan, {...externalInput,
      generationClaim: {...externalClaim, commit_sha: "f".repeat(40)}}]
  ]) {
    const writes = external.mock.writes().length;
    await failure(() => externalFresh.stageReserved(plan, input), label + " cannot stage reserved output");
    assert(external.mock.writes().length === writes && !external.mock.branches.has(external.branch),
      label + " is rejected before every remote write");
  }
  const writesBeforeClaimMove = external.mock.writes().length;
  external.mock.branches.set(externalClaim.branch, "f".repeat(40));
  try {
    await failure(() => externalFresh.stageReserved(external.plan, externalInput),
      "a changed live claim ref invalidates an otherwise intact external reservation");
    assert(external.mock.writes().length === writesBeforeClaimMove,
      "changed live claim ownership is rejected before any candidate write");
  } finally {
    external.mock.branches.set(externalClaim.branch, externalClaim.commit_sha);
  }
  const writesBeforeReservedStop = external.mock.writes().length;
  external.mock.cloud.workflow.state = "disabled_manually";
  const stoppedExternal = await failure(() => externalFresh.stageReserved(external.plan, externalInput),
    "live cloud stop between external reservation and output blocks staging");
  assert(stoppedExternal.observedAt === externalInput.observedAt &&
    stoppedExternal.imageGenResult === externalInput.imageGenResult &&
    stoppedExternal.generationClaim.branch === externalClaim.branch && stoppedExternal.checkpoint.identity === external.plan.identity,
    "external failure retains the original PNG, first observation, claim and checkpoint without retimestamping");
  assert(external.mock.writes().length === writesBeforeReservedStop,
    "stopped external staging preserves its reservation without candidate writes");
  external.mock.cloud.workflow.state = "active";
  const externalSaved = await externalFresh.stageReserved(external.plan, externalInput);
  const externalReceipt = JSON.parse(external.mock.readText(external.branch, receiptPath));
  assert(externalSaved.status === "saved" && externalReceipt.identity === external.plan.identity &&
    externalReceipt.generation.observed_at === externalInput.observedAt &&
    JSON.stringify(externalReceipt.generation.transport_preflight) === JSON.stringify(reserved.transportPreflight) &&
    JSON.stringify(externalReceipt.generation.generation_claim) === JSON.stringify(externalClaim) &&
    equalBytes(external.mock.resolve(external.branch).get(externalReceipt.source.parts[0].path).bytes, pngBytes),
    "fresh service stages the exact original external PNG with its original reservation evidence and observation time");
  protectedUnchanged(external, "external two-phase stage");
  const externalSavedWrites = external.mock.writes().length;
  external.mock.cloud.workflow.state = "disabled_manually";
  const externalReused = await externalFresh.reserveExternalGeneration(external.plan, {});
  const externalStageReused = await externalFresh.stageReserved(external.plan, externalInput);
  assert(externalReused.status === "saved" && externalStageReused.status === "saved" &&
    externalReused.receipt.identity === external.plan.identity && externalCounts.generated === 0 &&
    external.mock.writes().length === externalSavedWrites,
    "verified saved external candidate short-circuits both phases without new writes or generation while stopped");

  const callbackExternal = await scenario("external-ignores-generator"), callbackExternalCounts = {reads: 0, generated: 0};
  const callbackExternalOptions = gateOptions(callbackExternal, callbackExternalCounts);
  callbackExternalOptions.generate = async () => { callbackExternalCounts.generated++; throw Error("External reserve invoked generate"); };
  const callbackReservation = await callbackExternal.service.reserveExternalGeneration(callbackExternal.plan, callbackExternalOptions);
  assert(callbackReservation.status === "reserved" && callbackExternalCounts.generated === 0,
    "external reservation never calls a generator callback even when one is supplied");

  for (const [label, faults, change] of [
    ["missing byte callback", {}, (_context, options) => { delete options.readBytes; }],
    ["unavailable byte transfer", {}, (_context, options) => {
      options.readBytes = async () => { throw Error("No external PNG byte bridge"); };
    }],
    ["failed transport readback", {preflightCorrupt: true}, () => {}],
    ["stopped cloud workflow", {}, context => { context.mock.cloud.workflow.state = "disabled_manually"; }]
  ]) {
    const context = await scenario("external-block-" + label, faults), counts = {reads: 0, generated: 0};
    const options = gateOptions(context, counts);
    change(context, options);
    await failure(() => context.service.reserveExternalGeneration(context.plan, options),
      label + " cannot grant an external generation reservation");
    assert(counts.generated === 0 && !context.mock.branches.has(context.branch) &&
      ![...context.mock.branches.keys()].some(branch => branch.startsWith(claimBranchPrefix)),
      label + " leaves no external generation claim or article candidate");
  }
  const lostExternal = await scenario("external-lost-claim", {claimLostResponse: true});
  const lostExternalCounts = {reads: 0, generated: 0};
  const lostExternalError = await failure(() => lostExternal.service.reserveExternalGeneration(lostExternal.plan,
    gateOptions(lostExternal, lostExternalCounts)), "lost external claim response cannot grant generation ownership");
  assert(lostExternalCounts.generated === 0 && lostExternalError.generationClaim.branch ===
    claimBranchPrefix + lostExternal.plan.identity &&
    lostExternal.mock.branches.has(lostExternalError.generationClaim.branch) &&
    !lostExternal.mock.branches.has(lostExternal.branch),
    "lost external claim response retains the durable claim without allowing generation or candidate staging");
  const freshLostExternal = stageAPI.create({runtime: lostExternal.measuredRuntime, github: lostExternal.mock.github, repository});
  await failure(() => freshLostExternal.reserveExternalGeneration(lostExternal.plan, gateOptions(lostExternal, lostExternalCounts)),
    "fresh service cannot replace the uncertain external reservation");
  assert(lostExternalCounts.generated === 0 && lostExternal.mock.calls.filter(call => call.name === "create_branch" &&
    call.args.branch_name.startsWith(claimBranchPrefix)).length === 1,
    "uncertain external ownership remains create-only across fresh services");

  const lateExternalStop = await scenario("external-stop-after-claim"), lateExternalCounts = {reads: 0, generated: 0};
  const lateExternalFetch = lateExternalStop.mock.github.fetch;
  lateExternalStop.mock.github.fetch = async args => {
    const result = await lateExternalFetch(args);
    if (args.url.endsWith("/git/ref/heads/" + claimBranchPrefix + lateExternalStop.plan.identity))
      lateExternalStop.mock.cloud.workflow.state = "disabled_manually";
    return result;
  };
  const lateExternalError = await failure(() => lateExternalStop.service.reserveExternalGeneration(lateExternalStop.plan,
    gateOptions(lateExternalStop, lateExternalCounts)), "stop after external claim creation blocks the external handoff");
  assert(lateExternalCounts.generated === 0 && lateExternalError.generationClaim.branch ===
    claimBranchPrefix + lateExternalStop.plan.identity &&
    lateExternalStop.mock.branches.has(lateExternalError.generationClaim.branch) &&
    !lateExternalStop.mock.branches.has(lateExternalStop.branch),
    "final external cloud guard preserves the claim without returning generation permission after a stop");

  // Count actual connector/transfer callbacks independently of the step API's
  // counters. Each invocation gets a cold service and JSON-restored checkpoint.
  const boundedJpeg = runtime.convert(pngBytes);
  const boundedRuntime = {...runtime, convert(input) {
    const source = typeof input === "string" ? runtime.fromBase64(input) : new Uint8Array(input);
    return {...boundedJpeg, source_png_sha256: runtime.sha256(source), receipt: null};
  }};
  const fourMiBPadding = new Uint8Array(4 * 1024 * 1024 - pngBytes.length - 12);
  const fourMiBBody = concat(runtime.utf8("npAD"), fourMiBPadding);
  const fourMiBPng = concat(pngBytes.subarray(0, pngBytes.length - 12),
    u32(fourMiBPadding.length), fourMiBBody, u32(crc(fourMiBBody)), pngBytes.subarray(pngBytes.length - 12));
  const restored = value => JSON.parse(JSON.stringify(value));
  function plainJSON(value) {
    if (value === null || typeof value === "string" || typeof value === "boolean") return true;
    if (typeof value === "number") return Number.isFinite(value);
    if (Array.isArray(value)) return value.every(plainJSON);
    return value && typeof value === "object" && Object.getPrototypeOf(value) === Object.prototype &&
      Object.values(value).every(plainJSON);
  }
  async function boundedContext(run, faults) {
    const context = await scenario(run, faults);
    context.measuredRuntime = boundedRuntime;
    context.stepCounters = {reads: 0, generated: 0};
    context.stepOptions = gateOptions(context, context.stepCounters);
    context.stepOptions.generate = async () => {
      context.stepCounters.generated++;
      throw Error("A bounded external reservation must not invoke a generator");
    };
    return context;
  }
  async function boundedCall(context, method, input, checkpoint) {
    const service = stageAPI.create({runtime: context.measuredRuntime, github: context.mock.github, repository});
    const beforeCalls = context.mock.calls.length, beforeReads = context.stepCounters.reads;
    let result;
    try {
      result = await service[method](context.plan, input, checkpoint === undefined ? undefined : restored(checkpoint));
      return result;
    } finally {
      const calls = context.mock.calls.slice(beforeCalls);
      const toolCalls = calls.length + context.stepCounters.reads - beforeReads;
      const mutations = calls.filter(call => call.name.startsWith("create_") || call.name === "update_ref").length;
      assert(toolCalls <= 15, method + " uses at most 15 actual callbacks in one cold invocation (actual " + toolCalls + ")");
      assert(mutations <= 2, method + " performs at most two mutation callbacks per invocation");
      if (method === "stageReservedStep" && checkpoint?.phase !== "reservation_verify")
        assert(calls.filter(call => call.name === "fetch_file").length <= 2,
          "bounded candidate staging reads at most two immutable/source files per invocation");
      if (result) {
        assert(result.tool_calls === toolCalls && result.mutations === mutations,
          method + " reports the independently measured callback and mutation counts");
        if (result.status === "pending") {
          assert(typeof result.phase === "string" && !!result.phase && plainJSON(result.checkpoint),
            method + " returns a plain JSON checkpoint and explicit pending phase");
          assert(JSON.stringify(restored(result.checkpoint)) === JSON.stringify(result.checkpoint),
            method + " checkpoint survives JSON serialization without typed arrays or dropped values");
        }
      }
    }
  }
  async function boundedDrive(context, method, input, checkpoint, onPending) {
    let previous;
    for (let step = 0; step < 256; step++) {
      const result = await boundedCall(context, method, input, checkpoint);
      if (result.status !== "pending") {
        assert(result.status === "reserved" || result.status === "saved", method + " ends with verified reservation or saved bytes");
        return {result, checkpoint, steps: step + 1};
      }
      const serialized = JSON.stringify(result.checkpoint);
      assert(serialized !== previous, method + " advances its persisted checkpoint on every pending step");
      previous = serialized;
      checkpoint = restored(result.checkpoint);
      if (onPending) await onPending(checkpoint, result);
    }
    assert(false, method + " completes within 256 explicit invocations");
  }
  const boundedCases = [];
  for (const [label, source, parts] of [["one-part", pngBytes, 1], ["four-MiB-eight-parts", fourMiBPng, 8]]) {
    const context = await boundedContext("bounded-" + label);
    const reservationRun = await boundedDrive(context, "reserveExternalGenerationStep", context.stepOptions);
    const reservation = reservationRun.result;
    assert(reservation.status === "reserved" && context.stepCounters.generated === 0 && context.stepCounters.reads === 1 &&
      !context.mock.branches.has(context.branch), label + " reserves once without generator calls or an article candidate");
    assert(!!reservationRun.checkpoint, label + " reaches its final reservation through serialized checkpoints");
    await failure(() => boundedCall(context, "reserveExternalGenerationStep", context.stepOptions, reservationRun.checkpoint),
      label + " cannot replay the final claim checkpoint to regain generation permission");
    const firstObservedAt = new Date().toISOString();
    const stageInput = {imageGenResult: {image_url: "data:image/png;base64," + runtime.toBase64(source)},
      observedAt: firstObservedAt, transportPreflight: reservation.transportPreflight,
      generationClaim: reservation.generationClaim};
    const originalStageInput = {...stageInput};
    let firstStageCheckpoint;
    const staged = await boundedDrive(context, "stageReservedStep", stageInput, undefined, checkpoint => {
      if (!firstStageCheckpoint) {
        firstStageCheckpoint = restored(checkpoint);
        delete stageInput.imageGenResult;
        delete stageInput.observedAt;
      }
    });
    const candidate = staged.result.receipt;
    const sourceParts = candidate.source.parts.map(part => context.mock.resolve(context.branch).get(part.path).bytes);
    assert(staged.result.status === "saved" && candidate.source.parts.length === parts &&
      equalBytes(concat.apply(null, sourceParts), source) && candidate.generation.observed_at === firstObservedAt &&
      candidate.identity === context.plan.identity,
      label + " confirms every original PNG byte and the first observation time only after full staged readback");
    assert(context.stepCounters.generated === 0 && context.mock.calls.every((call, index) =>
      !(call.name.startsWith("create_") || call.name === "update_ref") ||
      context.mock.calls[index - 1]?.args.url ===
        "https://api.github.com/repos/" + repository + "/actions/workflows/free-cpu-scenes.yml"),
    label + " keeps a live stop guard immediately before every write across cold steps");
    protectedUnchanged(context, "bounded " + label);
    boundedCases.push({context, reservation, reservationCheckpoint: reservationRun.checkpoint,
      stageInput: originalStageInput, firstStageCheckpoint, source});
  }

  const boundedOne = boundedCases[0], boundedOneContext = boundedOne.context;
  const wrongBoundedPlan = await boundedOneContext.service.prepare({...boundedOneContext.args,
    logicalRunId: "bounded-checkpoint-other-plan"});
  for (const [label, context, input, checkpoint] of [
    ["changed checksum", boundedOneContext, boundedOne.stageInput,
      {...boundedOne.firstStageCheckpoint, checksum: "0".repeat(64)}],
    ["checkpoint from another plan", {...boundedOneContext, plan: wrongBoundedPlan},
      boundedOne.stageInput, boundedOne.firstStageCheckpoint],
    ["changed original PNG", boundedOneContext,
      {...boundedOne.stageInput, imageGenResult: {image_url: "data:image/png;base64,AAAA"}}, boundedOne.firstStageCheckpoint],
    ["changed first observation time", boundedOneContext,
      {...boundedOne.stageInput, observedAt: new Date(Date.parse(boundedOne.stageInput.observedAt) + 1000).toISOString()},
      boundedOne.firstStageCheckpoint]
  ]) {
    const calls = context.mock.calls.length;
    await failure(() => boundedCall(context, "stageReservedStep", input, checkpoint),
      "bounded resume rejects " + label);
    assert(context.mock.calls.length === calls, label + " is rejected before all connector calls");
  }
  const writesBeforeBoundedProof = boundedOneContext.mock.writes().length;
  await failure(() => boundedDrive(boundedOneContext, "stageReservedStep", {...boundedOne.stageInput,
    transportPreflight: {...boundedOne.reservation.transportPreflight, receipt_sha256: "0".repeat(64)}}),
  "bounded stage rejects a tampered proof even when a saved candidate exists");
  assert(boundedOneContext.mock.writes().length === writesBeforeBoundedProof,
    "tampered bounded reservation proof never causes writes");
  const boundedClaim = boundedOne.reservation.generationClaim;
  boundedOneContext.mock.branches.set(boundedClaim.branch, "f".repeat(40));
  try {
    await failure(() => boundedDrive(boundedOneContext, "stageReservedStep", boundedOne.stageInput),
      "bounded stage rejects a changed live generation claim");
    assert(boundedOneContext.mock.writes().length === writesBeforeBoundedProof,
      "changed live bounded claim cannot create or replace candidate files");
  } finally {
    boundedOneContext.mock.branches.set(boundedClaim.branch, boundedClaim.commit_sha);
  }

  const boundedStopped = await boundedContext("bounded-stop-before-preflight");
  const beforeStoppedStep = await boundedCall(boundedStopped, "reserveExternalGenerationStep", boundedStopped.stepOptions);
  boundedStopped.mock.cloud.workflow.state = "disabled_manually";
  const stoppedBoundedWrites = boundedStopped.mock.writes().length;
  await failure(() => boundedDrive(boundedStopped, "reserveExternalGenerationStep", boundedStopped.stepOptions,
    beforeStoppedStep.checkpoint), "bounded reservation rechecks a live stop in a fresh service");
  assert(boundedStopped.mock.writes().length === stoppedBoundedWrites && boundedStopped.stepCounters.generated === 0 &&
    !boundedStopped.mock.branches.has(claimBranchPrefix + boundedStopped.plan.identity),
    "stopped bounded reservation grants neither a claim nor generation permission");

  const boundedLost = await boundedContext("bounded-lost-claim", {claimLostResponse: true});
  let beforeLostClaim;
  const lostBoundedError = await failure(() => boundedDrive(boundedLost, "reserveExternalGenerationStep",
    boundedLost.stepOptions, undefined, checkpoint => { beforeLostClaim = restored(checkpoint); }),
  "lost bounded claim creation response never returns generation permission");
  assert(boundedLost.mock.branches.has(claimBranchPrefix + boundedLost.plan.identity) &&
    !boundedLost.mock.branches.has(boundedLost.branch) && boundedLost.stepCounters.generated === 0,
    "uncertain bounded claim remains durable without generating or saving an image");
  await failure(() => boundedCall(boundedLost, "reserveExternalGenerationStep", boundedLost.stepOptions,
    lostBoundedError.checkpoint || beforeLostClaim), "uncertain bounded claim cannot be replayed into permission");
  assert(boundedLost.stepCounters.generated === 0, "bounded lost-response recovery never invokes a generator");

  const boundedRecovery = await boundedContext("bounded-stopped-upload");
  const recoveryReservation = (await boundedDrive(boundedRecovery, "reserveExternalGenerationStep",
    boundedRecovery.stepOptions)).result;
  const recoveryInput = {...boundedRecovery.input, observedAt: new Date().toISOString(),
    transportPreflight: recoveryReservation.transportPreflight, generationClaim: recoveryReservation.generationClaim};
  const boundedCreateBlob = boundedRecovery.mock.github.create_blob;
  let injectedBoundedStop = false;
  boundedRecovery.mock.github.create_blob = async args => {
    const result = await boundedCreateBlob(args);
    if (!injectedBoundedStop) {
      injectedBoundedStop = true;
      boundedRecovery.mock.cloud.workflow.state = "disabled_manually";
    }
    return result;
  };
  const blobsBeforeBoundedStop = boundedRecovery.mock.count("create_blob");
  const boundedUploadStop = await failure(() => boundedDrive(boundedRecovery, "stageReservedStep", recoveryInput),
    "a live stop between two bounded upload writes retains the original checkpoint");
  assert(boundedRecovery.mock.count("create_blob") === blobsBeforeBoundedStop + 1 &&
    boundedUploadStop.imageGenResult.image_url === recoveryInput.imageGenResult.image_url &&
    boundedUploadStop.observedAt === recoveryInput.observedAt && plainJSON(boundedUploadStop.checkpoint),
    "bounded upload stop retains first PNG/time and exactly one successful blob write");
  boundedRecovery.mock.cloud.workflow.state = "active";
  const boundedRecovered = (await boundedDrive(boundedRecovery, "stageReservedStep", {},
    restored(boundedUploadStop.checkpoint))).result;
  assert(boundedRecovered.status === "saved" &&
    boundedRecovery.mock.count("create_blob") === blobsBeforeBoundedStop + 3 &&
    boundedRecovered.receipt.generation.observed_at === recoveryInput.observedAt &&
    boundedRecovery.stepCounters.generated === 0,
    "fresh bounded recovery uses its retained bytes/time and skips the already uploaded source blob");

  const boundedCorrupt = await boundedContext("bounded-corrupt-final-readback");
  const corruptReservation = (await boundedDrive(boundedCorrupt, "reserveExternalGenerationStep", boundedCorrupt.stepOptions)).result;
  let corruptedBoundedSource = false;
  await failure(() => boundedDrive(boundedCorrupt, "stageReservedStep", {...boundedCorrupt.input,
    observedAt: new Date().toISOString(), transportPreflight: corruptReservation.transportPreflight,
    generationClaim: corruptReservation.generationClaim}, undefined, (_checkpoint, result) => {
    if (result.phase === "saved_readback" && !corruptedBoundedSource) {
      const stored = boundedCorrupt.mock.resolve(boundedCorrupt.branch).get(prefix + "source.png.part000");
      stored.bytes = stored.bytes.slice();stored.bytes[stored.bytes.length - 1] ^= 1;
      corruptedBoundedSource = true;
    }
  }), "bounded staging cannot report saved after source readback bytes change");
  assert(corruptedBoundedSource && boundedCorrupt.stepCounters.generated === 0,
    "bounded final verification checks the actual saved source SHA without generating a replacement");

  // The scheduled connector wraps payloads in .result rather than
  // .structuredContent. Apply that shape to every callback, not just reads.
  function resultWrappedMock(faults, decorate) {
    const mock = makeMock(faults), wrappedNames = new Set();
    for (const [name, callback] of Object.entries(mock.github)) mock.github[name] = async args => {
      wrappedNames.add(name);
      const response = await callback(args);
      const wrapped = {result: response.structuredContent ?? response};
      return decorate ? decorate(wrapped, name) : wrapped;
    };
    return {...mock, wrappedNames};
  }
  const wrappedMock = resultWrappedMock();
  const wrappedService = stageAPI.create({runtime, github: wrappedMock.github, repository});
  const wrappedArgs = {slug, articleSha256, baseCommitSha, baseTreeSha, prompt, logicalRunId: "result-wrapper-save"};
  const wrappedPlan = await wrappedService.prepare(wrappedArgs);
  assert((await wrappedService.probe(wrappedPlan)).status === "absent" && wrappedMock.writes().length === 0,
    "result-wrapped precise NOT_FOUND still establishes the exact missing candidate ref without writes");
  const wrappedSaved = await wrappedService.stage(wrappedPlan, {imageGenResult: {image_url: pngDataURL}, observedAt});
  const wrappedReceipt = wrappedSaved.receipt;
  assert(wrappedSaved.status === "saved" && wrappedReceipt.source.sha256 === runtime.sha256(pngBytes) &&
    equalBytes(wrappedMock.resolve(wrappedPlan.branch).get(wrappedReceipt.source.parts[0].path).bytes, pngBytes),
    "result-wrapped prepare, cloud guards and Git object writes preserve the exact original PNG");
  assert(["fetch_file", "fetch", "create_blob", "create_tree", "create_commit", "create_branch"]
    .every(name => wrappedMock.wrappedNames.has(name)),
  "result-wrapped staging exercises reads, guard fetches and every candidate mutation callback");
  protectedUnchanged({mock: wrappedMock, branch: wrappedPlan.branch}, "result-wrapped stage");

  for (const [location, field, value] of [
    ["outer", "isError", true], ["outer", "error_code", "UNAVAILABLE"], ["outer", "error", "Outer wrapper failure"],
    ["inner", "isError", true], ["inner", "error_code", "UNAVAILABLE"], ["inner", "error", "Inner wrapper failure"]
  ]) {
    const errorMock = resultWrappedMock({}, (response, name) => {
      if (name !== "fetch") return response;
      if (location === "outer") return {...response, [field]: value};
      return {result: {...response.result, [field]: value}};
    });
    const service = stageAPI.create({runtime, github: errorMock.github, repository});
    const plan = await service.prepare({...wrappedArgs, logicalRunId: "result-wrapper-" + location + "-" + field});
    const error = await failure(() => service.stage(plan, {imageGenResult: {image_url: pngDataURL}, observedAt}),
      "result wrapper rejects " + location + " " + field + " before candidate writes");
    assert(error.message.includes("Cloud image operations stopped:") && errorMock.writes().length === 0,
      location + " " + field + " cannot authorize a write through an otherwise valid wrapped guard payload");
  }
  const ambiguousWrapped = resultWrappedMock({ambiguous404: true});
  const ambiguousWrappedService = stageAPI.create({runtime, github: ambiguousWrapped.github, repository});
  const ambiguousWrappedPlan = await ambiguousWrappedService.prepare({...wrappedArgs, logicalRunId: "result-wrapper-ambiguous-missing"});
  await failure(() => ambiguousWrappedService.stage(ambiguousWrappedPlan,
    {imageGenResult: {image_url: pngDataURL}, observedAt}),
  "result-wrapped generic NOT_FOUND is not mistaken for exact missing-ref evidence");
  assert(ambiguousWrapped.writes().length === 0, "ambiguous wrapped missing-ref errors remain fail-closed without writes");

  // Compact checkpoints contain references/hashes, with a fresh original-file
  // reader on every cold invocation instead of any cross-cell byte store.
  async function compactContext(run, faults) {
    const context = await boundedContext(run, faults);
    context.probeRef = "/synthetic-existing/" + run + "-transport.png";
    context.originalRef = "/synthetic-existing/" + run + "-original.png";
    context.originalBytes = fourMiBPng;
    context.refsRead = [];
    context.compactMax = {reserveExternalGenerationStep: 0, stageReservedStep: 0};
    return context;
  }
  function compactInput(context, metadata) {
    return {...restored(metadata), async readBytes(reference) {
      context.stepCounters.reads++;context.refsRead.push(reference);
      demand(reference === context.probeRef || reference === context.originalRef,
        "compact reads must use the actual provided original/probe reference");
      return {image_url: "data:image/png;base64," + runtime.toBase64(
        reference === context.probeRef ? pngBytes : context.originalBytes)};
    }};
  }
  function compactReservationOptions(context) {
    return {compact: true, probeSource: context.probeRef, probeSourceRef: context.probeRef,
      expectedProbeSha256: runtime.sha256(pngBytes), route: "synthetic original-file read to bounded fake GitHub"};
  }
  function checkCompactCheckpoint(context, method, checkpoint) {
    assert(checkpoint.version === 2 && plainJSON(checkpoint), "compact checkpoint is version two with only JSON values");
    const text = JSON.stringify(checkpoint), size = runtime.utf8(text).length;
    context.compactMax[method] = Math.max(context.compactMax[method], size);
    assert(size <= 50 * 1024, "compact checkpoint stays within 50 KiB (actual " + size + " bytes)");
    assert(!/data:image\//i.test(text) && !text.includes(pngBase64.slice(0, 80)) &&
      !text.includes(boundedJpeg.base64.slice(0, 80)) && !text.includes("A".repeat(128)),
    "compact checkpoint contains no PNG/JPEG data URL or base64 image payload");
    assert(JSON.stringify(restored(checkpoint)) === text, "compact checkpoint serializes without callbacks or typed arrays");
  }
  async function compactCall(context, method, metadata, checkpoint) {
    const service = stageAPI.create({runtime: context.measuredRuntime, github: context.mock.github, repository});
    const beforeCalls = context.mock.calls.length, beforeReads = context.stepCounters.reads;
    let result;
    try {
      result = await service[method](context.plan, compactInput(context, metadata),
        checkpoint === undefined ? undefined : restored(checkpoint));
      if (result.status === "pending") checkCompactCheckpoint(context, method, result.checkpoint);
      return result;
    } catch (error) {
      if (error.checkpoint) checkCompactCheckpoint(context, method, error.checkpoint);
      throw error;
    } finally {
      const calls = context.mock.calls.slice(beforeCalls), toolCalls = calls.length + context.stepCounters.reads - beforeReads;
      const mutations = calls.filter(call => call.name.startsWith("create_") || call.name === "update_ref").length;
      assert(toolCalls <= 15 && mutations <= 2,
        "compact cold step respects 15 actual callbacks and two mutations (actual " + toolCalls + "/" + mutations + ")");
      if (result) assert(result.tool_calls === toolCalls && result.mutations === mutations,
        "compact counters include actual connector and original-file rehydration callbacks");
    }
  }
  async function compactDrive(context, method, firstInput, checkpoint) {
    let previous;
    for (let step = 0; step < 256; step++) {
      const metadata = step === 0 && checkpoint === undefined ? firstInput :
        method === "reserveExternalGenerationStep" ? compactReservationOptions(context) :
          {compact: true, originalSourceRef: context.originalRef};
      const result = await compactCall(context, method, metadata, checkpoint);
      if (result.status !== "pending") {
        assert(result.status === "reserved" || result.status === "saved", "compact steps finish with verified reservation or candidate");
        return {result, checkpoint};
      }
      const serialized = JSON.stringify(result.checkpoint);
      assert(serialized !== previous, "compact step advances its restored metadata checkpoint");
      previous = serialized;checkpoint = restored(result.checkpoint);
    }
    assert(false, "compact operation finishes within 256 explicit invocations");
  }
  const compact = await compactContext("compact-four-MiB");
  const compactReserved = await compactDrive(compact, "reserveExternalGenerationStep", compactReservationOptions(compact));
  assert(compactReserved.result.status === "reserved" && !compact.mock.branches.has(compact.branch),
    "compact reservation tests synthetic transport without saving an article candidate");
  await failure(() => compactCall(compact, "reserveExternalGenerationStep", compactReservationOptions(compact),
    compactReserved.checkpoint), "compact final-claim replay cannot regain generation permission");
  const compactFirstTime = new Date().toISOString();
  const compactStageInput = {compact: true, originalSourceRef: compact.originalRef,
    imageGenResult: {image_url: "data:image/png;base64," + runtime.toBase64(fourMiBPng)}, observedAt: compactFirstTime,
    transportPreflight: compactReserved.result.transportPreflight, generationClaim: compactReserved.result.generationClaim};
  const compactFirst = await compactCall(compact, "stageReservedStep", compactStageInput);
  const compactCaptured = await compactCall(compact, "stageReservedStep", {compact: true, originalSourceRef: compact.originalRef},
    compactFirst.checkpoint);
  assert(compactCaptured.status === "pending" && !!compactCaptured.checkpoint.sourceSha,
    "compact stage pins the original PNG hash before candidate writes");
  for (const [label, metadata] of [
    ["original reference", {compact: true, originalSourceRef: compact.originalRef + "-changed"}],
    ["first observation time", {compact: true, originalSourceRef: compact.originalRef,
      observedAt: new Date(Date.parse(compactFirstTime) + 1000).toISOString()}]
  ]) {
    const calls = compact.mock.calls.length, reads = compact.stepCounters.reads;
    await failure(() => compactCall(compact, "stageReservedStep", metadata, compactCaptured.checkpoint),
      "compact retry rejects a changed " + label);
    assert(compact.mock.calls.length === calls && compact.stepCounters.reads === reads,
      "changed compact " + label + " is rejected before connector or original-file reads");
  }
  const beforeChangedCompactSource = compact.mock.writes().length;
  compact.originalBytes = pngBytes;
  await failure(() => compactDrive(compact, "stageReservedStep", {}, compactCaptured.checkpoint),
    "compact rehydration rejects changed original PNG bytes");
  assert(compact.mock.writes().length === beforeChangedCompactSource, "changed compact original SHA is rejected before writes");
  compact.originalBytes = fourMiBPng;
  const originalCompactRuntime = compact.measuredRuntime;
  compact.measuredRuntime = {...boundedRuntime, convert(input) {
    const converted = boundedRuntime.convert(input), bytes = converted.bytes.slice();bytes[bytes.length - 1] ^= 1;
    return {...converted, bytes, base64: runtime.toBase64(bytes), sha256: runtime.sha256(bytes)};
  }};
  await failure(() => compactDrive(compact, "stageReservedStep", {}, compactCaptured.checkpoint),
    "compact rehydration rejects a JPEG differing from its pinned conversion SHA");
  assert(compact.mock.writes().length === beforeChangedCompactSource, "changed compact JPEG correspondence is rejected before writes");
  compact.measuredRuntime = originalCompactRuntime;
  const compactSaved = (await compactDrive(compact, "stageReservedStep", {}, compactCaptured.checkpoint)).result;
  const compactParts = compactSaved.receipt.source.parts.map(part => compact.mock.resolve(compact.branch).get(part.path).bytes);
  assert(compactSaved.status === "saved" && compactSaved.receipt.source.parts.length === 8 &&
    equalBytes(concat.apply(null, compactParts), fourMiBPng) && compactSaved.receipt.generation.observed_at === compactFirstTime,
    "compact cold JSON resumes preserve exactly 4 MiB of original PNG and its first time across eight parts");
  assert(compact.refsRead.includes(compact.probeRef) && compact.refsRead.includes(compact.originalRef) &&
    compact.stepCounters.generated === 0, "compact resumes reread provided file references without generating or cross-cell bytes");
  for (const [method, maximum] of Object.entries(compact.compactMax))
    assert(maximum > 0 && maximum <= 50 * 1024, "compact " + method + " checkpoint maximum: " + maximum + " bytes");
  protectedUnchanged(compact, "compact four-MiB stage");
  const immutableService = stageAPI.create({runtime: compact.measuredRuntime, github: compact.mock.github, repository});
  const immutableImages = await immutableService.load(compact.plan, compactSaved.commitSha);
  assert(immutableImages.receipt_sha256 === compactSaved.receipt_sha256 &&
    runtime.sha256(runtime.fromBase64(immutableImages.sourcePngDataUrl)) === runtime.sha256(fourMiBPng),
    "separate visual loading reads the verified immutable candidate commit and exact original bytes");
  const beforeInvalidLoad = compact.mock.calls.length;
  await failure(() => immutableService.load(compact.plan, "https://untrusted.invalid/image"),
    "immutable visual loader rejects a URL as a candidate ref");
  assert(compact.mock.calls.length === beforeInvalidLoad, "invalid visual ref is rejected before connector calls");
  const writesBeforeCompactCap = compact.mock.writes().length;
  compact.originalBytes = concat(fourMiBPng, new Uint8Array([0]));
  await failure(() => compactDrive(compact, "stageReservedStep", {...compactStageInput,
    imageGenResult: {image_url: "data:image/png;base64," + runtime.toBase64(compact.originalBytes)}}),
  "compact original PNG larger than 4 MiB is rejected");
  assert(compact.mock.writes().length === writesBeforeCompactCap, "compact source cap rejects before writes even with a saved identity");
  const compactLost = await compactContext("compact-lost-claim", {claimLostResponse: true});
  const compactLostError = await failure(() => compactDrive(compactLost, "reserveExternalGenerationStep",
    compactReservationOptions(compactLost)), "lost compact claim response cannot grant generation permission");
  assert(compactLost.mock.branches.has(claimBranchPrefix + compactLost.plan.identity) &&
    !compactLost.mock.branches.has(compactLost.branch), "lost compact claim stays create-only and unconfirmed");
  await failure(() => compactCall(compactLost, "reserveExternalGenerationStep", compactReservationOptions(compactLost),
    compactLostError.checkpoint), "lost compact claim checkpoint cannot be replayed into permission");

  return {passed: checks.length, checks};
};
if (typeof module !== "undefined" && module.exports) module.exports = NativeImageStageTests;
