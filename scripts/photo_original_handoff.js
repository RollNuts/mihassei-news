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
    async function upload(file, expectedSha, start = 0, count = 1) {
      requireThat(HASH.test(expectedSha) && Number.isInteger(start) && start >= 0 && (count === 1 || count === 2), "Invalid source reference");
      const png = payload(await readCandidatePng({file}));
      requireThat(png.status === "read" && png.mime_type === "image/png" && png.sha256 === expectedSha &&
        Number.isInteger(png.bytes) && png.bytes > 0 && png.bytes <= MAX &&
        typeof png.image_url === "string" && png.image_url.startsWith("data:image/png;base64,") &&
        png.image_url.length === 22 + 4 * Math.ceil(png.bytes/3), "Unexpected original PNG response");
      const bytes = runtime.fromBase64(png.image_url);
      requireThat(bytes.length === png.bytes && runtime.sha256(bytes) === expectedSha, "Original PNG changed");
      const {width, height} = runtime.inspectPng(bytes);
      requireThat(width === png.width && height === png.height && start * PART < bytes.length, "Invalid PNG dimensions or offset");
      const parts = [];
      for (let index = start; index < start + count && index * PART < bytes.length; index++) {
        const chunk = bytes.subarray(index * PART, (index + 1) * PART);
        const blob = await write("create_blob", {content:runtime.toBase64(chunk), encoding:"base64"});
        parts.push({index, blob:gitId(blob, "blob"), sha256:runtime.sha256(chunk)});
      }
      return {source_sha256:expectedSha, source_bytes:bytes.length, width, height, parts,
        next:(start + parts.length) * PART < bytes.length ? start + parts.length : null};
    }
    function validate(packet) {
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
          asset.parts.length === Math.ceil(asset.source_bytes/PART) &&
          asset.parts.every((part, i) => part.index === i && GIT.test(part.blob) && HASH.test(part.sha256)),
        "Incomplete, duplicate or invalid original");
        hashes.add(asset.source_sha256);
        ordinals.add(position);
      }
    }
    function receipt(packet) {
      validate(packet);
      return {version:1, status:"originals_only_unreviewed", issue:packet.issue, model_id:packet.model_id,
        source_chat_url:packet.source_chat_url, delivery_turn:packet.delivery_turn,
        assets:packet.assets.map(a => ({source_sha256:a.source_sha256, source_bytes:a.source_bytes,
          width:a.width, height:a.height, source_ordinal:a.source_ordinal, generation_turn:a.generation_turn ?? null,
          parts:a.parts.map(p => ({index:p.index, blob:p.blob, sha256:p.sha256}))}))};
    }
    async function tree(packet, base) {
      const dir = directory(packet.issue);
      const record = receipt(packet);
      requireThat(GIT.test(base), "Invalid base commit");
      const parent = await get("/git/commits/" + base);
      requireThat(parent.sha === base && GIT.test(parent.tree?.sha), "Invalid parent tree");
      const files = await get("/git/trees/" + parent.tree.sha + "?recursive=1");
      requireThat(files.sha === parent.tree.sha && files.truncated === false && Array.isArray(files.tree) &&
        !files.tree.some(f => f.path === dir || f.path.startsWith(dir + "/")), "Existing originals must be recovered first");
      const entries = record.assets.flatMap(a => a.parts.map(p => ({
        path:partPath(dir, a.source_sha256, p.index), mode:"100644", type:"blob", sha:p.blob})));
      entries.push({path:dir + "/receipt.json", mode:"100644", type:"blob", content:JSON.stringify(record)});
      return gitId(await write("create_tree", {base_tree_sha:parent.tree.sha, tree_elements:entries}), "tree");
    }
    async function checkProducer(packet) {
      const draft = await git("fetch_file", {ref:"cast-source-drafts", path:"drafts/cast-voices/" + packet.model_id + ".json", encoding:"utf-8"});
      const producer = JSON.parse(draft.content);
      requireThat(producer.model_id === packet.model_id && producer.source_chat_url === packet.source_chat_url, "Wrong producer chat");
    }
    async function prepare(packet, expectedHead = null) {
      validate(packet);
      await checkProducer(packet);
      const main = await guard(), base = expectedHead ?? main;
      if (expectedHead !== null) {
        requireThat(GIT.test(expectedHead), "Invalid expected candidate head");
        const branch = "photo-issue-plan/" + packet.issue;
        const ref = await get("/git/ref/heads/" + branch);
        requireThat(ref.ref === "refs/heads/" + branch && ref.object?.sha === base, "Candidate branch moved");
      }
      const treeId = await tree(packet, base);
      const result = await write("create_commit", {tree_sha:treeId, parent_sha:base,
        message:"Stage unreviewed original photos for " + packet.issue});
      return {base, expectedHead, commit:gitId(result,"commit"), receipt_sha256:hashText(JSON.stringify(receipt(packet)))};
    }
    async function readOriginal({issue, commit, receipt_sha256, source_sha256}) {
      requireThat(GIT.test(commit) && HASH.test(receipt_sha256) && HASH.test(source_sha256), "Invalid immutable original reference");
      const dir = directory(issue);
      const entry = await git("fetch_file", {ref:commit, path:dir + "/receipt.json", encoding:"utf-8"});
      requireThat(entry.encoding === "utf-8" && hashText(entry.content) === receipt_sha256, "Original receipt changed");
      const record = JSON.parse(entry.content);
      validate(record);
      requireThat(record.issue === issue && record.status === "originals_only_unreviewed" && record.version === 1, "Invalid original receipt");
      const asset = record.assets.find(item => item.source_sha256 === source_sha256);
      requireThat(asset, "Missing original in receipt");
      const bytes = new Uint8Array(asset.source_bytes);
      for (const p of asset.parts) {
        const part = await git("fetch_file", {ref:commit, path:partPath(dir, source_sha256, p.index), encoding:"base64"});
        requireThat(part.encoding === "base64" && typeof part.content === "string", "Missing saved PNG part");
        const chunk = runtime.fromBase64(part.content);
        requireThat(chunk.length === Math.min(PART,bytes.length - p.index * PART) &&
          runtime.sha256(chunk) === p.sha256, "Saved PNG part changed");
        bytes.set(chunk, p.index * PART);
      }
      requireThat(runtime.sha256(bytes) === source_sha256, "Saved original PNG changed");
      const info = runtime.inspectPng(bytes);
      requireThat(info.width === asset.width && info.height === asset.height, "Saved PNG dimensions changed");
      return bytes;
    }
    async function publish(packet, plan) {
      const branch = "photo-issue-plan/" + packet.issue;
      requireThat(GIT.test(plan.base) && GIT.test(plan.commit) &&
        plan.receipt_sha256 === hashText(JSON.stringify(receipt(packet))), "Changed handoff plan");
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
        commit.tree?.sha === await tree(packet, plan.base), "Commit differs from prepared original files");
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
          path:directory(packet.issue) + "/receipt.json", encoding:"utf-8"});
        if (found.encoding !== "utf-8" || hashText(found.content) !== plan.receipt_sha256) throw error;
        return {status:"staged_pending_readback",branch,commit:existing.object.sha,receipt_sha256:plan.receipt_sha256};
      }
      const saved = await get("/git/ref/heads/" + branch);
      requireThat(saved.ref === "refs/heads/" + branch && saved.object?.sha === plan.commit, "Branch save not confirmed");
      return {status:"staged_pending_readback",branch,commit:plan.commit,receipt_sha256:plan.receipt_sha256};
    }
    return {upload, prepare, publish, readOriginal};
  }
  return {create};
})();
if (typeof module !== "undefined" && module.exports) module.exports = PhotoOriginalHandoff;
