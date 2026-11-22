/* Keep an existing producer's original PNGs on an isolated photo plan branch.
 * Tool payloads stay inside code mode. No generation, conversion, acceptance or main write.
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
  function directory(issue) {
    requireThat(typeof issue === "string" && issue.length <= 100 && SLUG.test(issue), "Invalid issue slug");
    return "maintenance/photo-issue-plans/" + issue + "/originals";
  }
  function partPath(dir, sha, index) {
    return dir + "/" + sha + ".png.part" + String(index).padStart(3, "0");
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
    async function parentFiles(base) {
      requireThat(GIT.test(base), "Invalid base commit");
      const parent = await get("/git/commits/" + base);
      requireThat(parent.sha === base && GIT.test(parent.tree?.sha), "Invalid parent tree");
      const files = await get("/git/trees/" + parent.tree.sha + "?recursive=1");
      requireThat(files.sha === parent.tree.sha && files.truncated === false && Array.isArray(files.tree), "Invalid parent files");
      return {parent, files};
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
    async function tree(packet, base, partial = false) {
      const dir = directory(packet.issue);
      const record = receipt(packet, partial);
      const {parent, files} = await parentFiles(base);
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
      return gitId(await write("create_tree", {base_tree_sha:parent.tree.sha, tree_elements:entries}), "tree");
    }
    async function checkProducer(packet) {
      const draft = await git("fetch_file", {ref:"cast-source-drafts", path:"drafts/cast-voices/" + packet.model_id + ".json", encoding:"utf-8"});
      const producer = JSON.parse(draft.content);
      requireThat(producer.model_id === packet.model_id && producer.source_chat_url === packet.source_chat_url, "Wrong producer chat");
    }
    async function prepareRecord(packet, expectedHead, partial) {
      validate(packet, partial);
      await checkProducer(packet);
      const main = await guard(), base = expectedHead ?? main;
      if (expectedHead !== null) {
        requireThat(GIT.test(expectedHead), "Invalid expected candidate head");
        const branch = "photo-issue-plan/" + packet.issue;
        const ref = await get("/git/ref/heads/" + branch);
        requireThat(ref.ref === "refs/heads/" + branch && ref.object?.sha === base, "Candidate branch moved");
      }
      const treeId = await tree(packet, base, partial);
      const result = await write("create_commit", {tree_sha:treeId, parent_sha:base,
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
    async function originalRecord({issue, commit, receipt_sha256, source_sha256}) {
      requireThat(GIT.test(commit) && HASH.test(receipt_sha256) && HASH.test(source_sha256), "Invalid immutable original reference");
      const dir = directory(issue);
      const entry = await git("fetch_file", {ref:commit, path:dir + "/receipt.json", encoding:"utf-8"});
      requireThat(entry.encoding === "utf-8" && hashText(entry.content) === receipt_sha256, "Original receipt changed");
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
    async function readOriginalStepRecord(ref, state, partial) {
      const {issue, commit, source_sha256} = ref;
      const recordHash = ref[partial ? "checkpoint_sha256" : "receipt_sha256"];
      requireThat(GIT.test(commit) && HASH.test(source_sha256) && HASH.test(recordHash), "Invalid immutable original reference");
      const record = partial ? (await checkpointRecord(issue, commit, recordHash)).record : await originalRecord(ref);
      const asset = completeAsset(record, source_sha256), limit = 8;
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
    const readOriginalStep = (ref, state = null) => readOriginalStepRecord(ref, state, false);
    const readCheckpointOriginalStep = (ref, state = null) => readOriginalStepRecord(ref, state, true);
    async function publishRecord(packet, plan, partial) {
      const branch = "photo-issue-plan/" + packet.issue;
      const hashKey = partial ? "checkpoint_sha256" : "receipt_sha256";
      requireThat(GIT.test(plan.base) && GIT.test(plan.commit) &&
        plan[hashKey] === hashText(JSON.stringify(receipt(packet, partial))), "Changed handoff plan");
      const current = await guard();
      await checkProducer(packet);
      if (plan.expectedHead != null) {
        requireThat(plan.expectedHead === plan.base, "Invalid candidate branch lease");
      } else if (current !== plan.base) {
        const comparison = await get("/compare/" + plan.base + "..." + current);
        requireThat(comparison.merge_base_commit?.sha === plan.base && comparison.status === "ahead", "Base is not main history");
      }
      const commit = await get("/git/commits/" + plan.commit);
      requireThat(commit.sha === plan.commit && commit.parents?.length === 1 && commit.parents[0].sha === plan.base &&
        commit.tree?.sha === await tree(packet, plan.base, partial), "Commit differs from prepared original files");
      const result = savedCommit => ({status:partial ? "checkpoint_saved_pending_readback" : "staged_pending_readback",
        branch, commit:savedCommit, [hashKey]:plan[hashKey]});
      // A duplicate or lost success is usable only after the same remote receipt is read back.
      try {
        if (plan.expectedHead != null) {
          await write("update_ref", {branch_name:branch,sha:plan.commit,expected_sha:plan.base,force:false});
        } else {
          await write("create_branch", {branch_name:branch,sha:plan.commit});
        }
      }
      catch (error) {
        const existing = await get("/git/ref/heads/" + branch);
        requireThat(existing.ref === "refs/heads/" + branch && GIT.test(existing.object?.sha), "Unverified existing branch");
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
    return {upload, uploadDataUrl, prepare, publish, readOriginal,
      prepareCheckpoint, publishCheckpoint, restoreCheckpoint, readCheckpointOriginal,
      readOriginalStep, readCheckpointOriginalStep};
  }
  return {create};
})();
if (typeof module !== "undefined" && module.exports) module.exports = PhotoOriginalHandoff;
