/* Keep an existing producer's original PNGs on an isolated photo plan branch.
 * Tool payloads stay inside code mode. Release preparation creates tree entries without publishing or accepting a cut.
 */
var PhotoOriginalHandoff = (() => {
  "use strict";
  const REPO = "RollNuts/mihassei-news";
  const GIT = /^[0-9a-f]{40}$/, HASH = /^[0-9a-f]{64}$/;
  const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
  const PART = 512 * 1024, MAX = 8 * 1024 * 1024;
  function requireThat(condition, message) {
    if (!condition) throw Error(message);
  }
  function payload(reply) {
    const data = reply?.structuredContent ?? reply?.result ?? reply;
    requireThat(data && !reply.isError && !data.isError && !reply.error && !data.error &&
      !reply.error_code && !data.error_code, "Photo transport tool failed");
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
  function releaseSource(candidate, observed) {
    const {source, source_ordinal, source_chat_url} = candidate;
    requireThat(source.url === observed.url && typeof source.url === "string" &&
      !("archive" in source) && !("archive" in observed) &&
      source.generation_prompt_status === "not_retained" && source.generation_prompt === null,
    "Expected an individual original with its observed source reference");
    const library = /^https:\/\/chatgpt\.com\/(?:space\/file\/(libfile_[A-Za-z0-9_-]+)|library\?file_id=(libfile_[A-Za-z0-9_-]+)&account_id=personal)$/.exec(source.url);
    const shared = /^https:\/\/chatgpt\.com\/library\/share\/(libfile_[A-Za-z0-9_-]+)(?:\?account_id=personal)?$/.exec(source.url);
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
    const retained = {};
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
  function create({runtime, github, cloudStatus, readCandidatePng}) {
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
    }
    async function prepareRecord(packet, expectedHead, partial) {
      validate(packet, partial);
      await checkProducer(packet);
      const branch = expectedHead === null ? "main" : "photo-issue-plan/" + packet.issue;
      const head = await get("/branches/" + branch);
      const base = head.commit?.sha, root = head.commit?.commit?.tree?.sha;
      requireThat(head.name === branch && GIT.test(base) && GIT.test(root) &&
        (expectedHead === null || expectedHead === base), "Candidate branch moved");
      const entries = await originalEntries(packet, base, partial, await treeFiles(root));
      const initialMain = await guard();
      const treeId = gitId(await git("create_tree", {base_tree_sha:root, tree_elements:entries}), "tree");
      // If main moved, the fresh guard needed two extra reads. Retry before creating the commit.
      requireThat(await guard() === initialMain, "Main changed during original preparation");
      const result = await git("create_commit", {tree_sha:treeId, parent_sha:base,
        message:(partial ? "Checkpoint original photo transfer for " : "Stage unreviewed original photos for ") + packet.issue});
      return {base, expectedHead, commit:gitId(result,"commit"),
        [partial ? "checkpoint_sha256" : "receipt_sha256"]:hashText(JSON.stringify(receipt(packet, partial)))};
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
    async function uploadReviewJpeg(ref, original) {
      const record = await originalRecord(ref);
      const asset = completeAsset(record, ref.source_sha256);
      let bytes;
      if (original === undefined) {
        // One receipt, at most eight part reads, then a cold five-call guard and one blob write.
        requireThat(asset.parts.length <= 8, "Use readOriginalStep for originals larger than 4 MiB");
        bytes = await readParts(record, ref.commit, directory(ref.issue), ref.source_sha256);
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
      requireThat(GIT.test(expectedHead) && await reviewHead(cut.branch) === expectedHead, "Candidate branch moved");
      const {parent, files} = await parentFiles(expectedHead);
      if (await reviewSource(ref, cut, files)) {
        await readReviewCut(cut, expectedHead);
        return {base:expectedHead, commit:expectedHead};
      }
      const entries = [{path:cut.path, mode:"100644", type:"blob", sha:cut.record.jpeg.blob},
        {path:cut.metaPath, mode:"100644", type:"blob", content:JSON.stringify(cut.record)}];
      const initialMain = await guard();
      const treeId = gitId(await git("create_tree", {base_tree_sha:parent.tree.sha, tree_elements:entries}), "tree");
      // A changed main needs a second cold storage scan; stop within the 15-call cell and retry on the same branch.
      requireThat(await guard() === initialMain, "Main changed during review JPEG preparation");
      const result = await git("create_commit", {tree_sha:treeId, parent_sha:expectedHead,
        message:"Prepare one unreviewed photo for " + ref.issue});
      return {base:expectedHead, commit:gitId(result,"commit")};
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
      const {files:before} = await parentFiles(current);
      // Git changes tree IDs up to the root when adding the two files. Every other entry must survive exactly.
      const ancestor = f => f.type === "tree" && f.mode === "040000" && cut.path.startsWith(f.path + "/");
      const target = f => f.path === cut.path || f.path === cut.metaPath;
      const retained = new Map(after.tree.filter(f => !ancestor(f) && !target(f)).map(f => [f.path,f]));
      const prior = before.tree.filter(f => !ancestor(f));
      requireThat(!before.tree.some(target) && retained.size === prior.length && prior.every(f => {
        const found = retained.get(f.path);
        return found?.type === f.type && found.mode === f.mode && found.sha === f.sha;
      }), "Commit changes other candidate files");
      await readReviewCut(cut, plan.commit);
      await write("update_ref", {branch_name:cut.branch, sha:plan.commit, expected_sha:current, force:false});
      return {status:"review_jpeg_staged_pending_readback", branch:cut.branch, commit:plan.commit, path:cut.path};
    }
    async function verifyOriginalCommit(packet, plan, partial) {
      const commit = await get("/git/commits/" + plan.commit);
      requireThat(commit.sha === plan.commit && commit.parents?.length === 1 &&
        commit.parents[0].sha === plan.base && GIT.test(commit.tree?.sha), "Commit differs from prepared original files");
      // GitHub accepts an immutable commit SHA as the tree selector and echoes that selector.
      const before = await treeFiles(plan.base);
      const changes = await originalEntries(packet, plan.base, partial, before);
      const after = await treeFiles(commit.tree.sha);
      const manifest = changes[changes.length - 1];
      const saved = await git("fetch_file", {ref:plan.commit, path:manifest.path, encoding:"utf-8"});
      requireThat(saved.encoding === "utf-8" && saved.content === manifest.content && GIT.test(saved.sha),
        "Commit differs from prepared original receipt");
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
    async function publishRecord(packet, plan, partial) {
      const branch = "photo-issue-plan/" + packet.issue;
      const hashKey = partial ? "checkpoint_sha256" : "receipt_sha256";
      requireThat(GIT.test(plan.base) && GIT.test(plan.commit) &&
        plan[hashKey] === hashText(JSON.stringify(receipt(packet, partial))), "Changed handoff plan");
      await checkProducer(packet);
      if (plan.expectedHead != null) requireThat(plan.expectedHead === plan.base, "Invalid candidate branch lease");
      await verifyOriginalCommit(packet, plan, partial);
      // Check ancestry before the live write guard. A change between these reads requires a retry.
      let mainAtCheck;
      if (plan.expectedHead == null) {
        const comparison = await get("/compare/main..." + plan.base + "?per_page=1");
        mainAtCheck = comparison.base_commit?.sha;
        requireThat(GIT.test(mainAtCheck) && comparison.merge_base_commit?.sha === plan.base &&
          comparison.status === (mainAtCheck === plan.base ? "identical" : "behind"), "Base is not main history");
      }
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
        const found = await git("fetch_file", {ref:existing.object.sha,
          path:partial ? pendingDirectory(packet.issue) + "/checkpoint.json" : directory(packet.issue) + "/receipt.json", encoding:"utf-8"});
        if (found.encoding !== "utf-8" || hashText(found.content) !== plan[hashKey]) throw error;
        return result(existing.object.sha);
      }
      const saved = await get("/git/ref/heads/" + branch);
      requireThat(saved.ref === "refs/heads/" + branch && saved.object?.sha === plan.commit, "Branch save not confirmed");
      return result(plan.commit);
    }
    const publish = (packet, plan) => publishRecord(packet, plan, false);
    const publishCheckpoint = (packet, plan) => publishRecord(packet, plan, true);
    // Inputs come from the registered producer draft, fixed candidate handoff and completed byte readback.
    // The caller owns visual review, the PR tree and the final publication validator.
    function prepareSuppliedCut({issue, cutId, producer, candidate, readback, importedAt}) {
      const prefix = planPrefix(issue.slug);
      const snapshot = issue.model_snapshot;
      requireThat(typeof cutId === "string" && /^(?:0[1-9]|10)$/.test(cutId) && candidate?.issue === issue.slug &&
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
      const original = readback.source.data;
      requireThat(original instanceof Uint8Array && original.length === source.bytes, "Missing or incomplete original bytes");
      if (png) inspectOriginal({source_sha256:source.sha256, width:source.width, height:source.height}, original);
      else checkJpegBytes(original, source);
      checkJpegBytes(readback.jpeg.data, jpeg);
      const retained = releaseSource(candidate, readback.source);
      validateReleaseEncoding(candidate);
      const dir = "maintenance/photo-issues/" + issue.slug + "/" + cutId + "/";
      const sourcePath = dir + "source-" + source.sha256 + (png ? ".png" : ".jpg");
      const jpegPath = "assets/generated/photo-issue-" + issue.slug + "-" + cutId + ".jpg";
      const receiptPath = dir + "receipt-supplied-" + source.sha256.slice(0,12) + "-" + jpeg.sha256.slice(0,12) + ".json";
      const recovery = {candidate_commit:candidate.commit, source_ordinal:candidate.source_ordinal,
        source_path:source.path, jpeg_path:jpeg.path, original_blob_sha:readback.source.blob, jpeg_blob_sha:readback.jpeg.blob};
      if (candidate.handoff_path !== undefined) {
        requireThat(isCandidatePath(candidate.handoff_path, prefix) && candidate.handoff_path.endsWith(".json"), "Invalid candidate handoff path");
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
        {path:sourcePath, mode:"100644", type:"blob", sha:readback.source.blob},
        {path:jpegPath, mode:"100644", type:"blob", sha:readback.jpeg.blob},
        {path:receiptPath, mode:"100644", type:"blob", content}],
      image:{...publicationJpeg, source_sha256:source.sha256, provenance}};
    }
    return {upload, uploadDataUrl, prepare, publish, readOriginal, prepareSuppliedCut,
      prepareCheckpoint, publishCheckpoint, restoreCheckpoint, readCheckpointOriginal,
      readOriginalStep, readCheckpointOriginalStep, uploadReviewJpeg, prepareReviewJpeg, publishReviewJpeg, readReviewJpeg};
  }
  return {create};
})();
if (typeof module !== "undefined" && module.exports) module.exports = PhotoOriginalHandoff;
