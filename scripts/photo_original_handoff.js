/* Keep an existing producer's original PNGs on an isolated photo plan branch.
 * Tool payloads stay inside code mode. Release preparation creates tree entries without publishing or accepting a cut.
 */
var PhotoOriginalHandoff = (() => {
  "use strict";
  const REPO = "RollNuts/mihassei-news";
  const GIT = /^[0-9a-f]{40}$/, HASH = /^[0-9a-f]{64}$/;
  const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
  const PROBE = {ref:"1d9b571b914d6c323a3821e46d23ae0d8e354f95", path:"assets/favicon-192.png",
    blob:"6608aa987c11d56ea9d7910eb4c0770bb529da9d", sha256:"3713afa375160c13556a90f2347b151725ac5922498211d2d980c7c58176c40e",
    bytes:3395};
  const PART = 512 * 1024, MAX = 8 * 1024 * 1024;
  function requireThat(condition, message) {
    if (!condition) throw Error(message);
  }
  function payload(reply) {
    const data = reply?.structuredContent ?? reply?.result ?? reply;
    const code = data?.error_code ?? reply?.error_code;
    requireThat(data && !reply.isError && !data.isError && !reply.error && !data.error &&
      !reply.error_code && !data.error_code, "Photo transport tool failed" + (/^[a-zA-Z0-9_-]{1,48}$/.test(String(code ?? "")) ? " (" + code + ")" : ""));
    return data;
  }
  function gitId(data, kind) {
    const sha = data.sha ?? data[kind + "_sha"] ?? data[kind]?.sha;
    requireThat(GIT.test(sha), "Invalid Git " + kind + " response");
    return sha;
  }
  function planPrefix(issue) {
    requireThat(typeof issue === "string" && issue.length <= 100 && SLUG.test(issue), "Invalid issue slug");
    return "maintenance/photo-issue-plans/" + issue + "/";
  }
  function directory(issue) {
    return planPrefix(issue) + "originals";
  }
  function partPath(dir, sha, index) {
    return dir + "/" + sha + ".png.part" + String(index).padStart(3, "0");
  }
  function matchesProducer(packet, producer) {
    return producer?.model_id === packet.model_id && producer.source_chat_url === packet.source_chat_url;
  }
  function similarSourceGroups(handoff, rows) {
    const groups = handoff.editorial?.near_similar_source_ordinal_groups;
    if (groups === undefined) return [];
    requireThat(Array.isArray(groups), "Invalid near-similar group list");
    return groups.map(group => {
      requireThat(Array.isArray(group) && group.length >= 2, "Invalid near-similar group");
      const hashes = group.map(ordinal => {
        const matches = rows.filter(row => row.source_ordinal === ordinal);
        requireThat(Number.isSafeInteger(ordinal) && ordinal > 0 && matches.length === 1,
          "Unknown or ambiguous near-similar source ordinal");
        const hash = handoff.kind === "photo_original_recovery_handoff"
          ? matches[0].source_sha256 : matches[0].source?.sha256;
        requireThat(typeof hash === "string" && HASH.test(hash), "Missing near-similar source hash");
        return hash;
      });
      requireThat(new Set(hashes).size === group.length, "Repeated near-similar source");
      return hashes;
    });
  }
  function checkDistinctSelection(groups, selectedHashes) {
    requireThat(groups.every(group => group.filter(hash => selectedHashes.has(hash)).length <= 1),
      "Near-similar photos must not be selected together in this feature");
  }
  function validReviewSize(jpeg) {
    return Number.isInteger(jpeg.bytes) && jpeg.bytes >= 16000 && jpeg.bytes <= 700 * 1024 &&
      [jpeg.width, jpeg.height].every(n => Number.isInteger(n) && n >= 320 && n <= 1200);
  }
  function isCandidatePath(path, prefix) {
    return typeof path === "string" && path.startsWith(prefix) &&
      !/[\\\x00-\x1f\x7f]/.test(path) &&
      path.slice(prefix.length).split("/").every(part => part && part !== "." && part !== "..");
  }
  function verifiedFile(file, observed, prefix) {
    requireThat(isCandidatePath(file?.path, prefix) && typeof observed?.blob === "string" && GIT.test(observed.blob) &&
      typeof file.sha256 === "string" && HASH.test(file.sha256) && ["path", "sha256", "bytes", "width", "height"].every(
        field => file[field] === observed[field]) && (file.blob === undefined || file.blob === observed.blob),
    "Candidate file differs from the verified readback");
  }
  function safeBasename(name, suffix) {
    return typeof name === "string" && name.length <= 510 && [...name].length <= 255 &&
      !/^\p{White_Space}|\p{White_Space}$/u.test(name) &&
      !name.startsWith(".") && !/[/\\:\x00-\x1f\x7f]/.test(name) && suffix.test(name);
  }
  function verifiedArchive(source, observed) {
    const archive = source.archive, saved = observed.archive;
    const fields = ["format", "filename", "sha256", "bytes", "member"];
    requireThat([archive, saved].every(record => record && typeof record === "object" &&
      !Array.isArray(record) && Object.keys(record).length === fields.length &&
      fields.every(key => Object.hasOwn(record, key))) &&
      fields.every(key => archive[key] === saved[key]), "Archive differs from the verified source member");
    requireThat(archive.format === "zip" && typeof archive.sha256 === "string" && HASH.test(archive.sha256) &&
      Number.isSafeInteger(archive.bytes) && archive.bytes > 0 && safeBasename(archive.filename, /\.zip$/i) &&
      safeBasename(archive.member, source.format === "jpeg" ? /\.jpe?g$/i : /\.png$/i),
    "Invalid Library archive or original member");
    return {...archive};
  }
  function releaseSource(candidate, observed) {
    const {source, source_ordinal, source_chat_url} = candidate;
    requireThat(source.url === observed.url && typeof source.url === "string" &&
      source.generation_prompt_status === "not_retained" && source.generation_prompt === null,
    "Expected an original with its observed source reference");
    const library = /^https:\/\/chatgpt\.com\/(?:space\/file\/(libfile_[A-Za-z0-9_-]+)|library\?file_id=(libfile_[A-Za-z0-9_-]+)&account_id=personal)$/.exec(source.url);
    const shared = /^https:\/\/chatgpt\.com\/library\/share\/(libfile_[A-Za-z0-9_-]+)(?:\?account_id=personal)?$/.exec(source.url);
    const retained = {};
    if ("archive" in source || "archive" in observed) {
      requireThat(library?.[2], "An archive requires the observed Library query URL");
      retained.archive = verifiedArchive(source, observed);
    }
    if (library || shared) {
      const references = [source, observed];
      requireThat(references.every(ref => !("image_number" in ref) && !("turn_key" in ref)),
        "Library original cannot use a conversation ordinal");
      if (shared) {
        requireThat(references.every(ref => !("library_file_id" in ref) || ref.library_file_id === shared[1]),
          "Library ID differs from the observed share URL");
      } else {
        requireThat(source.library_file_id === (library[1] || library[2]) && source.library_file_id === observed.library_file_id &&
          source.generation_turn_status === "not_retained" && source.generation_turn === null,
        "Library original differs from the observed individual file");
      }
    } else {
      requireThat(source.url === source_chat_url && typeof source.turn_key === "string" && UUID.test(source.turn_key) &&
        source.turn_key !== "00000000-0000-0000-0000-000000000000" && observed.turn_key === source.turn_key &&
        source.image_number === source_ordinal && observed.image_number === source_ordinal &&
        [source, observed].every(ref => !("library_file_id" in ref)),
      "Conversation original differs from the observed producer turn and image");
    }
    for (const key of ["format", "sha256", "bytes", "width", "height", "url", "library_file_id", "file_id",
      "file_name", "library_file_version", "turn_key", "image_number", "generation_turn", "generation_turn_status",
      "generation_prompt", "generation_prompt_status"]) {
      if (Object.hasOwn(source, key)) retained[key] = source[key];
    }
    return retained;
  }
  function validPhotoTransform(transform) {
    return transform?.max_edge === 1200 && transform.jpeg_quality === 84 && transform.geometry === "preserve-aspect";
  }
  function pinnedJpegEncoder(details) {
    return details?.encoder_ref === "1031ccd7a63c641c16afb2430279d1598530a85c" &&
      details.resample === "bilinear" && details.mode === "RGB";
  }
  function validateReleaseEncoding(candidate) {
    const {transform, encoding_details: details} = candidate;
    requireThat(validPhotoTransform(transform) && Object.keys(transform).length === 4, "Invalid supplied photo transform");
    if (transform.encoder === "macOS sips" && details === undefined) return;
    if (transform.encoder === "jpeg-js" && candidate.source.format === "png" &&
      pinnedJpegEncoder(details) && Object.keys(details).length === 3) return;
    requireThat(transform.encoder === "Pillow" && typeof details?.pillow_version === "string" &&
      /^[1-9][0-9]*\.[0-9]+\.[0-9]+$/.test(details.pillow_version) && details.resample === "LANCZOS" &&
      details.mode === "RGB" && details.optimize === true && details.progressive === false &&
      details.subsampling === 0 && Object.keys(details).length === 6, "Invalid supplied photo encoding details");
  }
  function create({runtime, github, cloudStatus, readCandidatePng, readWeeklyPng}) {
    const git = (name, args) => github[name]({repository_full_name:REPO, ...args}).then(payload);
    const hashText = value => runtime.sha256(runtime.utf8(value));
    async function get(path) {
      const reply = payload(await github.fetch({url:"https://api.github.com/repos/" + REPO + path}));
      return typeof reply.content === "string" ? JSON.parse(reply.content) : reply;
    }
    async function guard() {
      const status = await cloudStatus();
      requireThat(status?.state === "active" && status.public === true && GIT.test(status.main_commit_sha) &&
        Number.isSafeInteger(status.jpeg_bytes) && status.jpeg_bytes >= 0 && status.jpeg_bytes <= 1536 * 1024 * 1024,
      "Cloud image operations stopped");
      return status.main_commit_sha;
    }
    async function write(name, args) {
      await guard();
      return git(name, args);
    }
    function sourceReference(expectedSha, start, count) {
      requireThat(HASH.test(expectedSha) && Number.isInteger(start) && start >= 0 && (count === 1 || count === 2), "Invalid source reference");
    }
    async function uploadPng(dataUrl, expectedSha, start, count, metadata = null) {
      requireThat(typeof dataUrl === "string" && dataUrl.startsWith("data:image/png;base64,") &&
        dataUrl.length > 22 && dataUrl.length <= 22 + 4 * Math.ceil(MAX/3), "Unexpected original PNG data URL");
      const bytes = runtime.fromBase64(dataUrl);
      requireThat(bytes.length > 0 && bytes.length <= MAX && dataUrl.length === 22 + 4 * Math.ceil(bytes.length/3) &&
        (!metadata || bytes.length === metadata.bytes) && runtime.sha256(bytes) === expectedSha, "Original PNG changed");
      const {width, height} = runtime.inspectPng(bytes);
      requireThat((!metadata || (width === metadata.width && height === metadata.height)) &&
        start * PART < bytes.length, "Invalid PNG dimensions or offset");
      const parts = [];
      const progress = () => ({source_sha256:expectedSha, source_bytes:bytes.length, width, height,
        parts:parts.map(part => ({...part})),
        next:(start + parts.length) * PART < bytes.length ? start + parts.length : null});
      try {
        for (let index = start; index < start + count && index * PART < bytes.length; index++) {
          const chunk = bytes.subarray(index * PART, (index + 1) * PART);
          const blob = await write("create_blob", {content:runtime.toBase64(chunk), encoding:"base64"});
          parts.push({index, blob:gitId(blob, "blob"), sha256:runtime.sha256(chunk)});
        }
      } catch (error) {
        // Only acknowledged parts are recoverable here; a lost blob response is not success.
        const failure = error instanceof Error ? error : Error(String(error));
        failure.partial = progress();
        throw failure;
      }
      return progress();
    }
    async function upload(file, expectedSha, start = 0, count = 1) {
      sourceReference(expectedSha, start, count);
      const png = payload(await readCandidatePng({file}));
      requireThat(png.status === "read" && png.mime_type === "image/png" && png.sha256 === expectedSha &&
        Number.isInteger(png.bytes) && png.bytes > 0 && png.bytes <= MAX &&
        typeof png.image_url === "string" && png.image_url.startsWith("data:image/png;base64,") &&
        png.image_url.length === 22 + 4 * Math.ceil(png.bytes/3), "Unexpected original PNG response");
      return uploadPng(png.image_url, expectedSha, start, count, png);
    }
    async function uploadDataUrl(dataUrl, expectedSha, start = 0, count = 1) {
      sourceReference(expectedSha, start, count);
      return uploadPng(dataUrl, expectedSha, start, count);
    }
    async function checkOriginalTransport({route, file} = {}) {
      let operation = "select_route";
      try {
        requireThat(route === "inline_png" || route === "file", "Missing original PNG route");
        if (route === "file") requireThat(file && typeof readCandidatePng === "function", "Missing file handle or readCandidatePng");
        // The public fixture is already reachable. Its guarded write deduplicates to the same Git blob.
        async function readFixture() {
          const ref = await git("fetch_file", {ref:PROBE.ref, path:PROBE.path, encoding:"base64"});
          requireThat(ref.sha === PROBE.blob && ref.encoding === "base64" && typeof ref.content === "string" &&
            ref.content.length <= 8192, "Unexpected probe file");
          const bytes = runtime.fromBase64(ref.content);
          requireThat(bytes.length === PROBE.bytes && runtime.sha256(bytes) === PROBE.sha256, "Probe bytes changed");
          return bytes;
        }
        operation = "fetch_file";
        const bytes = await readFixture();
        operation = route === "file" ? "readCandidatePng/upload" : "uploadDataUrl";
        const saved = route === "file" ? await upload(file, PROBE.sha256) :
          await uploadDataUrl("data:image/png;base64," + runtime.toBase64(bytes), PROBE.sha256);
        requireThat(saved.next === null && saved.parts.length === 1 && saved.parts[0].blob === PROBE.blob, "Unconfirmed probe blob");
        operation = "fetch_file_readback";
        await readFixture();
        return {status:"verified", route, sha256:PROBE.sha256, blob:PROBE.blob, bytes:PROBE.bytes};
      } catch (error) {
        // Do not echo unknown connector payloads or file references into a task log.
        const detail = String(error?.message ?? "");
        const message = detail.startsWith("Cloud image operations stopped:") ? "Cloud image operations stopped" : detail;
        const safe = /^(?:Missing original PNG route|Missing file handle or readCandidatePng|Unexpected probe file|Probe bytes changed|Unconfirmed probe blob|Cloud image operations stopped|Photo transport tool failed(?: [(][a-zA-Z0-9_-]{1,48}[)])?|Unexpected original PNG response|Original PNG changed|Invalid Git blob response)$/;
        return {status:"blocked", route:["inline_png","file"].includes(route) ? route : null, operation,
          reason:safe.test(message) ? message : "Transport callback failed", ...(Number.isInteger(error?.status) ? {http_status:error.status} : {})};
      }
    }
    function validate(packet, partial = false) {
      const {issue, model_id, source_chat_url, delivery_turn, assets} = packet;
      directory(issue);
      requireThat(SLUG.test(model_id) && model_id.length < 80 &&
        typeof source_chat_url === "string" && source_chat_url.startsWith("https://chatgpt.com/c/") &&
        UUID.test(source_chat_url.slice(22)) && UUID.test(delivery_turn), "Invalid producer or delivery reference");
      requireThat(Array.isArray(assets) && assets.length >= 1 && assets.length <= 30, "Invalid original count");
      const hashes = new Set(), ordinals = new Set();
      for (const asset of assets) {
        const position = (asset.generation_turn ?? delivery_turn) + "/" + asset.source_ordinal;
        requireThat(HASH.test(asset.source_sha256) && !hashes.has(asset.source_sha256) &&
          Number.isInteger(asset.source_ordinal) && asset.source_ordinal > 0 && !ordinals.has(position) &&
          (asset.generation_turn == null || UUID.test(asset.generation_turn)) &&
          Number.isSafeInteger(asset.source_bytes) && asset.source_bytes > 0 && asset.source_bytes <= MAX &&
          Number.isInteger(asset.width) && asset.width > 0 && Number.isInteger(asset.height) && asset.height > 0 &&
          asset.width * asset.height <= 32000000 && Array.isArray(asset.parts) &&
          (partial ? asset.parts.length <= Math.ceil(asset.source_bytes/PART) :
            asset.parts.length === Math.ceil(asset.source_bytes/PART)) &&
          asset.parts.every((part, i) => part.index === i && GIT.test(part.blob) && HASH.test(part.sha256)),
        "Incomplete, duplicate or invalid original");
        hashes.add(asset.source_sha256);
        ordinals.add(position);
      }
    }
    function receipt(packet, partial = false) {
      validate(packet, partial);
      return {version:1, status:partial ? "originals_transfer_in_progress" : "originals_only_unreviewed", issue:packet.issue, model_id:packet.model_id,
        source_chat_url:packet.source_chat_url, delivery_turn:packet.delivery_turn,
        assets:packet.assets.map(a => ({source_sha256:a.source_sha256, source_bytes:a.source_bytes,
          width:a.width, height:a.height, source_ordinal:a.source_ordinal, generation_turn:a.generation_turn ?? null,
          parts:a.parts.map(p => ({index:p.index, blob:p.blob, sha256:p.sha256}))}))};
    }
    const pendingDirectory = issue => directory(issue) + "-pending";
    const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
    async function treeFiles(sha) {
      const files = await get("/git/trees/" + sha + "?recursive=1");
      requireThat(files.sha === sha && files.truncated === false && Array.isArray(files.tree), "Invalid parent files");
      return files;
    }
    async function parentFiles(base) {
      requireThat(GIT.test(base), "Invalid base commit");
      const parent = await get("/git/commits/" + base);
      requireThat(parent.sha === base && GIT.test(parent.tree?.sha), "Invalid parent tree");
      return {parent, files:await treeFiles(parent.tree.sha)};
    }
    async function checkpointRecord(issue, commit, expectedHash = null, knownFiles = null) {
      const dir = pendingDirectory(issue);
      requireThat(GIT.test(commit) && (expectedHash === null || HASH.test(expectedHash)), "Invalid checkpoint reference");
      const entry = await git("fetch_file", {ref:commit, path:dir + "/checkpoint.json", encoding:"utf-8"});
      requireThat(entry.encoding === "utf-8" && typeof entry.content === "string" &&
        (expectedHash === null || hashText(entry.content) === expectedHash), "Original checkpoint changed");
      const record = JSON.parse(entry.content);
      validate(record, true);
      requireThat(record.version === 1 && record.status === "originals_transfer_in_progress" &&
        record.issue === issue && same(record, receipt(record, true)), "Invalid original checkpoint");
      const files = knownFiles ?? (await parentFiles(commit)).files;
      const parts = record.assets.flatMap(asset => asset.parts.map(part => ({
        path:partPath(dir, asset.source_sha256, part.index), sha:part.blob})));
      const saved = files.tree.filter(file => file.type === "blob" && file.path.startsWith(dir + "/"));
      requireThat(saved.length === parts.length + 1 && saved.some(file => file.path === dir + "/checkpoint.json") &&
        parts.every(part => saved.some(file => file.path === part.path && file.sha === part.sha && file.mode === "100644")),
      "Checkpoint part tree changed");
      return {record, checkpoint_sha256:hashText(entry.content)};
    }
    function extendsCheckpoint(previous, next) {
      const identity = record => [record.issue, record.model_id, record.source_chat_url, record.delivery_turn];
      requireThat(same(identity(previous), identity(next)) && next.assets.length >= previous.assets.length,
        "Checkpoint identity or originals changed");
      for (let i = 0; i < previous.assets.length; i++) {
        const before = previous.assets[i], after = next.assets[i];
        const fixed = asset => ({...asset, parts:[]});
        requireThat(same(fixed(before), fixed(after)) && after.parts.length >= before.parts.length &&
          before.parts.every((part, index) => same(part, after.parts[index])), "Checkpoint originals are append-only");
      }
    }
    async function originalEntries(packet, base, partial, files) {
      const dir = directory(packet.issue);
      const record = receipt(packet, partial);
      requireThat(!files.tree.some(f => f.path === dir || f.path.startsWith(dir + "/")), "Existing originals must be recovered first");
      const pending = pendingDirectory(packet.issue);
      if (files.tree.some(file => file.path === pending || file.path.startsWith(pending + "/"))) {
        const prior = (await checkpointRecord(packet.issue, base, null, files)).record;
        if (partial) extendsCheckpoint(prior, record);
        else requireThat(same(receipt(prior), record), "Final originals differ from checkpoint");
      }
      const destination = partial ? pending : dir;
      const entries = record.assets.flatMap(a => a.parts.map(p => ({
        path:partPath(destination, a.source_sha256, p.index), mode:"100644", type:"blob", sha:p.blob})));
      entries.push({path:destination + (partial ? "/checkpoint.json" : "/receipt.json"), mode:"100644", type:"blob", content:JSON.stringify(record)});
      return entries;
    }
    async function checkProducer(packet) {
      const draft = await git("fetch_file", {ref:"cast-source-drafts", path:"drafts/cast-voices/" + packet.model_id + ".json", encoding:"utf-8"});
      const producer = JSON.parse(draft.content);
      requireThat(matchesProducer(packet, producer), "Wrong producer chat");
      return producer;
    }
    async function prepareRecordTree(packet, expectedHead, partial, reuse=false) {
      validate(packet, partial);
      await checkProducer(packet);
      const branch = expectedHead === null ? "main" : "photo-issue-plan/" + packet.issue;
      const head = await get("/branches/" + branch);
      const base = head.commit?.sha, root = head.commit?.commit?.tree?.sha;
      requireThat(head.name === branch && GIT.test(base) && GIT.test(root) &&
        (expectedHead === null || expectedHead === base), "Candidate branch moved");
      const files = await treeFiles(root);
      if (reuse && expectedHead !== null) {
        const completeDir = directory(packet.issue) + "/";
        requireThat(!partial || !files.tree.some(f => f.path.startsWith(completeDir)), "Existing originals must be recovered first");
        const existing = await matchingTransfer(packet, partial, base, files.tree);
        if (existing) return existing;
      }
      const entries = await originalEntries(packet, base, partial, files);
      const mainCommit = await guard();
      const tree = gitId(await git("create_tree", {base_tree_sha:root, tree_elements:entries}), "tree");
      return {base, expectedHead, tree, mainCommit,
        [partial ? "checkpoint_sha256" : "receipt_sha256"]:hashText(JSON.stringify(receipt(packet, partial)))};
    }
    async function recordCommit(packet, prepared, partial) {
      const {tree,base,expectedHead} = prepared;
      const hashKey = partial ? "checkpoint_sha256" : "receipt_sha256";
      const result = await git("create_commit", {tree_sha:tree, parent_sha:base,
        message:(partial ? "Checkpoint original photo transfer for " : "Stage unreviewed original photos for ") + packet.issue});
      return {base, expectedHead, commit:gitId(result,"commit"), [hashKey]:prepared[hashKey]};
    }
    async function prepareRecord(packet, expectedHead, partial) {
      const prepared = await prepareRecordTree(packet, expectedHead, partial);
      requireThat(await guard() === prepared.mainCommit, "Main changed during original preparation");
      return recordCommit(packet, prepared, partial);
    }
    const prepare = (packet, expectedHead = null) => prepareRecord(packet, expectedHead, false);
    const prepareCheckpoint = (packet, expectedHead = null) => prepareRecord(packet, expectedHead, true);
    function completeAsset(record, source_sha256) {
      const asset = record.assets.find(item => item.source_sha256 === source_sha256);
      requireThat(asset, "Missing original in receipt");
      requireThat(asset.parts.length === Math.ceil(asset.source_bytes/PART), "Original checkpoint is incomplete");
      return asset;
    }
    function checkedPart(asset, index, encoded) {
      const chunk = runtime.fromBase64(encoded);
      requireThat(chunk.length === Math.min(PART,asset.source_bytes - index * PART) &&
        runtime.sha256(chunk) === asset.parts[index].sha256, "Saved PNG part changed");
      return chunk;
    }
    async function fetchPart(asset, index, commit, dir) {
      const part = await git("fetch_file", {ref:commit,
        path:partPath(dir, asset.source_sha256, index), encoding:"base64"});
      requireThat(part.encoding === "base64" && typeof part.content === "string", "Missing saved PNG part");
      return checkedPart(asset, index, part.content);
    }
    function inspectOriginal(asset, bytes) {
      requireThat(runtime.sha256(bytes) === asset.source_sha256, "Saved original PNG changed");
      const info = runtime.inspectPng(bytes);
      requireThat(info.width === asset.width && info.height === asset.height, "Saved PNG dimensions changed");
      return bytes;
    }
    async function readParts(record, commit, dir, source_sha256) {
      const asset = completeAsset(record, source_sha256);
      const bytes = new Uint8Array(asset.source_bytes);
      for (const p of asset.parts) {
        bytes.set(await fetchPart(asset, p.index, commit, dir), p.index * PART);
      }
      return inspectOriginal(asset, bytes);
    }
    async function originalRecord({issue, commit, receipt_sha256, source_sha256}, treeEntries = null) {
      requireThat(GIT.test(commit) && HASH.test(receipt_sha256) && HASH.test(source_sha256), "Invalid immutable original reference");
      const dir = directory(issue);
      const entry = await git("fetch_file", {ref:commit, path:dir + "/receipt.json", encoding:"utf-8"});
      requireThat(entry.encoding === "utf-8" && hashText(entry.content) === receipt_sha256, "Original receipt changed");
      requireThat(!treeEntries || treeEntries.some(f => f.path === dir + "/receipt.json" &&
        f.sha === entry.sha && f.mode === "100644"), "Candidate original receipt changed");
      const record = JSON.parse(entry.content);
      validate(record);
      requireThat(record.issue === issue && record.status === "originals_only_unreviewed" && record.version === 1, "Invalid original receipt");
      return record;
    }
    async function readOriginal(ref) {
      return readParts(await originalRecord(ref), ref.commit, directory(ref.issue), ref.source_sha256);
    }
    async function restoreCheckpoint({issue, commit, checkpoint_sha256}) {
      requireThat(HASH.test(checkpoint_sha256), "Invalid checkpoint reference");
      const {record} = await checkpointRecord(issue, commit, checkpoint_sha256);
      return {status:"checkpoint_metadata_only", image_bytes_verified:false, commit, checkpoint_sha256,
        packet:record, assets:record.assets.map(asset => ({source_sha256:asset.source_sha256,
          complete:asset.parts.length === Math.ceil(asset.source_bytes/PART),
          next:asset.parts.length === Math.ceil(asset.source_bytes/PART) ? null : asset.parts.length}))};
    }
    async function readCheckpointOriginal({issue, commit, checkpoint_sha256, source_sha256}) {
      requireThat(HASH.test(source_sha256) && HASH.test(checkpoint_sha256), "Invalid immutable original reference");
      const {record} = await checkpointRecord(issue, commit, checkpoint_sha256);
      return readParts(record, commit, pendingDirectory(issue), source_sha256);
    }
    async function readOriginalStepRecord(ref, state, partial, limit = 8) {
      requireThat(Number.isInteger(limit) && limit >= 1 && limit <= 8, "Invalid part read limit");
      const {issue, commit, source_sha256} = ref;
      const recordHash = ref[partial ? "checkpoint_sha256" : "receipt_sha256"];
      requireThat(GIT.test(commit) && HASH.test(source_sha256) && HASH.test(recordHash), "Invalid immutable original reference");
      const record = partial ? (await checkpointRecord(issue, commit, recordHash)).record : await originalRecord(ref);
      const asset = completeAsset(record, source_sha256);
      const reference = {issue, commit, record_sha256:recordHash, source_sha256};
      const kind = partial ? "checkpoint_original" : "completed_original";
      let chunks = [];
      if (state !== null) {
        requireThat(state && typeof state === "object" && !Array.isArray(state) &&
          same(Object.keys(state).sort(), ["version","kind","reference","next","chunks","sha256"].sort()) &&
          state.version === 1 && state.kind === kind && same(state.reference, reference) &&
          Number.isInteger(state.next) && state.next > 0 && state.next < asset.parts.length && state.next % limit === 0 &&
          Array.isArray(state.chunks) && state.chunks.length === state.next &&
          state.chunks.every((chunk,index) => typeof chunk === "string" &&
            chunk.length === 4 * Math.ceil(Math.min(PART,asset.source_bytes - index * PART)/3)) &&
          HASH.test(state.sha256), "Invalid readback state");
        const body = {version:1, kind, reference, next:state.next, chunks:state.chunks};
        requireThat(hashText(JSON.stringify(body)) === state.sha256, "Readback state changed");
        chunks = state.chunks.slice();
        for (let index = 0; index < chunks.length; index++) {
          checkedPart(asset, index, chunks[index]);
        }
      }
      const dir = partial ? pendingDirectory(issue) : directory(issue);
      const end = Math.min(chunks.length + limit, asset.parts.length);
      for (let index = chunks.length; index < end; index++) {
        chunks.push(runtime.toBase64(await fetchPart(asset, index, commit, dir)));
      }
      if (chunks.length < asset.parts.length) {
        const body = {version:1, kind, reference, next:chunks.length, chunks};
        // Private serializable byte state: never print this result or its state.
        return {status:"readback_pending", image_bytes_verified:false, next:chunks.length,
          state:{...body, sha256:hashText(JSON.stringify(body))}};
      }
      const bytes = new Uint8Array(asset.source_bytes);
      chunks.forEach((chunk, index) => bytes.set(checkedPart(asset, index, chunk), index * PART));
      inspectOriginal(asset, bytes);
      return {status:"original_verified", image_bytes_verified:true, commit, source_sha256, bytes};
    }
    const readOriginalStep = (ref, state = null, limit = 8) => readOriginalStepRecord(ref, state, false, limit);
    const readCheckpointOriginalStep = (ref, state = null) => readOriginalStepRecord(ref, state, true);
    // Restore only the requested split original. The stock receipt determines the numbered destination.
    async function numberedRevision(ref, commit) {
      requireThat(GIT.test(ref.commit) && GIT.test(commit), "Invalid immutable original reference");
      // The connector accepts immutable SHAs on both sides. Reversing the comparison
      // returns the proposed commit, tree and parents without paging its intervening history.
      const lineage = await get("/compare/" + commit + "..." + ref.commit + "?per_page=1");
      const revision = lineage.base_commit;
      requireThat(revision?.sha === commit && GIT.test(revision.commit?.tree?.sha) &&
        lineage.merge_base_commit?.sha === ref.commit &&
        lineage.status === (commit === ref.commit ? "identical" : "behind"), "Receipt commit is outside its candidate branch");
      return revision;
    }
    async function numberedSource(ref, verifyOwner) {
      const dir = directory(ref.issue), branch = "photo-issue-plan/" + ref.issue;
      requireThat(GIT.test(ref.commit), "Invalid immutable original reference");
      const head = await get("/branches/" + branch), base = head.commit?.sha, tree = head.commit?.commit?.tree?.sha;
      requireThat(head.name === branch && GIT.test(base) && GIT.test(tree), "Unverified candidate branch");
      const entries = (await treeFiles(tree)).tree;
      const record = await originalRecord(ref, entries), asset = completeAsset(record, ref.source_sha256);
      requireThat(Number.isSafeInteger(asset.source_ordinal) &&
        record.assets.filter(a => a.source_ordinal === asset.source_ordinal).length === 1 &&
        asset.parts.every(p => entries.some(f => f.path === partPath(dir, asset.source_sha256, p.index) &&
          f.type === "blob" && f.mode === "100644" && f.sha === p.blob)), "Ambiguous ordinal or changed candidate parts");
      if (verifyOwner) await checkProducer(record);
      const path = dir + "/" + String(asset.source_ordinal).padStart(2, "0") + ".png";
      const existing = entries.find(f => f.path === path);
      requireThat(!entries.some(f => path.startsWith(f.path + "/") && (f.type !== "tree" || f.mode !== "040000")) &&
        (!existing || (existing.type === "blob" && existing.mode === "100644" && GIT.test(existing.sha))),
      "Occupied numbered original destination");
      return {asset, branch, base, tree, entries, path, existing};
    }
    async function readNumberedPng(ref, path, asset, commit) {
      requireThat(typeof readWeeklyPng === "function", "Missing weekly PNG reader");
      const png = payload(await readWeeklyPng({issue:ref.issue, commit, path, expected_sha256:ref.source_sha256}));
      requireThat(png.status === "read" && png.mime_type === "image/png" && png.path === path && png.commit === commit &&
        png.sha256 === ref.source_sha256 && png.width === asset.width && png.height === asset.height &&
        png.bytes === asset.source_bytes && typeof png.image_url === "string" && png.image_url.startsWith("data:image/png;base64,") &&
        png.image_url.length === 22 + 4 * Math.ceil(asset.source_bytes / 3), "Numbered original readback changed");
      const bytes = runtime.fromBase64(png.image_url);
      requireThat(bytes.length === asset.source_bytes, "Numbered original byte count changed");
      return bytes;
    }
    async function verifyNumberedPng(ref, source, commit) {
      inspectOriginal(source.asset, await readNumberedPng(ref, source.path, source.asset, commit));
    }
    async function uploadNumberedOriginal(ref, verified) {
      const source = await numberedSource(ref, true);
      if (source.base !== ref.commit) await numberedRevision(ref, source.base);
      requireThat(verified?.status === "original_verified" && verified.image_bytes_verified === true &&
        verified.commit === ref.commit && verified.source_sha256 === ref.source_sha256 &&
        verified.bytes instanceof Uint8Array && verified.bytes.length === source.asset.source_bytes,
      "Use the completed split original");
      inspectOriginal(source.asset, verified.bytes);
      let blob;
      if (source.existing) {
        await verifyNumberedPng(ref, source, source.base);
        blob = source.existing.sha;
      } else {
        blob = gitId(await write("create_blob", {encoding:"base64", content:runtime.toBase64(verified.bytes)}), "blob");
      }
      return {blob, path:source.path, base:source.base, receipt_sha256:ref.receipt_sha256, source_sha256:ref.source_sha256};
    }
    function checkNumberedUpload(ref, source, uploaded) {
      requireThat(uploaded?.path === source.path && uploaded.source_sha256 === ref.source_sha256 &&
        uploaded.receipt_sha256 === ref.receipt_sha256 && GIT.test(uploaded.blob) && GIT.test(uploaded.base) &&
        (!source.existing || source.existing.sha === uploaded.blob), "Invalid upload or occupied numbered destination");
    }
    async function prepareNumberedOriginal(ref, uploaded, expectedHead) {
      const source = await numberedSource(ref, false);
      checkNumberedUpload(ref, source, uploaded);
      requireThat(GIT.test(expectedHead) && source.base === expectedHead && uploaded.base === expectedHead, "Candidate branch moved");
      if (source.existing) return {base:expectedHead, commit:expectedHead};
      const main = await guard();
      const tree = gitId(await git("create_tree", {base_tree_sha:source.tree, tree_elements:[
        {path:source.path, mode:"100644", type:"blob", sha:uploaded.blob}]}), "tree");
      requireThat(await guard() === main, "Main changed during original preparation");
      const commit = await git("create_commit", {tree_sha:tree, parent_sha:expectedHead,
        message:"Restore numbered photo original for " + ref.issue});
      return {base:expectedHead, commit:gitId(commit, "commit")};
    }
    async function numberedPublication(ref, uploaded, plan, readbackOnly) {
      const source = await numberedSource(ref, true);
      checkNumberedUpload(ref, source, uploaded);
      requireThat(plan?.base === uploaded.base && GIT.test(plan.commit), "Invalid numbered original plan");
      const result = {branch:source.branch, commit:plan.commit, path:source.path, blob:uploaded.blob};
      if (source.existing) {
        requireThat(source.base === plan.commit, "Candidate branch moved beyond this numbered original");
        const landed = await numberedRevision(ref, source.base);
        requireThat(landed.commit.tree.sha === source.tree, "Commit differs from prepared numbered original");
        if (plan.base !== plan.commit) {
          // A lost reply does not certify the commit. Check its parent and only permitted addition.
          requireThat(landed.parents?.length === 1 && landed.parents[0].sha === plan.base, "Commit differs from prepared numbered original");
          checkOnlyAdded((await parentFiles(plan.base)).files.tree, source.entries, [source.path]);
        }
        await verifyNumberedPng(ref, source, source.base);
        requireThat(await reviewHead(source.branch) === plan.commit, "Numbered original branch update not confirmed");
        return {status:"numbered_original_staged", ...result};
      }
      requireThat(!readbackOnly, "Numbered original branch update not confirmed");
      requireThat(source.base === plan.base && plan.commit !== source.base && plan.commit !== ref.commit,
        "Candidate branch moved without this original");
      // The split receipt is a strict ancestor of this commit, whose only parent is the live head.
      const prepared = await numberedRevision(ref, plan.commit);
      requireThat(prepared.parents?.length === 1 && prepared.parents[0].sha === source.base, "Commit differs from prepared numbered original");
      const files = await treeFiles(prepared.commit.tree.sha);
      requireThat(files.tree.some(f => f.path === source.path && f.mode === "100644" && f.type === "blob" &&
          f.sha === uploaded.blob), "Commit differs from prepared numbered original");
      checkOnlyAdded(source.entries, files.tree, [source.path]);
      await verifyNumberedPng(ref, source, plan.commit);
      await write("update_ref", {branch_name:source.branch, sha:plan.commit, expected_sha:source.base, force:false});
      return {status:"submitted_pending_branch_readback", ...result};
    }
    const submitNumberedOriginal = (ref, uploaded, plan) => numberedPublication(ref, uploaded, plan, false);
    const confirmNumberedOriginal = (ref, uploaded, plan) => numberedPublication(ref, uploaded, plan, true);
    async function publishNumberedOriginal(ref, uploaded, plan) {
      const saved = await submitNumberedOriginal(ref, uploaded, plan);
      if (saved.status === "numbered_original_staged") return saved;
      requireThat(await reviewHead(saved.branch) === saved.commit, "Numbered original branch update not confirmed");
      return {...saved, status:"numbered_original_staged"};
    }
    async function uploadReviewJpeg(ref, original) {
      const record = await originalRecord(ref);
      const asset = completeAsset(record, ref.source_sha256);
      let bytes;
      if (original === undefined) {
        // Legacy one-shot reader. Cold callers budget codec imports as well as the receipt, parts and guarded write.
        requireThat(asset.parts.length <= 8, "Use readOriginalStep for originals larger than 4 MiB");
        bytes = await readParts(record, ref.commit, directory(ref.issue), ref.source_sha256);
      } else if (original?.numberedCommit !== undefined) {
        requireThat(GIT.test(original.numberedCommit) && Number.isSafeInteger(asset.source_ordinal) &&
          record.assets.filter(a => a.source_ordinal === asset.source_ordinal).length === 1, "Invalid numbered original reference");
        const path = directory(ref.issue) + "/" + String(asset.source_ordinal).padStart(2, "0") + ".png";
        bytes = inspectOriginal(asset, await readNumberedPng(ref, path, asset, original.numberedCommit));
      } else {
        requireThat(original?.status === "original_verified" && original.image_bytes_verified === true &&
          original.commit === ref.commit && original.source_sha256 === ref.source_sha256,
        "Use the verified original from the selected receipt");
        bytes = inspectOriginal(asset, original.bytes);
      }
      const jpeg = runtime.convertPhoto(bytes);
      const blob = gitId(await write("create_blob", {encoding:"base64", content:jpeg.base64}), "blob");
      return {status:"jpeg_blob_prepared", source_sha256:ref.source_sha256, original_commit:ref.commit,
        original_receipt_sha256:ref.receipt_sha256, jpeg:{blob, sha256:jpeg.sha256, bytes:jpeg.bytes.length,
          width:jpeg.width, height:jpeg.height}, transform:jpeg.transform, encoding_details:jpeg.encoding_details};
    }
    function reviewCut(ref, prepared) {
      const dir = directory(ref.issue).replace(/\/originals$/, "/prepared-jpegs");
      const jpeg = prepared?.jpeg, transform = prepared?.transform, encoding = prepared?.encoding_details;
      requireThat(GIT.test(ref.commit) && HASH.test(ref.receipt_sha256) && HASH.test(ref.source_sha256) &&
        prepared?.status === "jpeg_blob_prepared" && prepared.original_commit === ref.commit &&
        prepared.original_receipt_sha256 === ref.receipt_sha256 && prepared.source_sha256 === ref.source_sha256 &&
        GIT.test(jpeg?.blob) && HASH.test(jpeg?.sha256) && validReviewSize(jpeg) &&
        transform?.encoder === "jpeg-js" && validPhotoTransform(transform) && pinnedJpegEncoder(encoding),
      "Invalid prepared review JPEG");
      const path = dir + "/" + ref.source_sha256 + ".jpg", metaPath = path.slice(0,-4) + ".json";
      const record = {version:1, status:"unreviewed", issue:ref.issue, source_sha256:ref.source_sha256,
        original_commit:ref.commit, original_receipt_sha256:ref.receipt_sha256, path,
        jpeg:{blob:jpeg.blob, sha256:jpeg.sha256, bytes:jpeg.bytes, width:jpeg.width, height:jpeg.height},
        transform:{encoder:transform.encoder, max_edge:transform.max_edge, jpeg_quality:transform.jpeg_quality, geometry:transform.geometry},
        encoding_details:{encoder_ref:encoding.encoder_ref, resample:encoding.resample, mode:encoding.mode}};
      return {branch:"photo-issue-plan/" + ref.issue, path, metaPath, record};
    }
    async function reviewHead(branch) {
      const entry = await get("/git/ref/heads/" + branch);
      requireThat(entry.ref === "refs/heads/" + branch && GIT.test(entry.object?.sha), "Unverified candidate branch");
      return entry.object.sha;
    }
    function checkJpegBytes(data, jpeg) {
      requireThat(data instanceof Uint8Array && data.length === jpeg.bytes && data[0] === 255 && data[1] === 216 &&
        data[data.length-2] === 255 && data[data.length-1] === 217 &&
        runtime.sha256(data) === jpeg.sha256, "Review JPEG bytes changed");
      return data;
    }
    function verifiedJpeg(file, jpeg) {
      requireThat(file.sha === jpeg.blob && file.encoding === "base64" && typeof file.content === "string",
        "Review JPEG blob changed");
      return checkJpegBytes(runtime.fromBase64(file.content), jpeg);
    }
    async function readReviewCut(cut, commit) {
      requireThat(GIT.test(commit), "Invalid immutable review commit");
      const meta = await git("fetch_file", {ref:commit, path:cut.metaPath, encoding:"utf-8"});
      requireThat(meta.encoding === "utf-8" && meta.content === JSON.stringify(cut.record), "Occupied review JPEG destination");
      const file = await git("fetch_file", {ref:commit, path:cut.path, encoding:"base64"});
      return verifiedJpeg(file, cut.record.jpeg);
    }
    async function readReviewJpeg(ref, prepared, commit) {
      const cut = reviewCut(ref, prepared);
      const bytes = await readReviewCut(cut, commit);
      return {status:"review_jpeg_verified", image_bytes_verified:true, commit, path:cut.path,
        sha256:cut.record.jpeg.sha256, bytes};
    }
    async function reviewSource(ref, cut, files) {
      const source = await originalRecord(ref, files.tree);
      const asset = completeAsset(source, ref.source_sha256), jpeg = cut.record.jpeg;
      const scale = Math.min(1, 1200 / Math.max(asset.width, asset.height));
      requireThat(jpeg.width === Math.round(asset.width * scale) && jpeg.height === Math.round(asset.height * scale) &&
        asset.parts.every(p => files.tree.some(f => f.path === partPath(directory(ref.issue), ref.source_sha256, p.index) &&
          f.mode === "100644" && f.sha === p.blob)), "Candidate branch source or dimensions changed");
      const paths = [cut.path, cut.metaPath];
      requireThat(!files.tree.some(f => f.type !== "tree" && paths.some(p => p.startsWith(f.path + "/"))),
        "Occupied review JPEG directory");
      const existing = files.tree.filter(f => paths.includes(f.path));
      requireThat(!existing.length || (existing.length === 2 && existing.every(f => f.type === "blob" && f.mode === "100644") &&
        existing.some(f => f.path === cut.path && f.sha === jpeg.blob)), "Occupied review JPEG destination");
      return existing.length === 2;
    }
    async function prepareReviewJpeg(ref, prepared, expectedHead) {
      const cut = reviewCut(ref, prepared);
      requireThat(GIT.test(expectedHead), "Invalid expected candidate head");
      const head = await get("/branches/" + cut.branch), tree = head?.commit?.commit?.tree?.sha;
      requireThat(head?.name === cut.branch && head.commit?.sha === expectedHead && GIT.test(tree), "Candidate branch moved");
      const files = await treeFiles(tree);
      if (await reviewSource(ref, cut, files)) {
        await readReviewCut(cut, expectedHead);
        return {base:expectedHead, commit:expectedHead};
      }
      const entries = [{path:cut.path, mode:"100644", type:"blob", sha:cut.record.jpeg.blob},
        {path:cut.metaPath, mode:"100644", type:"blob", content:JSON.stringify(cut.record)}];
      const initialMain = await guard();
      const treeId = gitId(await git("create_tree", {base_tree_sha:tree, tree_elements:entries}), "tree");
      // If main moves, the fresh scan takes the slot otherwise used by create_commit.
      requireThat(await guard() === initialMain, "Main changed during review JPEG preparation");
      const result = await git("create_commit", {tree_sha:treeId, parent_sha:expectedHead,
        message:"Prepare one unreviewed photo for " + ref.issue});
      return {base:expectedHead, commit:gitId(result,"commit")};
    }
    function checkOnlyAdded(before, after, paths) {
      // Adding a file changes its ancestor tree IDs. All other entries must survive exactly.
      const ancestor = f => f.type === "tree" && f.mode === "040000" && paths.some(p => p.startsWith(f.path + "/"));
      const target = f => paths.includes(f.path);
      const retained = new Map(after.filter(f => !ancestor(f) && !target(f)).map(f => [f.path,f]));
      const prior = before.filter(f => !ancestor(f));
      requireThat(!before.some(target) && retained.size === prior.length && prior.every(f => {
        const found = retained.get(f.path);
        return found?.type === f.type && found.mode === f.mode && found.sha === f.sha;
      }), "Commit changes other candidate files");
    }
    async function publishReviewJpeg(ref, prepared, plan) {
      const cut = reviewCut(ref, prepared);
      requireThat(GIT.test(plan?.base) && GIT.test(plan.commit), "Invalid review JPEG plan");
      const current = await reviewHead(cut.branch);
      if (current !== plan.base || current === plan.commit) {
        // Recover a lost success only when both immutable paths and their bytes match.
        requireThat(await reviewSource(ref, cut, (await parentFiles(current)).files),
          "Candidate branch moved without this review JPEG");
        await readReviewCut(cut, current);
        return {status:"review_jpeg_staged_pending_readback", branch:cut.branch, commit:current, path:cut.path};
      }
      const {parent:commit, files:after} = await parentFiles(plan.commit);
      requireThat(commit.parents?.length === 1 && commit.parents[0].sha === current &&
        await reviewSource(ref, cut, after), "Commit differs from prepared review JPEG");
      const before = await treeFiles(current);
      checkOnlyAdded(before.tree, after.tree, [cut.path, cut.metaPath]);
      await readReviewCut(cut, plan.commit);
      await write("update_ref", {branch_name:cut.branch, sha:plan.commit, expected_sha:current, force:false});
      return {status:"review_jpeg_staged_pending_readback", branch:cut.branch, commit:plan.commit, path:cut.path};
    }
    async function verifyOriginalCommit(packet, plan, partial) {
      const commit = await get("/git/commits/" + plan.commit);
      requireThat(commit.sha === plan.commit && commit.parents?.length === 1 &&
        commit.parents[0].sha === plan.base && GIT.test(commit.tree?.sha), "Commit differs from prepared original files");
      await verifyOriginalTree(packet,plan.base,partial,commit.tree.sha,plan.commit);
    }
    async function verifyOriginalTree(packet, base, partial, tree, commit=null) {
      // GitHub accepts an immutable commit SHA as the tree selector and echoes that selector.
      const before = await treeFiles(base);
      const changes = await originalEntries(packet, base, partial, before);
      const after = await treeFiles(tree);
      const manifest = changes[changes.length - 1];
      const saved = after.tree.find(entry=>entry.path===manifest.path);
      requireThat(saved?.mode==="100644" && saved.type==="blob" && GIT.test(saved.sha), "Missing prepared original receipt");
      if (commit) {
        const file = await git("fetch_file",{ref:commit,path:manifest.path,encoding:"utf-8"});
        requireThat(file.encoding==="utf-8" && file.content===manifest.content && file.sha===saved.sha,"Commit differs from prepared original receipt");
      } else {
        // Before a commit exists, the registered connector can read the exact UTF-8 manifest by its Git blob ID.
        const url = "https://api.github.com/repos/"+REPO+"/git/blobs/"+saved.sha;
        const blob = payload(await github.fetch({url}));
        requireThat(blob.url===url && blob.content===manifest.content,"Tree differs from prepared original receipt");
      }
      manifest.sha = saved.sha;
      const expected = new Map(before.tree.map(entry => [entry.path, entry]));
      for (const entry of changes) expected.set(entry.path, entry);
      for (const entry of after.tree) {
        const prior = expected.get(entry.path);
        if (manifest.path.startsWith(entry.path + "/")) {
          requireThat(entry.type === "tree" && entry.mode === "040000" &&
            (!prior || (prior.type === "tree" && prior.mode === entry.mode)), "Occupied original directory");
        } else {
          requireThat(prior && entry.type === prior.type && entry.mode === prior.mode && entry.sha === prior.sha,
            "Commit differs from prepared original files");
        }
        expected.delete(entry.path);
      }
      requireThat(expected.size === 0, "Commit drops prepared original files");
    }
    async function seedMain(base) {
      const comparison = await get("/compare/main..." + base + "?per_page=1");
      const current = comparison.base_commit?.sha;
      requireThat(GIT.test(current) && comparison.merge_base_commit?.sha === base &&
        comparison.status === (current === base ? "identical" : "behind"), "Base is not main history");
      return current;
    }
    async function publishRecord(packet, plan, partial, deferRecovery=false) {
      const branch = "photo-issue-plan/" + packet.issue;
      const hashKey = partial ? "checkpoint_sha256" : "receipt_sha256";
      requireThat(GIT.test(plan.base) && GIT.test(plan.commit) &&
        plan[hashKey] === hashText(JSON.stringify(receipt(packet, partial))), "Changed handoff plan");
      await checkProducer(packet);
      if (plan.expectedHead != null) requireThat(plan.expectedHead === plan.base, "Invalid candidate branch lease");
      await verifyOriginalCommit(packet, plan, partial);
      // Check ancestry before the live write guard. A change between these reads requires a retry.
      const mainAtCheck = plan.expectedHead == null ? await seedMain(plan.base) : undefined;
      const liveMain = await guard();
      requireThat(mainAtCheck === undefined || mainAtCheck === liveMain, "Main changed before original publication");
      const result = savedCommit => ({status:partial ? "checkpoint_saved_pending_readback" : "staged_pending_readback",
        branch, commit:savedCommit, [hashKey]:plan[hashKey]});
      // A duplicate or lost success is usable only after the same remote receipt is read back.
      try {
        if (plan.expectedHead != null) {
          await git("update_ref", {branch_name:branch,sha:plan.commit,expected_sha:plan.base,force:false});
        } else {
          await git("create_branch", {branch_name:branch,sha:plan.commit});
        }
      }
      catch (error) {
        const existing = await get("/git/ref/heads/" + branch);
        requireThat(existing.ref === "refs/heads/" + branch && GIT.test(existing.object?.sha), "Unverified existing branch");
        if (existing.object.sha === plan.commit) return result(plan.commit);
        if (deferRecovery) return {status:"transfer_pending",checkpoint:{phase:"recover",plan}};
        const found = await git("fetch_file", {ref:existing.object.sha,
          path:partial ? pendingDirectory(packet.issue) + "/checkpoint.json" : directory(packet.issue) + "/receipt.json", encoding:"utf-8"});
        if (found.encoding !== "utf-8" || hashText(found.content) !== plan[hashKey]) throw error;
        return result(existing.object.sha);
      }
      const saved = await get("/git/ref/heads/" + branch);
      requireThat(saved.ref === "refs/heads/" + branch && GIT.test(saved.object?.sha), "Branch save not confirmed");
      if (deferRecovery && saved.object.sha !== plan.commit) return {status:"transfer_pending",checkpoint:{phase:"recover",plan}};
      requireThat(saved.object.sha === plan.commit, "Branch save not confirmed");
      return result(plan.commit);
    }
    async function matchingTransfer(packet, partial, commit, files) {
      const record = receipt(packet,partial);
      const dir = partial ? pendingDirectory(packet.issue) : directory(packet.issue);
      const path = dir + (partial ? "/checkpoint.json" : "/receipt.json");
      if (!files.some(f=>f.path===path)) return null;
      const saved = await git("fetch_file",{ref:commit,path,encoding:"utf-8"});
      if (saved.encoding!=="utf-8" || saved.content!==JSON.stringify(record) || !GIT.test(saved.sha)) return null;
      const expected = new Map([[path,saved.sha]]);
      const numbered = new Set();
      for (const asset of record.assets) {
        for (const part of asset.parts) expected.set(partPath(dir,asset.source_sha256,part.index),part.blob);
        if (!partial && record.assets.filter(a=>a.source_ordinal===asset.source_ordinal).length===1)
          numbered.add(dir + "/" + String(asset.source_ordinal).padStart(2, "0") + ".png");
      }
      for (const entry of files.filter(f=>f.path.startsWith(dir+"/"))) {
        // Later numbered copies are checked against the original SHA when used. This result attests only the split receipt.
        if (numbered.delete(entry.path) && entry.type==="blob" && entry.mode==="100644" && GIT.test(entry.sha)) continue;
        requireThat(entry.type==="blob" && entry.mode==="100644" && expected.get(entry.path)===entry.sha, "Concurrent transfer files differ");
        expected.delete(entry.path);
      }
      requireThat(!expected.size,"Incomplete concurrent transfer");
      const key = partial?"checkpoint_sha256":"receipt_sha256";
      return {status:partial?"checkpoint_saved_pending_readback":"staged_pending_readback",
        branch:"photo-issue-plan/"+packet.issue,commit,[key]:hashText(saved.content)};
    }
    // Every mutation runs in its own cold cell; the checkpoint contains only Git references and the packet hash.
    async function saveTransferStep(packet, options, checkpoint=null) {
      const {kind,expectedHead} = options;
      requireThat(["checkpoint","receipt"].includes(kind) && (expectedHead===null || GIT.test(expectedHead)), "Invalid transfer operation");
      const partial = kind==="checkpoint", hashKey = partial ? "checkpoint_sha256" : "receipt_sha256";
      const record = receipt(packet,partial), hash = hashText(JSON.stringify(record));
      const branch = "photo-issue-plan/"+packet.issue;
      if (!checkpoint) {
        const plan = await prepareRecordTree(packet,expectedHead,partial,true);
        if (plan.status) return plan;
        return {status:"transfer_pending",checkpoint:{phase:"commit",plan}};
      }
      const {phase,plan} = checkpoint;
      requireThat(plan?.expectedHead===expectedHead && GIT.test(plan.base) &&
        (expectedHead===null || plan.base===expectedHead) && plan[hashKey]===hash, "Changed transfer packet or lease");
      if (phase==="commit") {
        requireThat(GIT.test(plan.tree), "Invalid prepared original tree");
        await checkProducer(packet);
        await verifyOriginalTree(packet,plan.base,partial,plan.tree);
        let mainAtCheck;
        if (expectedHead === null) mainAtCheck = await seedMain(plan.base);
        else {
          const head = await get("/git/ref/heads/"+branch);
          requireThat(head.ref==="refs/heads/"+branch && head.object?.sha===expectedHead, "Candidate branch moved");
        }
        const liveMain = await guard();
        requireThat(mainAtCheck===undefined || mainAtCheck===liveMain, "Main changed before original commit");
        const prepared = await recordCommit(packet,plan,partial);
        return {status:"transfer_pending",checkpoint:{phase:"publish",plan:prepared}};
      }
      if (phase==="publish") return publishRecord(packet,plan,partial,true);
      requireThat(phase==="recover" && GIT.test(plan.commit), "Invalid transfer recovery");
      await checkProducer(packet);
      const head = await get("/branches/"+branch);
      const commit = head.commit?.sha, tree = head.commit?.commit?.tree?.sha;
      requireThat(head.name===branch && GIT.test(commit) && GIT.test(tree), "Unverified recovery head");
      const saved = await matchingTransfer(packet,partial,commit,(await treeFiles(tree)).tree);
      requireThat(saved,"Concurrent transfer record differs");
      return saved;
    }
    const publish = (packet, plan) => publishRecord(packet, plan, false);
    const publishCheckpoint = (packet, plan) => publishRecord(packet, plan, true);
    // Inputs come from the registered producer draft, fixed candidate handoff and completed byte readback.
    // The caller owns visual review, the PR tree and the final publication validator.
    function prepareSuppliedCut(input) {
      return prepareCut(input, input.issue.slug);
    }
    function prepareCut({issue, cutId, producer, candidate, readback, importedAt}, sourceIssue, stockEvidence = {}) {
      const prefix = planPrefix(sourceIssue);
      const snapshot = issue.model_snapshot;
      requireThat(typeof cutId === "string" && /^(?:0[1-9]|10)$/.test(cutId) && candidate?.issue === sourceIssue &&
        typeof issue.model_id === "string" && SLUG.test(issue.model_id) && candidate.model_id === issue.model_id &&
        snapshot?.id === issue.model_id && typeof snapshot.master?.sha256 === "string" && HASH.test(snapshot.master.sha256) &&
        (!("body_profile" in snapshot || "body_profile_sha256" in snapshot) ||
          (typeof snapshot.body_profile_sha256 === "string" && HASH.test(snapshot.body_profile_sha256))) &&
        matchesProducer(candidate, producer) && typeof producer.source_chat_url === "string" &&
        producer.source_chat_url.startsWith("https://chatgpt.com/c/") && UUID.test(producer.source_chat_url.slice(22)),
      "Candidate belongs to a different issue or producer");
      requireThat(typeof candidate.commit === "string" && GIT.test(candidate.commit) && readback?.commit === candidate.commit &&
        readback.image_bytes_verified === true && readback.source_ordinal === candidate.source_ordinal &&
        Number.isSafeInteger(candidate.source_ordinal) && candidate.source_ordinal > 0,
      "Expected a complete original and JPEG readback at the candidate commit");
      requireThat(typeof importedAt === "string" &&
        /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/.test(importedAt) &&
        Number.isFinite(Date.parse(importedAt)) &&
        new Date(importedAt.slice(0, 10) + "T00:00:00Z").toISOString().slice(0, 10) === importedAt.slice(0, 10),
      "Expected the observed import timestamp with its timezone");
      const {source, jpeg} = candidate;
      verifiedFile(source, readback.source, prefix);
      verifiedFile(jpeg, readback.jpeg, prefix);
      const png = source.format === undefined || source.format === "png";
      requireThat((png || source.format === "jpeg") && (png ? /\.png$/i : /\.jpe?g$/i).test(source.path) &&
        /\.jpe?g$/i.test(jpeg.path) && source.path !== jpeg.path && Number.isSafeInteger(source.bytes) &&
        source.bytes >= 33 && source.bytes <= 32 * 1024 * 1024 &&
        [source.width, source.height].every(n => Number.isInteger(n) && n > 0 && n <= 8192) &&
        source.width * source.height <= 32000000 && validReviewSize(jpeg) &&
        Math.max(jpeg.width, jpeg.height) === Math.min(1200, Math.max(source.width, source.height)) &&
        Math.abs(source.width * jpeg.height - source.height * jpeg.width) <= Math.max(source.width, source.height),
      "Expected a whole original and aspect-preserving publication JPEG");
      const retained = releaseSource(candidate, readback.source);
      const original = readback.source.data;
      requireThat(original instanceof Uint8Array && original.length === source.bytes, "Missing or incomplete original bytes");
      if (png) inspectOriginal({source_sha256:source.sha256, width:source.width, height:source.height}, original);
      else checkJpegBytes(original, source);
      checkJpegBytes(readback.jpeg.data, jpeg);
      validateReleaseEncoding(candidate);
      return suppliedEntries(issue, cutId, candidate, retained, readback.source.blob, readback.jpeg.blob, importedAt, stockEvidence);
    }
    function suppliedEntries(issue, cutId, candidate, retained, sourceBlob, jpegBlob, importedAt, stockEvidence = {}) {
      const {source, jpeg} = candidate, snapshot = issue.model_snapshot;
      const png = source.format === undefined || source.format === "png";
      const dir = "maintenance/photo-issues/" + issue.slug + "/" + cutId + "/";
      const sourcePath = dir + "source-" + source.sha256 + (png ? ".png" : ".jpg");
      const jpegPath = "assets/generated/photo-issue-" + issue.slug + "-" + cutId + ".jpg";
      const receiptPath = dir + "receipt-supplied-" + source.sha256.slice(0,12) + "-" + jpeg.sha256.slice(0,12) + ".json";
      const recovery = {...stockEvidence, candidate_commit:candidate.commit, source_ordinal:candidate.source_ordinal,
        source_path:source.path, jpeg_path:jpeg.path, original_blob_sha:sourceBlob, jpeg_blob_sha:jpegBlob};
      if (candidate.handoff_path !== undefined) {
        requireThat(isCandidatePath(candidate.handoff_path, planPrefix(candidate.issue)) && candidate.handoff_path.endsWith(".json"), "Invalid candidate handoff path");
        recovery.handoff_path = candidate.handoff_path;
      }
      const publicationJpeg = {path:jpegPath, sha256:jpeg.sha256, bytes:jpeg.bytes, width:jpeg.width, height:jpeg.height};
      const record = {version:1, kind:"user_supplied_photo", issue_slug:issue.slug, cut_id:cutId, model_id:issue.model_id,
        imported_at:importedAt, review_reference_sha256:snapshot.master.sha256, source:{...retained, path:sourcePath},
        jpeg:publicationJpeg, transform:candidate.transform, encoding_details:candidate.encoding_details, recovery_reference:recovery};
      const content = JSON.stringify(record, null, 2) + "\n";
      const encoded = runtime.utf8(content);
      requireThat(encoded.length <= 64 * 1024, "Supplied photo receipt exceeds size limit");
      const provenance = {kind:record.kind, receipt_path:receiptPath, receipt_sha256:runtime.sha256(encoded)};
      if (snapshot.body_profile_sha256) provenance.target_design_sha256 = snapshot.body_profile_sha256;
      return {status:"prepared_not_published", files:[
        {path:sourcePath, mode:"100644", type:"blob", sha:sourceBlob},
        {path:jpegPath, mode:"100644", type:"blob", sha:jpegBlob},
        {path:receiptPath, mode:"100644", type:"blob", content}],
      image:{...publicationJpeg, source_sha256:source.sha256, provenance}};
    }
    async function catalog(ref) {
      const file = await git("fetch_file", {ref, path:"content/photo_issues.json", encoding:"utf-8"});
      const data = JSON.parse(file.content);
      requireThat(data.schema_version === 1 && Array.isArray(data.issues), "Invalid edition catalog");
      return data.issues;
    }
    // One saved editorial selection; source ordinals can repeat across different archives.
    async function openReviewedSelection({issue, selectionCommit, releaseCommit, importedAt}) {
      const prefix = planPrefix(issue.slug), path = prefix + "handoff.json";
      requireThat(GIT.test(selectionCommit) && GIT.test(releaseCommit) && issue.state === "draft" &&
        HASH.test(issue.issue_sha256) && issue.model_snapshot?.id === issue.model_id, "Expected a fixed release draft");
      async function handoff(ref) {
        const file = await git("fetch_file", {ref, path, encoding:"utf-8"});
        requireThat(file.encoding === "utf-8", "Missing candidate handoff at the pinned revision");
        const record = JSON.parse(file.content);
        requireThat(record.version === 1 && record.kind === "weekly_photo_candidate_handoff" &&
          record.issue === issue.slug && record.model_id === issue.model_id && record.handoff_path === path &&
          record.branch === "photo-issue-plan/" + issue.slug && Array.isArray(record.candidates), "Wrong candidate handoff");
        return record;
      }
      const selectedHandoff = await handoff(selectionCommit), review = selectedHandoff.editorial_review;
      requireThat(review?.status === "selected_pending_release_receipts" && review.model_id === issue.model_id &&
        GIT.test(review.candidate_commit) && Array.isArray(review.selected) && review.selected.length === 10 &&
        review.face_and_body_reference_checked === true && review.byte_readback_count === 10 &&
        review.reviewed_jpeg_count >= 10 && review.previous_catalog_sha_overlap === 0, "Missing completed editorial selection");
      const commit = review.candidate_commit;
      const sourceHandoff = commit === selectionCommit ? selectedHandoff : await handoff(commit);
      requireThat(matchesProducer(selectedHandoff, sourceHandoff) && same(selectedHandoff.candidates, sourceHandoff.candidates),
        "Selection and original handoff differ");
      const selectedHashes = new Set(review.selected.map(cut => cut.source_sha256));
      checkDistinctSelection(similarSourceGroups(selectedHandoff, selectedHandoff.candidates), selectedHashes);
      if (sourceHandoff !== selectedHandoff) {
        checkDistinctSelection(similarSourceGroups(sourceHandoff, sourceHandoff.candidates), selectedHashes);
      }
      const producer = await checkProducer(sourceHandoff);
      const live = await reviewHead("main"), current = await catalog(live);
      const editing = releaseCommit === live ? current : await catalog(releaseCommit);
      const drafts = [...current, ...editing, issue].filter(item => item.slug === issue.slug);
      requireThat(drafts.every(item => item.state === "draft" && item.issue_sha256 === issue.issue_sha256 &&
        item.model_id === issue.model_id && item.model_snapshot.master.sha256 === issue.model_snapshot.master.sha256 &&
        item.model_snapshot.body_profile_sha256 === issue.model_snapshot.body_profile_sha256),
      "Feature already published or release draft changed");
      const used = new Set();
      for (const feature of [...current, ...editing].filter(item => item.slug !== issue.slug)) {
        for (const image of Object.values(feature.images)) {
          used.add(image.source_sha256 ?? image.source_png_sha256);
          used.add(image.sha256);
        }
      }
      const available = new Map(), existing = [];
      const rows = new Map(sourceHandoff.candidates.map(row => [row.candidate_number, row]));
      requireThat(rows.size === sourceHandoff.candidates.length, "Ambiguous candidate numbering");
      const numbers = new Set(), cuts = new Set();
      for (const selection of review.selected) {
        const cut = selection.cut_id, number = selection.candidate_number;
        const row = rows.get(number), source = row?.source, jpeg = row?.jpeg;
        requireThat(/^(?:0[1-9]|10)$/.test(cut) && issue.shots.some(shot => shot.id === cut) && !cuts.has(cut) &&
          typeof number === "string" && /^[0-9]{2}$/.test(number) && !numbers.has(number) &&
          typeof selection.review === "string" && selection.review.trim() && row && matchesProducer(row, producer) &&
          row.issue === issue.slug && row.handoff_path === path && source?.format === "png" &&
          source.path === prefix + "originals/" + number + ".png" && jpeg?.path === prefix + "jpeg/" + number + ".jpg" &&
          GIT.test(source.blob) && GIT.test(jpeg.blob) && HASH.test(source.sha256) && HASH.test(jpeg.sha256) &&
          source.sha256 === selection.source_sha256 && jpeg.sha256 === selection.jpeg_sha256 &&
          source.bytes === selection.source_bytes && jpeg.bytes === selection.jpeg_bytes &&
          !used.has(source.sha256) && !used.has(jpeg.sha256), "Unavailable or changed selected pair for cut " + cut);
        cuts.add(cut);
        numbers.add(number);
        used.add(source.sha256);
        used.add(jpeg.sha256);
        const occupied = drafts.flatMap(item => Object.entries(item.images).filter(([id, image]) =>
          id === cut || image.source_sha256 === source.sha256 || image.sha256 === jpeg.sha256));
        requireThat(occupied.every(([id, image]) => id === cut && image.source_sha256 === source.sha256 &&
          image.sha256 === jpeg.sha256), "Occupied release cut differs from the selection");
        if (editing.some(item => item.slug === issue.slug && item.images[cut])) existing.push(cut);
        else available.set(cut, row);
      }
      const originals = await get("/contents/" + prefix + "originals?ref=" + commit);
      requireThat(Array.isArray(originals), "Missing original directory at the pinned revision");
      for (const row of available.values()) {
        requireThat(originals.some(file => file.type === "file" && file.path === row.source.path &&
          file.sha === row.source.blob && file.size === row.source.bytes), "Original Git blob changed");
      }
      const cutIds = [...available.keys()].sort();
      const master = {sha256:issue.model_snapshot.master.sha256};
      const model_snapshot = {id:issue.model_id, master};
      if (issue.model_snapshot.body_profile_sha256) model_snapshot.body_profile_sha256 = issue.model_snapshot.body_profile_sha256;
      const plan = {selectionCommit, releaseCommit, mainCommit:live, candidateCommit:commit, importedAt,
        issue:{slug:issue.slug, model_id:issue.model_id, issue_sha256:issue.issue_sha256, model_snapshot},
        edition:review.issue_edition, selectionHash:hashText(JSON.stringify(selectedHandoff)), cutIds};
      return {cutIds, existing:existing.sort(), selection:review, plan,
        prepareCut(cutId) {
          const row = available.get(cutId);
          requireThat(row, "Cut unavailable for preparation");
          return prepareNumberedCut(plan, row, cutId, producer);
        }};
    }
    async function prepareNumberedCut(plan, row, cutId, producer) {
      const {issue, candidateCommit:commit, importedAt} = plan;
      const source = row.source, jpeg = row.jpeg;
      const png = await readNumberedPng({issue:issue.slug, source_sha256:source.sha256}, source.path,
        {source_bytes:source.bytes, width:source.width, height:source.height}, commit);
      const saved = await git("fetch_file", {ref:commit, path:jpeg.path, encoding:"base64"});
      requireThat(saved.sha === jpeg.blob && saved.encoding === "base64", "Selected JPEG Git blob changed");
      const readback = {commit, source_ordinal:row.source_ordinal, image_bytes_verified:true,
        source:{...source, data:png}, jpeg:{...jpeg, data:runtime.fromBase64(saved.content)}};
      return prepareSuppliedCut({issue, cutId, producer, importedAt, candidate:{...row, commit}, readback});
    }
    function packetLocation(slug, edition) {
      requireThat(typeof edition === "string" && /^issue-[0-9]{3,}$/.test(edition), "Expected the weekly release branch");
      return {branch:"photo-issue/" + edition, path:planPrefix(slug) + "release-packet.json"};
    }
    async function startReviewedPacket(plan) {
      const loc = packetLocation(plan.issue.slug, plan.edition);
      requireThat(GIT.test(plan.selectionCommit) && GIT.test(plan.candidateCommit) && HASH.test(plan.selectionHash) &&
        GIT.test(plan.releaseCommit), "Invalid release plan");
      const branch = await get("/branches/" + loc.branch);
      const head = branch.commit?.sha, tree = branch.commit?.commit?.tree?.sha;
      requireThat(branch.name === loc.branch && GIT.test(head) && GIT.test(tree), "Unverified release branch");
      const entry = (await treeFiles(tree)).tree.find(file => file.path === loc.path);
      if (entry) {
        const ref = {slug:plan.issue.slug, edition:plan.edition, selectionCommit:plan.selectionCommit};
        const {packet, sha} = await readPacket(ref, head);
        const saved = packet.plan;
        requireThat(entry.type === "blob" && entry.mode === "100644" && entry.sha === sha &&
          GIT.test(saved.mainCommit) && GIT.test(saved.releaseCommit) &&
          same({...saved, mainCommit:plan.mainCommit, releaseCommit:plan.releaseCommit}, plan) &&
          (plan.releaseCommit === head || (plan.releaseCommit === saved.releaseCommit && plan.mainCommit === saved.mainCommit)),
        "Existing release packet differs; reopen the selection");
        const history = await get("/compare/" + saved.releaseCommit + "..." + head + "?per_page=1");
        requireThat(history.status === "ahead" && history.merge_base_commit?.sha === saved.releaseCommit &&
          history.files?.some(file => file.filename === loc.path && file.status === "added" && file.sha === sha) &&
          (plan.releaseCommit === head || history.files.length === 1),
        "Unverified release changes; reopen the selection at the current head");
        const selected = await packetSelection(ref, saved);
        for (const id of Object.keys(packet.prepared)) preparedReceipt(packet, selected, id);
        requireThat(await guard() === plan.mainCommit, "Main moved; reopen the selection");
        return {commit:head, next:saved.cutIds.find(id => !Object.hasOwn(packet.prepared, id)) ?? null};
      }
      requireThat(head === plan.releaseCommit && await guard() === plan.mainCommit, "Release or main moved; reopen the selection");
      const packet = {version:1, plan, prepared:{}};
      const saved = await git("create_file", {...loc, content:JSON.stringify(packet),
        message:"Start verified photo receipts for " + plan.issue.slug + " [skip ci]"});
      return {commit:gitId(saved,"commit"), next:plan.cutIds[0] ?? null};
    }
    async function readPacket({slug, edition, selectionCommit}, ref) {
      const loc = packetLocation(slug, edition);
      const file = await git("fetch_file", {ref:ref ?? loc.branch, path:loc.path, encoding:"utf-8"});
      const packet = JSON.parse(file.content), plan = packet.plan;
      requireThat(file.encoding === "utf-8" && GIT.test(file.sha) && packet.version === 1 && plan.issue.slug === slug &&
        plan.edition === edition && GIT.test(selectionCommit) && plan.selectionCommit === selectionCommit &&
        Array.isArray(plan.cutIds) && new Set(plan.cutIds).size === plan.cutIds.length &&
        plan.cutIds.length <= 10 && plan.cutIds.every(id => /^(?:0[1-9]|10)$/.test(id)) &&
        packet.prepared && typeof packet.prepared === "object" && !Array.isArray(packet.prepared) &&
        Object.entries(packet.prepared).every(([id, hash]) => plan.cutIds.includes(id) && typeof hash === "string" && HASH.test(hash)),
      "Unrecognized release packet");
      return {loc, packet, sha:file.sha};
    }
    async function continueReviewedPacket(ref) {
      const {loc, packet, sha} = await readPacket(ref);
      const {plan, prepared} = packet;
      const cutId = plan.cutIds.find(id => !Object.hasOwn(prepared, id));
      if (!cutId) return {next:null, prepared:Object.keys(prepared).length};
      const selected = await packetSelection(ref, plan);
      const row = selectedRow(selected, cutId);
      const cut = await prepareNumberedCut(plan, row, cutId, selected);
      prepared[cutId] = cut.image.provenance.receipt_sha256;
      const updated = await write("update_file", {...loc, sha, content:JSON.stringify(packet),
        message:"Verify photo cut " + cutId + " for " + ref.slug + " [skip ci]"});
      return {commit:gitId(updated,"commit"), prepared:Object.keys(prepared).length,
        next:plan.cutIds.find(id => !Object.hasOwn(prepared, id)) ?? null};
    }
    async function packetSelection(ref, plan) {
      const file = await git("fetch_file", {ref:ref.selectionCommit, path:planPrefix(ref.slug)+"handoff.json", encoding:"utf-8"});
      const selected = JSON.parse(file.content), review = selected.editorial_review;
      requireThat(file.encoding === "utf-8" && hashText(JSON.stringify(selected)) === plan.selectionHash &&
        selected.issue === ref.slug && review.issue_edition === ref.edition && review.candidate_commit === plan.candidateCommit,
      "Selection changed since preflight");
      return selected;
    }
    function selectedRow(selected, cutId) {
      const choice = selected.editorial_review.selected.find(item => item.cut_id === cutId);
      const row = selected.candidates.find(item => item.candidate_number === choice?.candidate_number);
      requireThat(choice && row && row.source.sha256 === choice.source_sha256 && row.jpeg.sha256 === choice.jpeg_sha256,
        "Missing selected pair");
      return row;
    }
    function preparedReceipt(packet, selected, id) {
      const plan = packet.plan, row = selectedRow(selected, id);
      const cut = suppliedEntries(plan.issue, id, {...row, commit:plan.candidateCommit}, releaseSource(row, row.source),
        row.source.blob, row.jpeg.blob, plan.importedAt);
      requireThat(cut.image.provenance.receipt_sha256 === packet.prepared[id], "Missing or changed verified receipt for cut " + id);
      return cut;
    }
    async function readReviewedRelease(ref) {
      requireThat(GIT.test(ref.commit), "Expected a fixed release head");
      const {loc, packet, sha} = await readPacket(ref, ref.commit);
      const plan = packet.plan, selected = await packetSelection(ref, plan);
      const files = [], images = {};
      for (const id of plan.cutIds) {
        const cut = preparedReceipt(packet, selected, id);
        files.push(...cut.files);
        images[id] = cut.image;
      }
      return {files, images, plan:packet.plan, packetPath:loc.path, packetBlob:sha};
    }
    async function prepareReviewedTree(ref, preflight) {
      const release = await readReviewedRelease(ref);
      requireThat(preflight?.releaseCommit === ref.commit && preflight.selectionCommit === ref.selectionCommit &&
        preflight.selectionHash === release.plan.selectionHash && same(preflight.issue, release.plan.issue) &&
        same(preflight.cutIds, release.plan.cutIds), "Release no longer matches the current preflight");
      const {parent, files} = await parentFiles(ref.commit);
      const destinations = new Set(release.files.map(file => file.path));
      requireThat(files.tree.some(file => file.type === "blob" && file.mode === "100644" &&
        file.path === release.packetPath && file.sha === release.packetBlob) &&
        !files.tree.some(file => destinations.has(file.path) || (file.type !== "tree" &&
          [...destinations].some(path => path.startsWith(file.path + "/")))), "Occupied release destination");
      requireThat(await guard() === preflight.mainCommit, "Main changed; reopen the selection before building the release tree");
      const tree = await git("create_tree", {base_tree_sha:parent.tree.sha, tree_elements:[...release.files,
        {path:release.packetPath,mode:"100644",type:"blob",sha:null}]});
      return {base:ref.commit, tree:gitId(tree,"tree"), cuts:Object.keys(release.images).length};
    }
    async function stockContext(issue, releaseCommit, modelId, groups) {
      const live = await reviewHead("main");
      const current = await catalog(live), editing = releaseCommit === live ? current : await catalog(releaseCommit);
      const targets = editing.filter(i => i.slug === issue.slug);
      requireThat(issue.state === "draft" && issue.model_id === modelId && issue.model_snapshot?.id === modelId && HASH.test(issue.issue_sha256) &&
        targets.length <= 1 && targets.every(i => i.state === "draft" && i.model_id === issue.model_id &&
          i.issue_sha256 === issue.issue_sha256 && i.model_snapshot.master.sha256 === issue.model_snapshot.master.sha256 &&
          i.model_snapshot.body_profile_sha256 === issue.model_snapshot.body_profile_sha256) &&
        !current.some(i => i.slug === issue.slug && i.state === "published"), "Expected the editor's fixed draft for this producer");
      const used = new Set(), featureSources = new Set();
      for (const edition of [...current.filter(i => i.state === "published" || i.slug === issue.slug), ...editing, issue]) {
        for (const image of Object.values(edition.images)) {
          const digest = image.source_sha256 ?? image.source_png_sha256;
          if (edition.state === "published") requireThat(HASH.test(digest), "Published original identity is missing");
          if (edition.slug === issue.slug && digest) featureSources.add(digest);
          if (digest) used.add(digest);
          if (image.sha256) used.add(image.sha256);
        }
      }
      checkDistinctSelection(groups, featureSources);
      return {live, targets, used, featureSources};
    }
    // An original saved before a publication number was assigned keeps its own immutable storage paths.
    async function openUnassignedStock({stockIssue, commit, releaseCommit, issue}) {
      const prefix = planPrefix(stockIssue), handoffPath = prefix + "handoff.json";
      planPrefix(issue.slug);
      requireThat(stockIssue !== issue.slug && GIT.test(commit) && GIT.test(releaseCommit), "Expected fixed stock and release revisions");
      const file = await git("fetch_file", {ref:commit, path:handoffPath, encoding:"utf-8"});
      requireThat(file.encoding === "utf-8" && file.content, "Missing stock handoff");
      const stock = JSON.parse(file.content), original = stock.originals_receipt;
      requireThat(stock.version === 1 && stock.kind === "photo_original_recovery_handoff" && stock.repository === REPO &&
        stock.status === "originals_verified_and_jpegs_prepared_editorial_adoption_pending" &&
        stock.source_candidate_only === true && stock.publication_number_assigned === null &&
        stock.source_candidate === stockIssue && stock.candidate_branch === "photo-issue-plan/" + stockIssue &&
        Array.isArray(stock.items) && stock.items.length > 0 && stock.items.length <= 30 &&
        original?.path === directory(stockIssue) + "/receipt.json" && GIT.test(stock.prepared_jpeg_storage?.commit) &&
        stock.prepared_jpeg_storage.path_prefix === prefix + "prepared-jpegs/", "Expected an unassigned original-stock handoff");
      const record = await originalRecord({issue:stockIssue, commit:original.commit, receipt_sha256:original.sha256,
        source_sha256:stock.items[0].source_sha256});
      requireThat(matchesProducer(stock, record), "Stock and original receipt belong to different producers");
      await checkProducer(record);
      const stockHead = await reviewHead(stock.candidate_branch);
      if (stockHead !== commit) {
        const lineage = await get("/compare/" + commit + "..." + stockHead + "?per_page=1");
        requireThat(lineage.merge_base_commit?.sha === commit && lineage.status === "ahead", "Stock commit is outside its candidate branch");
      }
      const groups = similarSourceGroups(stock, stock.items);
      const {live, targets, used, featureSources} = await stockContext(issue, releaseCommit, stock.model_id, groups);
      async function directoryAtCommit(name) {
        const entries = await get("/contents/" + prefix + name + "?ref=" + commit);
        requireThat(Array.isArray(entries), "Missing stock directory at the fixed revision");
        return new Map(entries.filter(f => f.type === "file" && GIT.test(f.sha)).map(f => [f.path, f]));
      }
      const originals = await directoryAtCommit("originals"), jpegs = await directoryAtCommit("prepared-jpegs");
      const evidence = {source_issue:stockIssue, handoff_sha256:hashText(file.content), originals_receipt_commit:original.commit,
        originals_receipt_path:original.path, originals_receipt_sha256:original.sha256};
      const chosen = new Map();
      const nearSimilarGroups = stock.editorial?.near_similar_source_ordinal_groups ?? [];
      function select({sourceOrdinal, cutId, importedAt}) {
        const rows = stock.items.filter(i => i.source_ordinal === sourceOrdinal), row = rows[0];
        requireThat(rows.length === 1 && Number.isSafeInteger(sourceOrdinal) && sourceOrdinal > 0 && sourceOrdinal <= 99 &&
          row.formal_editorial_adoption === "pending" && row.source_visual_screening === "passed_candidate_screening" &&
          row.jpeg_visual_screening === "passed_candidate_screening" &&
          stock.items.filter(i => i.source_sha256 === row.source_sha256 || i.prepared_jpeg.sha256 === row.prepared_jpeg.sha256).length === 1 &&
          issue.shots.some(shot => shot.id === cutId) && !issue.images[cutId] && !targets[0]?.images[cutId] &&
          !used.has(row.source_sha256) && !used.has(row.prepared_jpeg.sha256),
        "Source already used, ambiguous, or unavailable for this cut");
        requireThat([...chosen].every(([cut, hash]) => (cut === cutId) === (hash === row.source_sha256)),
          "Stock source or publication cut already selected in this batch");
        checkDistinctSelection(groups, new Set([...featureSources, row.source_sha256]));
        const asset = completeAsset(record, row.source_sha256);
        const sourcePath = prefix + "originals/" + String(sourceOrdinal).padStart(2,"0") + ".png";
        const sourceEntry = originals.get(sourcePath), saved = row.prepared_jpeg, jpegEntry = jpegs.get(saved.path);
        const {width,height} = row.source_dimensions;
        requireThat(asset.source_ordinal === sourceOrdinal && asset.width === width && asset.height === height &&
          asset.source_bytes === row.source_bytes && sourceEntry?.size === row.source_bytes &&
          saved.commit === stock.prepared_jpeg_storage.commit && jpegEntry?.sha === saved.git_blob_sha && jpegEntry.size === saved.bytes,
        "Stock files differ from the fixed original and JPEG");
        const source = {path:sourcePath, blob:sourceEntry.sha, format:"png", sha256:row.source_sha256, bytes:row.source_bytes, width,height,
          url:row.library.url, library_file_id:row.library.library_file_id, file_id:row.library.file_id,
          file_name:row.source_filename, library_file_version:row.library.library_file_version,
          generation_turn:stock.generation_turn, generation_turn_status:stock.generation_turn_status,
          generation_prompt:stock.generation_prompt, generation_prompt_status:stock.generation_prompt_status};
        const jpeg = {path:saved.path, sha256:saved.sha256, bytes:saved.bytes, width:saved.width, height:saved.height, blob:jpegEntry.sha};
        const candidate = {issue:stockIssue, model_id:stock.model_id, source_chat_url:stock.source_chat_url, commit,
          source_ordinal:sourceOrdinal, handoff_path:handoffPath, source, jpeg, transform:row.transform, encoding_details:row.encoding_details};
        return stockPlan(issue, cutId, candidate, evidence, groups, importedAt, live, releaseCommit);
      }
      return {nearSimilarGroups, planCut:select, prepareCut(input) {
        const plan = select(input), {source, jpeg} = plan.candidate;
        const png = input.readback.source;
        requireThat(png?.status === "read" && png.mime_type === "image/png" && png.commit === commit &&
          png.path === source.path && png.sha256 === source.sha256 && png.width === source.width &&
          png.height === source.height && png.bytes === source.bytes && typeof png.image_url === "string" &&
          png.image_url.startsWith("data:image/png;base64,") &&
          png.image_url.length === 22 + 4 * Math.ceil(source.bytes/3), "Stock original readback changed");
        // Preserve receipts already created by the original single-cell interface.
        const cut = stockBytes(plan, runtime.fromBase64(png.image_url), verifiedJpeg(input.readback.jpeg, jpeg), evidence);
        chosen.set(input.cutId, source.sha256);
        featureSources.add(source.sha256);
        return cut;
      }};
    }
    // Separate catalog/producer checks from binary reads. The plan contains only metadata.
    function stockPlan(issue, cutId, candidate, evidence, groups, importedAt, mainCommit, releaseCommit) {
      const {id, master, body_profile_sha256} = issue.model_snapshot;
      const target = {slug:issue.slug, model_id:id, issue_sha256:issue.issue_sha256,
        model_snapshot:{id, master:{sha256:master.sha256}, body_profile_sha256}};
      const excluded = groups.filter(group => group.includes(candidate.source.sha256)).flat()
        .filter(hash => hash !== candidate.source.sha256);
      const data = {issue:target, cutId, candidate, importedAt, mainCommit, releaseCommit, excluded,
        evidence:{...evidence, near_similar_source_sha256:excluded,
          target_issue_sha256:issue.issue_sha256, target_design_sha256:body_profile_sha256}};
      return {...data, planSha:hashText(JSON.stringify(data))};
    }
    function checkedStockPlan(plan) {
      const {planSha, ...data} = plan;
      const {issue, cutId, candidate} = data, prefix = planPrefix(candidate.issue);
      planPrefix(issue.slug);
      requireThat(HASH.test(planSha) && hashText(JSON.stringify(data)) === planSha &&
        /^(?:0[1-9]|10)$/.test(cutId) && candidate.model_id === issue.model_id &&
        GIT.test(plan.releaseCommit) && GIT.test(plan.mainCommit) && GIT.test(candidate.commit) &&
        GIT.test(candidate.source.blob) && GIT.test(candidate.jpeg.blob) && HASH.test(candidate.source.sha256) &&
        HASH.test(candidate.jpeg.sha256) && candidate.handoff_path === prefix+"handoff.json" &&
        isCandidatePath(candidate.source.path,prefix) && isCandidatePath(candidate.jpeg.path,prefix) &&
        plan.evidence.source_issue === candidate.issue && plan.evidence.target_issue_sha256 === issue.issue_sha256,
      "Invalid or changed stock cut plan");
    }
    async function planNumberedStockCut({issue, stockIssue, commit, releaseCommit, cutId, candidateNumber, importedAt}) {
      const prefix = planPrefix(stockIssue), path = prefix+"handoff.json";
      requireThat(GIT.test(commit) && GIT.test(releaseCommit) && /^[0-9]{2}$/.test(candidateNumber), "Invalid numbered stock reference");
      const file = await git("fetch_file",{ref:commit,path,encoding:"utf-8"});
      const stock = JSON.parse(file.content);
      requireThat(file.encoding === "utf-8" && stock.version === 1 && stock.kind === "weekly_photo_candidate_handoff" &&
        stock.issue === stockIssue && stock.handoff_path === path && stock.branch === "photo-issue-plan/"+stockIssue &&
        Array.isArray(stock.candidates), "Invalid numbered stock handoff");
      const rows = stock.candidates.filter(row => row.candidate_number === candidateNumber);
      requireThat(rows.length === 1, "Missing or ambiguous stock number");
      const row = rows[0], {source,jpeg} = row;
      requireThat(row.issue === stockIssue && row.handoff_path === path && matchesProducer(row,stock) &&
        source?.format === "png" && source.path === prefix+"originals/"+candidateNumber+".png" &&
        jpeg?.path === prefix+"jpeg/"+candidateNumber+".jpg" && GIT.test(source.blob) && GIT.test(jpeg.blob) &&
        stock.candidates.filter(i => i.source?.sha256 === source.sha256 || i.jpeg?.sha256 === jpeg.sha256).length === 1,
      "Invalid numbered candidate");
      await checkProducer(stock);
      const head = await reviewHead(stock.branch);
      if (head !== commit) {
        const history = await get("/compare/"+commit+"..."+head+"?per_page=1");
        requireThat(history.status === "ahead" && history.merge_base_commit?.sha === commit, "Stock commit is outside its branch");
      }
      const groups = similarSourceGroups(stock,stock.candidates);
      const {live,targets,used,featureSources} = await stockContext(issue,releaseCommit,stock.model_id,groups);
      requireThat(issue.shots.some(shot => shot.id === cutId) && !issue.images[cutId] && !targets[0]?.images[cutId] &&
        !used.has(source.sha256) && !used.has(jpeg.sha256), "Numbered cut or source already in use");
      checkDistinctSelection(groups,new Set([...featureSources,source.sha256]));
      const originals = await get("/contents/"+prefix+"originals?ref="+commit);
      requireThat(Array.isArray(originals) && originals.some(i => i.type === "file" && i.path === source.path &&
        i.sha === source.blob && i.size === source.bytes), "Numbered stock blob changed");
      return stockPlan(issue,cutId,{...row,commit},{source_issue:stockIssue,handoff_sha256:hashText(file.content)},
        groups,importedAt,live,releaseCommit);
    }
    function stockBytes(plan, png, jpg, evidence=plan.evidence) {
      const {issue, cutId, candidate, importedAt} = plan, {source,jpeg} = candidate;
      const readback = {commit:candidate.commit, source_ordinal:candidate.source_ordinal, image_bytes_verified:true,
        source:{...source,data:png}, jpeg:{...jpeg,data:jpg}};
      return prepareCut({issue,cutId,producer:candidate,candidate,readback,importedAt},candidate.issue,evidence);
    }
    async function readStockCut(plan) {
      checkedStockPlan(plan);
      const {candidate} = plan, {source,jpeg,commit} = candidate;
      const png = await readNumberedPng({issue:candidate.issue,source_sha256:source.sha256},source.path,
        {source_bytes:source.bytes,width:source.width,height:source.height},commit);
      const file = await git("fetch_file",{ref:commit,path:jpeg.path,encoding:"base64"});
      return stockBytes(plan,png,verifiedJpeg(file,jpeg));
    }
    function expectedStockCut(plan) {
      checkedStockPlan(plan);
      const {issue,cutId,candidate,importedAt,evidence} = plan, {source,jpeg} = candidate;
      return suppliedEntries(issue,cutId,candidate,releaseSource(candidate,source),source.blob,jpeg.blob,importedAt,evidence);
    }
    function stockReleaseBranch(edition) {
      requireThat(/^issue-[0-9]{3,}$/.test(edition), "Invalid weekly edition");
      return "photo-issue/"+edition;
    }
    function cutDirectory(issue, cutId) {
      planPrefix(issue.slug);
      requireThat(/^(?:0[1-9]|10)$/.test(cutId), "Invalid release cut");
      return "maintenance/photo-issues/"+issue.slug+"/"+cutId+"/";
    }
    async function stagedStockCut(issue, cutId, commit, files) {
      const dir = cutDirectory(issue,cutId), entries = files.filter(f => f.path.startsWith(dir));
      const jpg = files.find(f => f.path === "assets/generated/photo-issue-"+issue.slug+"-"+cutId+".jpg");
      if (!entries.length && !jpg) return null;
      const receipt = entries.find(f => f.path.startsWith(dir+"receipt-supplied-"));
      requireThat(entries.length === 2 && jpg?.type === "blob" && jpg.mode === "100644" &&
        receipt?.type === "blob" && receipt.mode === "100644", "Occupied or incomplete release cut");
      const file = await git("fetch_file",{ref:commit,path:receipt.path,encoding:"utf-8"});
      requireThat(file.encoding === "utf-8" && file.sha === receipt.sha, "Stored receipt blob changed");
      const record = JSON.parse(file.content), ref = record.recovery_reference, source = record.source, jpeg = record.jpeg;
      requireThat(record.version === 1 && record.kind === "user_supplied_photo" && record.issue_slug === issue.slug &&
        record.cut_id === cutId && record.model_id === issue.model_id && record.review_reference_sha256 === issue.model_snapshot.master.sha256 &&
        ref?.target_issue_sha256 === issue.issue_sha256 && ref.target_design_sha256 === issue.model_snapshot.body_profile_sha256 &&
        HASH.test(source.sha256) && HASH.test(jpeg.sha256) && source.format === "png" && validReviewSize(jpeg) &&
        source.path === dir+"source-"+source.sha256+".png" && jpeg.path === jpg.path && ref.jpeg_blob_sha === jpg.sha &&
        receipt.path === dir+"receipt-supplied-"+source.sha256.slice(0,12)+"-"+jpeg.sha256.slice(0,12)+".json" &&
        entries.some(f => f.type === "blob" && f.mode === "100644" && f.path === source.path && f.sha === ref.original_blob_sha),
      "Stored release cut differs from the frozen draft");
      const provenance = {kind:record.kind,receipt_path:receipt.path,receipt_sha256:hashText(file.content)};
      if (issue.model_snapshot.body_profile_sha256) provenance.target_design_sha256 = issue.model_snapshot.body_profile_sha256;
      return {...jpeg,source_sha256:source.sha256,provenance};
    }
    async function stockCutAt(issue,cutId,commit) {
      requireThat(GIT.test(commit), "Expected a fixed stock release commit");
      const files = await treeFiles(commit);
      const image = await stagedStockCut(issue,cutId,commit,files.tree);
      requireThat(image,"Missing staged cut");
      return image;
    }
    async function readStagedStockCut({issue,cutId,commit,edition}) {
      const head = await reviewHead(stockReleaseBranch(edition));
      if (commit === undefined || head === commit) return stockCutAt(issue,cutId,head);
      requireThat(GIT.test(commit), "Expected a fixed stock release commit");
      const history = await get("/compare/"+commit+"..."+head+"?per_page=1");
      requireThat(history.status === "ahead" && history.merge_base_commit?.sha === commit,
        "Requested cut commit is outside the release branch");
      const pinned = await stockCutAt(issue,cutId,commit), current = await stockCutAt(issue,cutId,head);
      requireThat(same(pinned,current), "Staged selection changed on the release branch");
      return current;
    }
    // An earlier cut can exclude this source even when the new shoot recorded no groups.
    // Read at most nine small immutable receipts in a separate cell from Git writes.
    function siblingReceipts(plan, files) {
      const prefix = "maintenance/photo-issues/"+plan.issue.slug+"/", own = cutDirectory(plan.issue,plan.cutId);
      const receipts = files.filter(f => f.path.startsWith(prefix) && !f.path.startsWith(own) &&
        /\/(?:0[1-9]|10)\/receipt-supplied-[a-f0-9]{12}-[a-f0-9]{12}\.json$/.test(f.path));
      requireThat(receipts.length <= 9 && receipts.every(f=>f.type === "blob" && f.mode === "100644" && GIT.test(f.sha)) &&
        new Set(receipts.map(f=>f.path.split("/").at(-2))).size === receipts.length, "Ambiguous earlier stock receipts");
      return receipts;
    }
    function siblingFingerprint(plan, receipts) {
      return hashText(JSON.stringify([plan.planSha,receipts.map(f=>[f.path,f.sha])]));
    }
    async function checkStockSiblings(plan) {
      checkedStockPlan(plan);
      const files = await treeFiles(plan.releaseCommit), receipts = siblingReceipts(plan,files.tree);
      for (const entry of receipts) {
        const file = await git("fetch_file",{ref:plan.releaseCommit,path:entry.path,encoding:"utf-8"});
        requireThat(file.encoding === "utf-8" && file.sha === entry.sha, "Earlier supplied receipt changed");
        const record = JSON.parse(file.content), evidence = record.recovery_reference;
        requireThat(record.kind === "user_supplied_photo" && record.issue_slug === plan.issue.slug &&
          record.cut_id === entry.path.split("/").at(-2) && record.model_id === plan.issue.model_id &&
          record.review_reference_sha256 === plan.issue.model_snapshot.master.sha256 &&
          (!evidence?.target_issue_sha256 || evidence.target_issue_sha256 === plan.issue.issue_sha256),
        "Earlier receipt belongs to another draft");
        const excluded = evidence?.near_similar_source_sha256 ?? [];
        requireThat(Array.isArray(excluded) && excluded.every(hash=>typeof hash === "string" && HASH.test(hash)) &&
          !excluded.includes(plan.candidate.source.sha256), "An earlier selection excludes this near-similar photo");
      }
      return siblingFingerprint(plan,receipts);
    }
    async function prepareStockCutTree(edition, plan, siblings) {
      const cut = expectedStockCut(plan), branch = stockReleaseBranch(edition);
      const current = await get("/branches/"+branch), head = current.commit?.sha, tree = current.commit?.commit?.tree?.sha;
      requireThat(current.name === branch && GIT.test(head) && GIT.test(tree), "Unverified release branch");
      const files = await treeFiles(tree);
      const {issue,cutId,candidate} = plan;
      const staged = await stagedStockCut(issue,cutId,head,files.tree);
      if (staged) {
        requireThat(same(staged,cut.image), "Release cut already belongs to another selection");
        return {status:"staged",commit:head,image:staged};
      }
      requireThat(head === plan.releaseCommit, "Release moved; reopen the cut at the current head");
      const dir = "maintenance/photo-issues/"+issue.slug+"/";
      const forbidden = new Set([candidate.source.sha256,...plan.excluded].map(hash => "source-"+hash+".png"));
      const destinations = cut.files.map(f=>f.path);
      requireThat(!files.tree.some(f => (f.path.startsWith(dir) && forbidden.has(f.path.split("/").pop())) ||
        (f.path.startsWith("assets/generated/photo-issue-"+issue.slug+"-") && f.sha === candidate.jpeg.blob) ||
        (f.type !== "tree" && destinations.some(path => path.startsWith(f.path+"/")))),
      "Repeated or near-similar stock photo, or occupied release path");
      const earlier = siblingReceipts(plan,files.tree);
      requireThat(!earlier.length || siblings === siblingFingerprint(plan,earlier), "Check earlier stock receipts at this head first");
      const checked = await readStockCut(plan);
      requireThat(same(checked,cut), "Stock readback differs from the selection");
      requireThat(await guard() === plan.mainCommit, "Main moved; reopen the stock cut");
      const created = await git("create_tree",{base_tree_sha:tree,tree_elements:checked.files});
      return {edition,base:head,tree:gitId(created,"tree")};
    }
    async function commitStockCutTree(plan, tree) {
      checkedStockPlan(plan);
      requireThat(tree.base === plan.releaseCommit && GIT.test(tree.tree), "Wrong stock tree base");
      requireThat(await guard() === plan.mainCommit, "Main moved; reopen the stock cut");
      const created = await git("create_commit",{tree_sha:tree.tree,parent_sha:tree.base,
        message:"Prepare selected photo "+plan.cutId+" for "+plan.issue.slug+" [skip ci]"});
      return {edition:tree.edition,base:tree.base,commit:gitId(created,"commit")};
    }
    async function publishStockCut(edition, plan, prepared) {
      const cut = expectedStockCut(plan);
      requireThat(edition === prepared.edition && prepared.base === plan.releaseCommit && GIT.test(prepared.commit),
        "Wrong stock commit or prepared files");
      const branch = stockReleaseBranch(edition), head = await reviewHead(branch);
      if (head !== prepared.base) {
        const staged = await stockCutAt(plan.issue,plan.cutId,head);
        requireThat(same(staged,cut.image), "Release moved to another selection");
        return {status:"staged",commit:head,image:staged};
      }
      const diff = await get("/compare/"+head+"..."+prepared.commit+"?per_page=1");
      requireThat(diff.status === "ahead" && diff.merge_base_commit?.sha === head && diff.total_commits === 1 &&
        diff.commits?.length === 1 && diff.commits[0].sha === prepared.commit && diff.commits[0].parents?.length === 1 &&
        diff.commits[0].parents[0].sha === head && diff.files?.length === cut.files.length &&
        cut.files.every(f => diff.files.some(d => d.filename === f.path && d.status === "added" && (!f.sha || d.sha === f.sha))),
      "Stock commit contains unverified release changes");
      const staged = await stockCutAt(plan.issue,plan.cutId,prepared.commit);
      requireThat(same(staged,cut.image), "Prepared release cut changed");
      requireThat(await guard() === plan.mainCommit, "Main moved; reopen the stock cut");
      const ack = await git("update_ref",{branch_name:branch,sha:prepared.commit,expected_sha:head,force:false});
      requireThat((ack.sha ?? ack.commit_sha ?? ack.object?.sha) === prepared.commit, "Unconfirmed stock release ref");
      return {status:"submitted_pending_branch_readback",commit:prepared.commit};
    }
    return {upload, uploadDataUrl, checkOriginalTransport, saveTransferStep, prepare, publish, readOriginal, prepareSuppliedCut, openReviewedSelection, openUnassignedStock,
      startReviewedPacket, continueReviewedPacket, readReviewedRelease, prepareReviewedTree,
      planNumberedStockCut, readStockCut, readStagedStockCut, checkStockSiblings, prepareStockCutTree, commitStockCutTree, publishStockCut,
      uploadNumberedOriginal, prepareNumberedOriginal, submitNumberedOriginal, confirmNumberedOriginal, publishNumberedOriginal,
      prepareCheckpoint, publishCheckpoint, restoreCheckpoint, readCheckpointOriginal,
      readOriginalStep, readCheckpointOriginalStep, uploadReviewJpeg, prepareReviewJpeg, publishReviewJpeg, readReviewJpeg};
  }
  return {create};
})();
if (typeof module !== "undefined" && module.exports) module.exports = PhotoOriginalHandoff;
