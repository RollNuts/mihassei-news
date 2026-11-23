const {test} = require("node:test");
const assert = require("node:assert/strict");
const {createHash} = require("node:crypto");
const {crc32, deflateSync} = require("node:zlib");
const bytes = require("./native_image_bytes.js");
const helper = require("./photo_original_handoff.js");
const stage = require("./native_image_stage.js");
const hash = (data, algorithm="sha256") => createHash(algorithm).update(data).digest("hex");
const sha1 = value => hash(JSON.stringify(value), "sha1");
const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC","base64");
const dataUrl = input => "data:image/png;base64," + input.toString("base64");
const repo = "RollNuts/mihassei-news", main = sha1("main"), baseTree = sha1("base tree");
const chat = "https://chatgpt.com/c/6af867a7-8a9c-83ee-b05d-abb1eadd9f5d";
const packet = () => ({issue:"example-011",model_id:"kiritani-sumire",source_chat_url:chat,delivery_turn:"064ecc91-b742-4a98-b2e6-96034bdc11d5"});

function pngChunk(name, payload) {
  const data = Buffer.concat([Buffer.from(name),payload]);
  const prefix=Buffer.alloc(4), suffix=Buffer.alloc(4);
  prefix.writeUInt32BE(payload.length);suffix.writeUInt32BE(crc32(data));
  return Buffer.concat([prefix,data,suffix]);
}
function largePng(width = 1024, height = 512) {
  const header=Buffer.from(png.subarray(16,29));
  header.writeUInt32BE(width);header.writeUInt32BE(height,4);
  return Buffer.concat([png.subarray(0,8),pngChunk("IHDR",header),
    pngChunk("IDAT",deflateSync(Buffer.alloc(height*(1+width*3)), {level:0})),pngChunk("IEND",Buffer.alloc(0))]);
}

function setup(input = png, {fileAdapter = true, convertPhoto} = {}) {
  const blobs = new Map(), trees = new Map([[baseTree,[]]]), branches = new Map();
  const commits = new Map([[main,{sha:main,tree:{sha:baseTree},parents:[]}]]);
  const state = {active:true,lost:false,writes:[],reads:[],branches,trees,blobs,commits,
    fileReads:0,fileMetadata:{},stopAfterBlobs:Infinity,stopAfterWrites:Infinity};
  const github = {
    async fetch({url}) {
      const path = url.slice(("https://api.github.com/repos/" + repo).length);
      assert.ok(!path.startsWith("/git/blobs/"), "binary blobs require immutable fetch_file");
      if (path === "") return {full_name:repo,private:false,visibility:"public"};
      if (path === "/git/ref/heads/main") return {ref:"refs/heads/main",object:{sha:main}};
      if (path.startsWith("/branches/")) {
        const name = decodeURIComponent(path.slice("/branches/".length));
        const sha = name === "main" ? main : branches.get(name);
        return {name, commit:{sha, commit:{tree:commits.get(sha)?.tree}}};
      }
      if (path === "/actions/workflows/free-cpu-scenes.yml")
        return {path:".github/workflows/free-cpu-scenes.yml",state:state.active ? "active" : "disabled_manually"};
      if (path === "/compare/main..." + main + "?per_page=1")
        return state.comparison ?? {base_commit:{sha:main},merge_base_commit:{sha:main},status:"identical"};
      if (path.startsWith("/git/commits/")) return commits.get(path.slice(13));
      if (path.startsWith("/git/trees/")) {
        const id = path.split("/")[3].split("?")[0];
        return {sha:id,truncated:false,tree:trees.get(commits.get(id)?.tree.sha ?? id)};
      }
      const branch = path.slice("/git/ref/heads/".length);
      return {ref:"refs/heads/"+branch,object:{sha:branches.get(branch)}};
    },
    async fetch_file({ref,path,encoding}) {
      if (ref === "cast-source-drafts") return {encoding,content:JSON.stringify({model_id:"kiritani-sumire",source_chat_url:chat})};
      const tree = trees.get(commits.get(ref).tree.sha);
      const entry = tree.find(e => e.path === path);
      if (state.stopOnFile === path) state.active = false;
      return {sha:entry.sha,encoding,content:entry.content ?? blobs.get(entry.sha).toString("base64")};
    },
    async create_blob({content,encoding}) {
      const bin = Buffer.from(content,encoding), sha = hash(bin,"sha1");
      blobs.set(sha,bin); return {sha};
    },
    async create_tree({base_tree_sha,tree_elements}) {
      const files = new Map(trees.get(base_tree_sha).map(entry => [entry.path,entry]));
      for (const item of tree_elements) {
        const entry = {...item};
        if (typeof entry.content === "string") {
          const bin = Buffer.from(entry.content); entry.sha = hash(bin,"sha1");blobs.set(entry.sha,bin);
        }
        files.set(entry.path,entry);
      }
      const entries = [...files.values()].sort((a,b) => a.path.localeCompare(b.path));
      const sha = sha1(entries); trees.set(sha,entries); return {sha};
    },
    async create_commit({tree_sha,parent_sha}) {
      const sha = sha1([tree_sha,parent_sha]);
      commits.set(sha,{sha,tree:{sha:tree_sha},parents:[{sha:parent_sha}]});return {sha};
    },
    async create_branch({branch_name,sha}) {
      if (branches.has(branch_name)) throw Error("exists");
      branches.set(branch_name,sha); if (state.lost) throw Error("lost response");
      return {sha};
    },
    async update_ref({branch_name,expected_sha,force,sha}) {
      assert.equal(force,false);
      if (branches.get(branch_name)!==expected_sha) throw Error("branch moved");
      branches.set(branch_name,sha);if (state.lost) throw Error("lost response");return {sha};
    }
  };
  for (const name of ["create_blob","create_tree","create_commit","create_branch","update_ref"]) {
    const operation = github[name]; github[name] = async args => {
      assert.equal(args.repository_full_name,repo);
      state.writes.push(name);return operation(args);
    };
  }
  for (const name of ["fetch","fetch_file"]) {
    const operation = github[name];github[name] = async args => {state.reads.push(name);return operation(args)};
  }
  const info = bytes.inspectPng(input);
  const runtime = {...bytes,sha256:hash,convertPhoto};
  const resetGuard = () => {
    state.guard = stage.create({runtime:{...runtime,convert:()=>assert.fail("No article conversion")},github,repository:repo}).cloudStatus;
  };
  const createApi = () => helper.create({runtime,github,
    cloudStatus:async () => state.guard ? state.guard() : ({state:state.active && state.writes.length < state.stopAfterWrites &&
      state.writes.filter(name => name === "create_blob").length < state.stopAfterBlobs
      ?"active":"disabled",public:true,main_commit_sha:state.liveMain ?? main,jpeg_bytes:123}),
    readCandidatePng:fileAdapter ? async () => {
      state.fileReads++;
      return {structuredContent:{status:"read",mime_type:"image/png",sha256:hash(input),bytes:input.length,...info,
        image_url:dataUrl(input),...state.fileMetadata}};
    } : undefined});
  return {api:createApi(),state,reopen:createApi,resetGuard};
}

test("stages the exact original, recovers a lost ref response and verifies immutable bytes",async () => {
  const {api,state}=setup(),input=packet(), asset=await api.upload("own.png",hash(png));
  input.assets=[{...asset,source_ordinal:1}]; const plan=await api.prepare(input);
  state.lost=true;
  const saved=await api.publish(input,plan);
  assert.equal(saved.status,"staged_pending_readback");
  assert.deepEqual(Buffer.from(await api.readOriginal({issue:input.issue,...saved,source_sha256:hash(png)})),png);
  assert.equal((await api.publish(input,plan)).commit,saved.commit);
  state.blobs.set(asset.parts[0].blob,Buffer.from("wrong"));
  await assert.rejects(api.readOriginal({issue:input.issue,...saved,source_sha256:hash(png)}),/part changed/);
});

test("source mismatch, missing part, identity mismatch and disabled guard fail closed",async () => {
  const {api,state}=setup(),input=packet();
  await assert.rejects(api.upload("own.png","a".repeat(64)),/Unexpected original/);
  input.assets=[{...await api.upload("own.png",hash(png)),source_ordinal:1}];
  await assert.rejects(api.prepare({...input,assets:[...input.assets,...input.assets]}),/duplicate/);
  await assert.rejects(api.prepare({...input,model_id:"someone-else"}),/Wrong producer/);
  await assert.rejects(api.prepare({...input,assets:[{...input.assets[0],parts:[]}]}),/Incomplete/);
  state.active=false;
  await assert.rejects(api.prepare(input),/stopped/);
  assert.deepEqual(state.writes,["create_blob"]);
});

test("only advances the requested prior candidate head, refuses occupied original directory",async () => {
  const {api,state}=setup(),input=packet(),branch="photo-issue-plan/"+input.issue;
  input.assets=[{...await api.upload("own.png",hash(png)),source_ordinal:1}];state.branches.set(branch,main);
  const plan=await api.prepare(input,main);
  await assert.rejects(api.publish(input,{...plan,expectedHead:"a".repeat(40)}),/Invalid candidate/);
  const saved=await api.publish(input,plan);
  assert.equal(saved.commit,state.branches.get(branch));
  assert.ok(state.writes.includes("update_ref"));
  await assert.rejects(api.prepare(input,main),/moved/);
  await assert.rejects(api.prepare(input,saved.commit),/Existing originals/);
});

for (const method of ["upload", "uploadDataUrl"]) test(method + " resumes multi-part PNGs and reconstructs exact bytes", async () => {
  const large=largePng(), {api,state}=setup(large,{fileAdapter:method === "upload"});
  const source=method === "upload" ? "large.png" : dataUrl(large), sha=hash(large), parts=[];
  let start=0, last;
  do {
    const before=state.writes.length;
    last=await api[method](source,sha,start,2);
    assert.ok(state.writes.length-before <= 2);
    for (const part of last.parts) {
      assert.ok(state.blobs.get(part.blob).length <= 512*1024);
    }
    parts.push(...last.parts); start=last.next;
  } while(start!==null);
  assert.equal(parts.length,4);
  assert.equal(state.fileReads,method === "upload" ? 2 : 0);
  const input={...packet(),assets:[{...last,parts,source_ordinal:3}]};
  const saved=await api.publish(input,await api.prepare(input));
  assert.deepEqual(Buffer.from(await api.readOriginal({issue:input.issue,...saved,source_sha256:sha})),large);
});

test("data URL originals need no file adapter and remain producer-bound unreviewed candidates", async () => {
  const {api,state}=setup(png,{fileAdapter:false}),input=packet();
  const asset=await api.uploadDataUrl(dataUrl(png),hash(png));
  input.assets=[{...asset,source_ordinal:1}];
  assert.equal(asset.next,null);
  assert.equal(state.fileReads,0);
  await assert.rejects(api.prepare({...input,source_chat_url:"https://chatgpt.com/c/11111111-1111-4111-8111-111111111111"}),/Wrong producer chat/);
  assert.deepEqual(state.writes,["create_blob"]);
  const saved=await api.publish(input,await api.prepare(input));
  assert.equal(saved.status,"staged_pending_readback");
  const receipt=JSON.parse(state.trees.get(state.commits.get(saved.commit).tree.sha).find(e => e.path.endsWith("/receipt.json")).content);
  assert.equal(receipt.status,"originals_only_unreviewed");
  assert.equal(receipt.source_chat_url,chat);
  assert.deepEqual(Buffer.from(await api.readOriginal({issue:input.issue,...saved,source_sha256:hash(png)})),png);
  await assert.rejects(api.prepare(input,saved.commit),/Existing originals/);
});

test("data URL input rejects malformed, changed, oversized and corrupt originals before writing", async () => {
  const {api,state}=setup(png,{fileAdapter:false}),sha=hash(png),url=dataUrl(png);
  for (const invalid of [null,{},"https://example.invalid/original.png","data:image/jpeg;base64,"+png.toString("base64"),"data:image/png;base64,"]) {
    await assert.rejects(api.uploadDataUrl(invalid,sha),/Unexpected original PNG data URL/);
  }
  await assert.rejects(api.uploadDataUrl("data:image/png;base64,!!!!",sha),/Invalid base64/);
  await assert.rejects(api.uploadDataUrl(url+"\n",sha),/Original PNG changed/);
  await assert.rejects(api.uploadDataUrl(url,"a".repeat(64)),/Original PNG changed/);
  await assert.rejects(api.uploadDataUrl(dataUrl(Buffer.alloc(8*1024*1024+1)),sha),/Original PNG changed/);
  await assert.rejects(api.uploadDataUrl(dataUrl(Buffer.alloc(8*1024*1024+2)),sha),/Unexpected original PNG data URL/);
  const corrupt=Buffer.from(png);corrupt[44]^=1;
  await assert.rejects(api.uploadDataUrl(dataUrl(corrupt),hash(corrupt)),/PNG CRC mismatch/);
  const header=Buffer.from(png.subarray(16,29));header.writeUInt32BE(32000001);
  const oversized=Buffer.concat([png.subarray(0,8),pngChunk("IHDR",header),png.subarray(33)]);
  await assert.rejects(api.uploadDataUrl(dataUrl(oversized),hash(oversized)),/PNG dimensions/);
  for (const [start,count] of [[-1,1],[0.5,1],[0,0],[0,3]]) {
    await assert.rejects(api.uploadDataUrl(url,sha,start,count),/Invalid source reference/);
  }
  await assert.rejects(api.uploadDataUrl(url,"invalid"),/Invalid source reference/);
  await assert.rejects(api.uploadDataUrl(url,sha,1),/offset/);
  assert.deepEqual(state.writes,[]);
  assert.equal(state.fileReads,0);
});

test("both upload routes retain metadata checks and recheck the guard before each blob", async () => {
  const file=setup();file.state.fileMetadata={width:2};
  await assert.rejects(file.api.upload("own.png",hash(png)),/PNG dimensions/);
  assert.deepEqual(file.state.writes,[]);
  const large=largePng(),sha=hash(large);
  for (const method of ["upload", "uploadDataUrl"]) {
    const {api,state}=setup(large,{fileAdapter:method === "upload"});
    const source=method === "upload" ? "large.png" : dataUrl(large);
    state.active=false;
    await assert.rejects(api[method](source,sha),/stopped/);
    assert.deepEqual(state.writes,[]);
    state.active=true;state.stopAfterBlobs=1;
    await assert.rejects(api[method](source,sha,0,2),/stopped/);
    assert.deepEqual(state.writes,["create_blob"]);
  }
});

test("checkpoints anchor partial PNGs and resume in a fresh runtime without losing siblings", async () => {
  const large=largePng(),sha=hash(large),{api,state,reopen}=setup(large,{fileAdapter:false});
  const input=packet(),branch="photo-issue-plan/"+input.issue;
  const sibling={path:"maintenance/photo-issue-plans/"+input.issue+"/generation-attempts.json",mode:"100644",type:"blob",content:'{"attempts":["reserved"]}',sha:sha1("attempt record")};
  state.trees.get(baseTree).push(sibling);state.branches.set(branch,main);
  input.assets=[{...await api.uploadDataUrl(dataUrl(large),sha,0),source_ordinal:1}];
  const saved=await api.publishCheckpoint(input,await api.prepareCheckpoint(input,main));
  assert.equal(saved.status,"checkpoint_saved_pending_readback");
  const files=state.trees.get(state.commits.get(saved.commit).tree.sha);
  assert.ok(files.some(file=>file.path.includes("/originals-pending/")&&file.sha===input.assets[0].parts[0].blob));
  assert.deepEqual(files.find(file=>file.path===sibling.path),sibling);
  const fresh=reopen(), restored=await fresh.restoreCheckpoint({issue:input.issue,...saved});
  assert.equal(restored.status,"checkpoint_metadata_only");assert.equal(restored.image_bytes_verified,false);
  assert.deepEqual(restored.assets,[{source_sha256:sha,complete:false,next:1}]);
  await assert.rejects(fresh.readCheckpointOriginal({issue:input.issue,...saved,source_sha256:sha}),/incomplete/);
  await assert.rejects(fresh.readCheckpointOriginalStep({issue:input.issue,...saved,source_sha256:sha}),/incomplete/);
  await assert.rejects(fresh.prepare(restored.packet,saved.commit),/Incomplete/);
  let next=1;
  while(next!==null) {
    const part=await fresh.uploadDataUrl(dataUrl(large),sha,next,2);
    restored.packet.assets[0].parts.push(...part.parts);next=part.next;
  }
  const completed=await fresh.publishCheckpoint(restored.packet,await fresh.prepareCheckpoint(restored.packet,saved.commit));
  assert.deepEqual((await fresh.restoreCheckpoint({issue:input.issue,...completed})).assets,[{source_sha256:sha,complete:true,next:null}]);
  assert.deepEqual(Buffer.from(await fresh.readCheckpointOriginal({issue:input.issue,...completed,source_sha256:sha})),large);
  const before=state.writes.filter(name=>name==="create_blob").length;
  const final=await fresh.publish(restored.packet,await fresh.prepare(restored.packet,completed.commit));
  assert.equal(state.writes.filter(name=>name==="create_blob").length,before);
  assert.deepEqual(Buffer.from(await fresh.readOriginal({issue:input.issue,...final,source_sha256:sha})),large);
  assert.deepEqual(state.trees.get(state.commits.get(final.commit).tree.sha).find(file=>file.path===sibling.path),sibling);
  await assert.rejects(fresh.prepareCheckpoint(restored.packet,final.commit),/Existing originals/);
});

test("checkpoint append-only ownership rejects drops, edits, reordering and stale different updates", async () => {
  const {api,state}=setup(),input=packet();
  const first={...await api.uploadDataUrl(dataUrl(png),hash(png)),source_ordinal:1};
  const large=largePng();let last,next=0;const parts=[];
  do {last=await api.uploadDataUrl(dataUrl(large),hash(large),next,2);parts.push(...last.parts);next=last.next;}while(next!==null);
  const second={...last,parts,source_ordinal:2};input.assets=[first];
  const one=await api.publishCheckpoint(input,await api.prepareCheckpoint(input));
  const both={...input,assets:[first,second]};
  const plan=await api.prepareCheckpoint(both,one.commit);
  const two=await api.publishCheckpoint(both,plan);
  const changes=[
    {...both,delivery_turn:"11111111-1111-4111-8111-111111111111"},
    {...both,assets:[first]},
    {...both,assets:[second,first]},
    {...both,assets:[{...first,width:2},second]},
    {...both,assets:[{...first,parts:[]},second]},
    {...both,assets:[{...first,parts:[{...first.parts[0],blob:"a".repeat(40)}]},second]}
  ];
  const before=state.writes.length;
  for(const changed of changes) await assert.rejects(api.prepareCheckpoint(changed,two.commit),/append-only|identity/);
  await assert.rejects(api.prepareCheckpoint({...both,source_chat_url:"https://chatgpt.com/c/11111111-1111-4111-8111-111111111111"},two.commit),/Wrong producer/);
  await assert.rejects(api.prepareCheckpoint(both,one.commit),/moved/);
  await assert.rejects(api.prepare({...both,assets:[first]},two.commit),/differ from checkpoint/);
  assert.equal(state.writes.length,before);
  // A prior create plan cannot replace a now different saved checkpoint.
  const previousPlan={...await api.prepareCheckpoint(input,null),expectedHead:null};
  await assert.rejects(api.publishCheckpoint(input,previousPlan),/exists/);
  assert.equal(state.branches.get("photo-issue-plan/"+input.issue),two.commit);
});

for (const lost of [false, true]) test("original handoff fits 15 callbacks per cold cell, lost ref reply: " + lost, async () => {
  const {api, state, reopen, resetGuard} = setup(), input = packet(), sha = hash(png);
  input.assets = [{...await api.uploadDataUrl(dataUrl(png), sha), source_ordinal:1}];
  state.lost = lost;
  async function cell(method, ...args) {
    resetGuard();
    const before = state.reads.length + state.writes.length;
    const result = await reopen()[method](...args);
    const calls = state.reads.length + state.writes.length - before;
    assert.ok(calls <= 15, method + " used " + calls + " callbacks");
    return result;
  }

  const plan = await cell("prepareCheckpoint", input);
  const saved = await cell("publishCheckpoint", input, plan);
  assert.equal((await cell("publishCheckpoint", input, plan)).commit, saved.commit);
  const ref = {issue:input.issue, source_sha256:sha};
  assert.deepEqual(Buffer.from(await api.readCheckpointOriginal({...ref, ...saved})), png);

  const update = await cell("prepareCheckpoint", input, saved.commit);
  const advanced = await cell("publishCheckpoint", input, update);
  assert.equal(advanced.commit, state.branches.get("photo-issue-plan/" + input.issue));
  assert.deepEqual(Buffer.from(await api.readCheckpointOriginal({...ref, ...advanced})), png);

  const finalPlan = await cell("prepare", input, advanced.commit);
  const final = await cell("publish", input, finalPlan);
  assert.equal((await cell("publish", input, finalPlan)).commit, final.commit);
  assert.deepEqual(Buffer.from(await api.readOriginal({...ref, ...final})), png);
});

test("checkpoint metadata is not byte verification and changed parts, hashes and CRC fail readback", async () => {
  const {api,state}=setup(),input=packet(),sha=hash(png);
  input.assets=[{...await api.uploadDataUrl(dataUrl(png),sha),source_ordinal:1}];
  const saved=await api.publishCheckpoint(input,await api.prepareCheckpoint(input));
  await assert.rejects(api.restoreCheckpoint({issue:input.issue,...saved,checkpoint_sha256:"a".repeat(64)}),/checkpoint changed/);
  const part=input.assets[0].parts[0],files=state.trees.get(state.commits.get(saved.commit).tree.sha);
  const entry=files.find(file=>file.path.endsWith(".png.part000"));entry.sha="a".repeat(40);
  await assert.rejects(api.restoreCheckpoint({issue:input.issue,...saved}),/part tree changed/);entry.sha=part.blob;
  state.blobs.set(part.blob,Buffer.from("bad"));
  assert.equal((await api.restoreCheckpoint({issue:input.issue,...saved})).image_bytes_verified,false);
  await assert.rejects(api.readCheckpointOriginal({issue:input.issue,...saved,source_sha256:sha}),/part changed/);
  state.blobs.set(part.blob,png);
  const bad=Buffer.from(png);bad[44]^=1;
  const badSha=hash(bad),blob=hash(bad,"sha1");state.blobs.set(blob,bad);
  const malformed={...packet(),issue:"bad-crc-012",assets:[{...input.assets[0],source_sha256:badSha,parts:[{index:0,blob,sha256:badSha}]}]};
  const staged=await api.publishCheckpoint(malformed,await api.prepareCheckpoint(malformed));
  await assert.rejects(api.readCheckpointOriginal({issue:malformed.issue,...staged,source_sha256:badSha}),/PNG CRC mismatch/);
  await assert.rejects(api.readCheckpointOriginalStep({issue:malformed.issue,...staged,source_sha256:badSha}),/PNG CRC mismatch/);
});

test("a new candidate requires its prepared base to be in the final guarded main history", async () => {
  const {api, state} = setup(), input = packet(), newer = sha1("newer main");
  input.assets = [{...await api.uploadDataUrl(dataUrl(png), hash(png)), source_ordinal:1}];
  const plan = await api.prepare(input);
  state.comparison = {base_commit:{sha:newer},merge_base_commit:{sha:main},status:"behind"};
  const writes = state.writes.length;
  await assert.rejects(api.publish(input, plan), /Main changed/);
  state.liveMain = newer;
  state.comparison.status = "diverged";
  await assert.rejects(api.publish(input, plan), /not main history/);
  assert.equal(state.writes.length, writes);
  state.comparison.status = "behind";
  assert.equal((await api.publish(input, plan)).commit, plan.commit);
});

test("publication verifies every prepared path and the receipt before updating the branch", async () => {
  const {api, state} = setup(), input = packet();
  const sibling = {path:"sibling.txt", mode:"100644", type:"blob", sha:sha1("sibling")};
  state.trees.get(baseTree).push(sibling);
  input.assets = [{...await api.uploadDataUrl(dataUrl(png), hash(png)), source_ordinal:1}];
  const plan = await api.prepare(input);
  const treeId = state.commits.get(plan.commit).tree.sha;
  const prepared = state.trees.get(treeId);
  const writes = state.writes.length;
  const changes = [
    [...prepared, {...sibling, path:"extra.txt"}],
    prepared.filter(entry => entry.path !== sibling.path),
    prepared.map(entry => entry.path.endsWith(".png.part000") ? {...entry, sha:sha1("changed part")} : entry),
    prepared.map(entry => entry.path.endsWith("/receipt.json") ? {...entry, content:entry.content + " "} : entry)
  ];
  for (const changed of changes) {
    state.trees.set(treeId, changed);
    await assert.rejects(api.publish(input, plan));
    assert.equal(state.writes.length, writes);
    assert.equal(state.branches.size, 0);
  }
  state.trees.set(treeId, prepared);
  assert.equal((await api.publish(input, plan)).commit, plan.commit);
});

test("upload failures retain only acknowledged part metadata and every checkpoint write checks the guard", async () => {
  const large=largePng(),sha=hash(large),{api,state}=setup(large,{fileAdapter:false});
  state.stopAfterBlobs=1;let failure;
  await assert.rejects(api.uploadDataUrl(dataUrl(large),sha,0,2),error=>{failure=error;return /stopped/.test(error.message)});
  assert.deepEqual(Object.keys(failure.partial).sort(),["height","next","parts","source_bytes","source_sha256","width"]);
  assert.equal(failure.partial.parts.length,1);assert.equal(failure.partial.next,1);
  assert.ok(!JSON.stringify(failure.partial).includes("base64"));
  const input={...packet(),assets:[{...failure.partial,source_ordinal:1}]};
  await assert.rejects(api.prepareCheckpoint(input),/stopped/);
  assert.deepEqual(state.writes,["create_blob"]);
  state.stopAfterBlobs=Infinity;state.stopAfterWrites=state.writes.length+1;
  await assert.rejects(api.prepareCheckpoint(input),/stopped/);
  assert.deepEqual(state.writes,["create_blob","create_tree"]);
  state.stopAfterWrites=Infinity;
  const plan=await api.prepareCheckpoint(input);state.active=false;
  const count=state.writes.length;await assert.rejects(api.publishCheckpoint(input,plan),/stopped/);assert.equal(state.writes.length,count);
  state.active=true;
  state.stopOnFile="maintenance/photo-issue-plans/"+input.issue+"/originals-pending/checkpoint.json";
  await assert.rejects(api.publishCheckpoint(input,plan),/stopped|Unverified/);
  assert.equal(state.branches.size,0);
  assert.equal(state.writes.length,count);
  state.stopOnFile=null;state.active=true;
  const saved=await api.publishCheckpoint(input,plan);state.active=false;
  assert.equal((await api.restoreCheckpoint({issue:input.issue,...saved})).status,"checkpoint_metadata_only");
});

test("bounded readback reconstructs a 14-part original across fresh cells within callback limits", async () => {
  const large=largePng(1536,1536),sha=hash(large),{api,state,reopen}=setup(large,{fileAdapter:false});
  assert.ok(large.length<=8*1024*1024);
  const input=packet(),parts=[];let last,next=0;
  do{last=await api.uploadDataUrl(dataUrl(large),sha,next,2);parts.push(...last.parts);next=last.next;}while(next!==null);
  assert.ok(parts.length>12);assert.equal(parts.length,14);
  input.assets=[{...last,parts,source_ordinal:1}];
  const checkpoint=await api.publishCheckpoint(input,await api.prepareCheckpoint(input));
  const final=await api.publish(input,await api.prepare(input,checkpoint.commit));
  const ref = {issue:input.issue, ...final, source_sha256:sha};
  const priorWrites = state.writes.length, priorReads = state.reads.length;
  await assert.rejects(api.uploadReviewJpeg(ref), /larger than 4 MiB/);
  assert.equal(state.writes.length, priorWrites);
  assert.equal(state.reads.length, priorReads + 1); // receipt only
  await assert.rejects(api.readOriginalStep(ref, null, 9), /part read limit/);
  const firstSeven = await api.readOriginalStep(ref, null, 7);
  assert.equal(firstSeven.next, 7);
  assert.equal((await api.readOriginalStep(ref, firstSeven.state, 7)).status, "original_verified");
  state.active=false;
  for(const [method,saved,overhead] of [["readCheckpointOriginalStep",checkpoint,3],["readOriginalStep",final,1]]) {
    const ref={issue:input.issue,...saved,source_sha256:sha};
    const before=state.reads.length,first=await api[method](ref);
    assert.equal(state.reads.length-before,overhead+8);
    assert.equal(first.status,"readback_pending");assert.equal(first.image_bytes_verified,false);
    assert.equal(first.next,8);assert.equal(first.bytes,undefined);
    const retained=JSON.parse(JSON.stringify(first.state));
    const again=state.reads.length,complete=await reopen()[method](ref,retained);
    assert.ok(state.reads.length-again<=overhead+8);
    assert.equal(state.reads.length-again,overhead+6);
    assert.equal(complete.status,"original_verified");assert.equal(complete.image_bytes_verified,true);
    assert.equal(complete.state,undefined);assert.deepEqual(Buffer.from(complete.bytes),large);
  }
});

test("bounded readback rejects edited, reordered, skipped and cross-reference private state", async () => {
  const large=largePng(1536,1536),sha=hash(large),{api,state}=setup(large,{fileAdapter:false});
  const input=packet(),parts=[];let last,next=0;
  do{last=await api.uploadDataUrl(dataUrl(large),sha,next,2);parts.push(...last.parts);next=last.next;}while(next!==null);
  input.assets=[{...last,parts,source_ordinal:1}];
  const saved=await api.publishCheckpoint(input,await api.prepareCheckpoint(input));
  const ref={issue:input.issue,...saved,source_sha256:sha};
  const initial=await api.readCheckpointOriginalStep(ref),good=initial.state;
  const copy=()=>JSON.parse(JSON.stringify(good));
  const resign=value=>{const {sha256,...body}=value;value.sha256=hash(Buffer.from(JSON.stringify(body)));return value};
  const changes=[];
  let changed=copy();changed.next=7;changes.push(changed);
  changed=copy();changed.chunks.pop();changes.push(changed);
  changed=copy();changed.reference.commit="a".repeat(40);changes.push(resign(changed));
  changed=copy();changed.reference.source_sha256="a".repeat(64);changes.push(resign(changed));
  changed=copy();changed.kind="completed_original";changes.push(resign(changed));
  changed=copy();changed.sha256="a".repeat(64);changes.push(changed);
  changed=copy();[changed.chunks[0],changed.chunks[1]]=[changed.chunks[1],changed.chunks[0]];changes.push(resign(changed));
  changed=copy();const chunk=Buffer.from(changed.chunks[0],"base64");chunk[100]^=1;changed.chunks[0]=chunk.toString("base64");changes.push(resign(changed));
  changed=copy();changed.extra=true;changes.push(changed);
  const writes=state.writes.length;
  for(const bad of changes)await assert.rejects(api.readCheckpointOriginalStep(ref,bad),/readback state|state changed|part changed/);
  assert.equal(state.writes.length,writes);
  const advanced=await api.publishCheckpoint(input,await api.prepareCheckpoint(input,saved.commit));
  await assert.rejects(api.readCheckpointOriginalStep({issue:input.issue,...advanced,source_sha256:sha},good),/readback state/);
  const final=await api.publish(input,await api.prepare(input,advanced.commit));
  await assert.rejects(api.readOriginalStep({issue:input.issue,...final,source_sha256:sha},good),/readback state/);
  assert.deepEqual(Buffer.from((await api.readCheckpointOriginalStep(ref,good)).bytes),large);
});

test("a completed receipt stages one verified JPEG on its own branch and recovers a lost lease reply", async () => {
  const source = largePng(320,320), sourceHash = hash(source);
  // The codec is separately tested. This fixture exercises the transport of actual distinct byte buffers.
  const jpeg = Buffer.alloc(16000);
  jpeg.set([255,216,255]); jpeg.set([255,217],jpeg.length-2);
  const convertPhoto = original => {
    assert.deepEqual(Buffer.from(original), source);
    return {width:320,height:320,bytes:jpeg,sha256:hash(jpeg),base64:jpeg.toString("base64"),
      transform:{encoder:"jpeg-js",max_edge:1200,jpeg_quality:84,geometry:"preserve-aspect"},
      encoding_details:{encoder_ref:bytes.SOURCES.encode.ref,resample:"bilinear",mode:"RGB"}};
  };
  const {api,state,resetGuard} = setup(source,{convertPhoto});
  const raw = packet();
  raw.assets = [{...await api.uploadDataUrl(dataUrl(source),sourceHash),source_ordinal:1}];
  const staged = await api.publish(raw,await api.prepare(raw));
  const ref = {issue:raw.issue,...staged,source_sha256:sourceHash};
  const verified = await api.readOriginalStep(ref);
  let writes = state.writes.length;
  await assert.rejects(api.uploadReviewJpeg(ref,{...verified,bytes:png}),/original PNG changed/);
  await assert.rejects(api.uploadReviewJpeg({...ref,receipt_sha256:"0".repeat(64)},verified),/receipt changed/);
  state.active=false;
  await assert.rejects(api.uploadReviewJpeg(ref,verified),/stopped/);
  assert.equal(state.writes.length,writes);
  state.active=true;

  // Exercise the real public cloud guard with a fresh immutable-main cache in every measured cell.
  async function callbacks(expected, operation) {
    resetGuard();
    const {length:reads} = state.reads, {length:mutations} = state.writes;
    const result = await operation();
    const count = state.reads.length - reads + state.writes.length - mutations;
    assert.equal(count, expected);
    assert.ok(count <= 15);
    return result;
  }
  const prepared = await callbacks(7,()=>api.uploadReviewJpeg(ref,verified));
  const recoveredBlob = await callbacks(8,()=>api.uploadReviewJpeg(ref));
  assert.deepEqual(recoveredBlob,prepared);
  const plan = await callbacks(14,()=>api.prepareReviewJpeg(ref,prepared,staged.commit));
  const provisional = state.trees.get(state.commits.get(plan.commit).tree.sha);
  const originalTree = state.trees.get(state.commits.get(staged.commit).tree.sha);
  assert.equal(provisional.length, originalTree.length + 2);
  assert.equal(state.branches.get(staged.branch),staged.commit);
  const file = provisional.find(f => f.path.endsWith(".jpg"));
  assert.equal(file.path,"maintenance/photo-issue-plans/example-011/prepared-jpegs/"+sourceHash+".jpg");
  assert.equal(file.sha,prepared.jpeg.blob);
  const receipt = JSON.parse(provisional.find(f=>f.path.endsWith(sourceHash+".json")).content);
  assert.equal(receipt.status,"unreviewed");
  assert.equal(receipt.original_commit,staged.commit);

  // Substituting another tree or binary after preparation must not advance the branch.
  const sibling = {path:"other.txt",mode:"100644",type:"blob",sha:sha1("unrelated")};
  provisional.push(sibling);
  await assert.rejects(api.publishReviewJpeg(ref,prepared,plan),/other candidate files/);
  provisional.pop();
  const corrupted = Buffer.from(jpeg); corrupted[100] ^= 1;
  state.blobs.set(file.sha,corrupted);
  await assert.rejects(api.publishReviewJpeg(ref,prepared,plan),/bytes changed/);
  state.blobs.set(file.sha,jpeg);
  state.active=false;
  await assert.rejects(api.publishReviewJpeg(ref,prepared,plan),/stopped/);
  assert.equal(state.branches.get(staged.branch),staged.commit);
  state.active=true;
  await assert.rejects(api.prepareReviewJpeg(ref,prepared,main),/branch moved/);
  await assert.rejects(api.prepareReviewJpeg({...ref,issue:"elsewhere"},prepared,staged.commit),/branch/);
  await assert.rejects(api.prepareReviewJpeg(ref,{...prepared,encoding_details:{...prepared.encoding_details,encoder_ref:main}},staged.commit),/Invalid prepared/);

  state.lost=true;
  await callbacks(14,()=>assert.rejects(api.publishReviewJpeg(ref,prepared,plan),/lost response/));
  assert.equal(state.branches.get(staged.branch),plan.commit);
  state.lost=false;
  writes = state.writes.length;
  const recovered = await callbacks(6,()=>api.publishReviewJpeg(ref,prepared,plan));
  const live = await callbacks(2,()=>api.readReviewJpeg(ref,prepared,recovered.commit));
  assert.equal(live.image_bytes_verified,true);
  assert.deepEqual(Buffer.from(live.bytes),jpeg);
  assert.deepEqual(await api.prepareReviewJpeg(ref,prepared,recovered.commit),{base:recovered.commit,commit:recovered.commit});
  assert.equal(state.writes.length,writes);
  assert.deepEqual(Buffer.from(await api.readOriginal(ref)), source);
  assert.deepEqual([...state.branches.keys()],[staged.branch]);

  const sidecar=provisional.find(f=>f.path.endsWith(sourceHash+".json"));
  sidecar.content+=' ';
  await assert.rejects(api.prepareReviewJpeg(ref,prepared,recovered.commit),/Occupied/);
  await assert.rejects(api.publishReviewJpeg(ref,prepared,plan),/Occupied/);
  assert.equal(state.writes.length,writes);
});

const {readFileSync} = require("node:fs");
const {resolve} = require("node:path");
const checkout = resolve(__dirname, "..");
const readLocal = path => readFileSync(resolve(checkout, path));
const rio = JSON.parse(readLocal("content/photo_issues.json")).issues.find(
  issue => issue.slug === "november-coast-city-rio-015");
const rioProducer = {model_id:rio.model_id, source_chat_url:"https://chatgpt.com/c/6af86744-fb88-83e8-991f-4e2e9d3d8a7f"};

function readbackFile(record, path, expectedBlob) {
  const data = readLocal(record.path);
  const object = Buffer.concat([Buffer.from("blob " + data.length + "\0"), data]);
  const blob = hash(object, "sha1");
  if (expectedBlob !== undefined) assert.equal(blob, expectedBlob);
  return {path, blob, data, sha256:hash(data), bytes:data.length, width:record.width, height:record.height};
}

// #963 retained the published bytes and the source and JPEG paths at the candidate commit.
// Reconstruct the caller's readback from those bytes without fetching the old branch in CI.
function suppliedCutFixture(cutId) {
  const receipt = JSON.parse(readLocal(rio.images[cutId].provenance.receipt_path));
  const ref = receipt.recovery_reference;
  const qa = JSON.parse(readLocal(rio.images[cutId].review.evidence.path));
  const checked = qa.cuts.find(row => row.cut_id === cutId);
  const candidate = {issue:rio.slug, model_id:rio.model_id, source_chat_url:rioProducer.source_chat_url,
    commit:ref.candidate_commit, source_ordinal:ref.source_ordinal, handoff_path:ref.handoff_path,
    source:{...receipt.source, path:ref.source_path}, jpeg:{...receipt.jpeg, path:ref.jpeg_path},
    transform:receipt.transform, encoding_details:receipt.encoding_details};
  const readback = {commit:qa.editorial_source.candidate_commit, source_ordinal:checked.source_ordinal, image_bytes_verified:true,
    source:readbackFile(receipt.source, ref.source_path, ref.original_blob_sha),
    jpeg:readbackFile(receipt.jpeg, ref.jpeg_path, ref.jpeg_blob_sha)};
  const {url, library_file_id, turn_key, image_number} = checked.source;
  Object.assign(readback.source, library_file_id ? {url, library_file_id} : {url, turn_key, image_number});
  return {receipt, input:{issue:rio, cutId, producer:rioProducer, candidate, readback, importedAt:receipt.imported_at}};
}

// Use archived bytes in an offline stock-only handoff shaped like Rena's 334f466 record.
function unassignedStockFixture() {
  const {input} = suppliedCutFixture("09");
  const {source, jpeg, transform, encoding_details} = input.candidate, measured = input.readback;
  const releaseCommit = sha1("editor head"), commit = sha1("materialized stock"), receiptCommit = sha1("split receipt");
  const stockIssue = "reserve-storage", prefix = "maintenance/photo-issue-plans/" + stockIssue + "/";
  const issue = {...rio, slug:"editor-draft", state:"draft", published_at:null, images:{}};
  const parts = [];
  for (let start=0; start<source.bytes; start+=512*1024) {
    const chunk = measured.source.data.subarray(start, start+512*1024);
    parts.push({index:parts.length, blob:sha1(parts.length), sha256:hash(chunk)});
  }
  const receipt = {version:1, status:"originals_only_unreviewed", issue:stockIssue, ...rioProducer,
    delivery_turn:"064ecc91-b742-4a98-b2e6-96034bdc11d5", assets:[{source_sha256:source.sha256,
      source_bytes:source.bytes, width:source.width, height:source.height, source_ordinal:1, generation_turn:null, parts}]};
  const jpegPath = prefix+"prepared-jpegs/reserve-source01.jpg", sourcePath = prefix+"originals/01.png";
  const item = {source_ordinal:1, source_sha256:source.sha256, source_bytes:source.bytes, source_filename:source.file_name,
    source_dimensions:{width:source.width,height:source.height}, library:{url:source.url,library_file_id:source.library_file_id},
    prepared_jpeg:{...jpeg,commit:sha1("original JPEG"),path:jpegPath,git_blob_sha:measured.jpeg.blob}, transform, encoding_details,
    source_visual_screening:"passed_candidate_screening", jpeg_visual_screening:"passed_candidate_screening", formal_editorial_adoption:"pending"};
  const handoff = {version:1,kind:"photo_original_recovery_handoff",repository:repo,...rioProducer,source_candidate:stockIssue,
    status:"originals_verified_and_jpegs_prepared_editorial_adoption_pending", candidate_branch:"photo-issue-plan/"+stockIssue,
    source_candidate_only:true,publication_number_assigned:null,generation_turn:null,generation_turn_status:"not_retained",
    generation_prompt:null,generation_prompt_status:"not_retained",items:[item],
    prepared_jpeg_storage:{commit:item.prepared_jpeg.commit,path_prefix:prefix+"prepared-jpegs/"},
    originals_receipt:{commit:receiptCommit,path:prefix+"originals/receipt.json",sha256:hash(JSON.stringify(receipt))}};
  const inventory = {live:[],editing:[],originals:[{type:"file",path:sourcePath,sha:measured.source.blob,size:source.bytes}],
    jpegs:[{type:"file",path:jpegPath,sha:measured.jpeg.blob,size:jpeg.bytes}]};
  const requests = [];
  const github = {
    async fetch({url}) {
      const path = url.split("/"+repo)[1]; requests.push(path);
      if (path === "/git/ref/heads/main") return {ref:"refs/heads/main",object:{sha:main}};
      if (path === "/git/ref/heads/"+handoff.candidate_branch) return {ref:"refs/heads/"+handoff.candidate_branch,object:{sha:commit}};
      if (path === "/contents/"+prefix+"originals?ref="+commit) return {content:JSON.stringify(inventory.originals)};
      if (path === "/contents/"+prefix+"prepared-jpegs?ref="+commit) return {content:JSON.stringify(inventory.jpegs)};
      throw Error("Unexpected transport " + path);
    },
    async fetch_file({ref,path,encoding}) {
      requests.push([ref,path]);
      let data;
      if (ref === commit && path === prefix+"handoff.json") data = handoff;
      else if (ref === receiptCommit && path === prefix+"originals/receipt.json") data = receipt;
      else if (ref === "cast-source-drafts" && path === "drafts/cast-voices/"+rioProducer.model_id+".json") data = rioProducer;
      else if ([main,releaseCommit].includes(ref) && path === "content/photo_issues.json") {
        data = {schema_version:1,issues:ref === main ? inventory.live : inventory.editing};
      } else throw Error("Unexpected file " + ref + "/" + path);
      return {content:JSON.stringify(data),encoding};
    }
  };
  const params = {stockIssue,commit,releaseCommit,issue};
  const readback = {source:{status:"read",mime_type:"image/png",commit,path:sourcePath,sha256:source.sha256,bytes:source.bytes,
    width:source.width,height:source.height,image_url:dataUrl(measured.source.data)},
    jpeg:{sha:measured.jpeg.blob,encoding:"base64",content:measured.jpeg.data.toString("base64")}};
  const api = helper.create({runtime:{...bytes,sha256:hash},github});
  return {open:()=>api.openUnassignedStock(params),params,handoff,receipt,inventory,requests,item,
    cut:{sourceOrdinal:1,cutId:"09",importedAt:input.importedAt,readback}};
}

test("an editor can prepare an unassigned split-receipt original for a first draft at a different slug", async () => {
  const fixture = unassignedStockFixture(), {cut,item,params,handoff,requests} = fixture;
  const batch = await fixture.open(), reads = requests.length;
  const result = batch.prepareCut(cut), record = JSON.parse(result.files[2].content);
  assert.equal(result.status, "prepared_not_published");
  assert.equal(record.issue_slug, params.issue.slug);
  assert.deepEqual([record.cut_id,record.model_id,record.source.sha256,record.jpeg.sha256], ["09",rio.model_id,item.source_sha256,item.prepared_jpeg.sha256]);
  assert.equal(record.source.url, item.library.url);
  assert.equal(record.source.generation_turn, null);
  assert.ok(record.source.path.startsWith("maintenance/photo-issues/"+params.issue.slug+"/09/"));
  assert.deepEqual(record.recovery_reference, {source_issue:params.stockIssue, handoff_sha256:hash(JSON.stringify(handoff)),
    originals_receipt_commit:handoff.originals_receipt.commit, originals_receipt_path:handoff.originals_receipt.path,
    originals_receipt_sha256:handoff.originals_receipt.sha256, candidate_commit:params.commit, source_ordinal:1,
    source_path:cut.readback.source.path, jpeg_path:item.prepared_jpeg.path, original_blob_sha:result.files[0].sha,
    jpeg_blob_sha:cut.readback.jpeg.sha, handoff_path:"maintenance/photo-issue-plans/"+params.stockIssue+"/handoff.json"});
  assert.deepEqual(batch.prepareCut(cut),result);
  assert.throws(()=>batch.prepareCut({...cut,cutId:"08"}), /already selected/);
  assert.equal(requests.length,reads);
  assert.equal(requests.filter(req=>req[1]==="content/photo_issues.json").length,2);
  assert.ok(requests.length <= 15);
});

test("unassigned stock rejects reassignment, swapped evidence, published originals and repeated sources", async () => {
  const fixture = unassignedStockFixture(), {handoff,item,inventory,params,cut} = fixture;
  async function rejected() {
    await assert.rejects(async () => (await fixture.open()).prepareCut(cut));
  }
  for (const [object,key,bad] of [
    [handoff,"publication_number_assigned",14], [handoff,"source_candidate_only",false],
    [handoff,"source_chat_url",chat], [handoff,"model_id","another-model"],
    [handoff.originals_receipt,"sha256",hash("different original receipt")],
    [item,"formal_editorial_adoption","accepted"], [item,"source_ordinal",2],
    [inventory,"originals",[]], [item.prepared_jpeg,"git_blob_sha",sha1("another JPEG")],
    [item.prepared_jpeg,"commit",main],
    [cut.readback.source,"commit",main], [cut.readback.source,"path",item.prepared_jpeg.path],
    [cut.readback.source,"image_url","data:image/png;base64,AAAA"], [cut.readback.jpeg,"sha",sha1("other blob")],
    [handoff,"items",[item,{...item,source_ordinal:2}]], [params.issue,"state","published"],
    [inventory,"live",[{state:"published",images:{"01":{source_png_sha256:item.source_sha256}}}]],
    [inventory,"editing",[{...params.issue,model_id:"another-model"}]],
    [inventory,"editing",[{...params.issue,images:{"09":{source_sha256:hash("different original")}}}]],
    [inventory,"editing",[{...params.issue,images:{"02":{source_sha256:item.source_sha256}}}]]
  ]) {
    const before=object[key]; object[key]=bad;
    try { await rejected(); } finally { object[key]=before; }
  }
});

for (const [cut, ordinal, encoder] of [["03",11,"Pillow"],["09",3,"jpeg-js"]]) {
  test("prepares the retained " + encoder + " photo from original " + ordinal + " for cut " + cut, () => {
    const api = helper.create({runtime:{...bytes, sha256:hash}});
    const {receipt, input} = suppliedCutFixture(cut);
    const result = api.prepareSuppliedCut(input);
    assert.equal(result.status, "prepared_not_published");
    assert.deepEqual(api.prepareSuppliedCut(input), result);
    const record = result.files.find(file => file.path === rio.images[cut].provenance.receipt_path);
    const binary = result.files.filter(file => file !== record);
    assert.deepEqual(binary.map(file => [file.path,file.sha]).sort(), [
      [receipt.source.path,input.readback.source.blob], [receipt.jpeg.path,input.readback.jpeg.blob]
    ].sort());
    const formal = JSON.parse(record.content);
    for (const key of ["version","kind","issue_slug","cut_id","model_id","imported_at","review_reference_sha256",
                       "source","jpeg","transform","encoding_details"]) {
      assert.deepEqual(formal[key], receipt[key], key);
    }
    assert.deepEqual(formal.recovery_reference, receipt.recovery_reference);
    assert.equal(formal.recovery_reference.source_ordinal, ordinal);
    const {review, provenance, ...image} = rio.images[cut];
    assert.equal(review.decision, "accepted");
    assert.ok(!Object.hasOwn(formal, "review"));
    assert.deepEqual(result.image, {...image, provenance:{...provenance, receipt_sha256:hash(record.content)}});
  });
}

test("one cut cannot take another verified pair or an unverified original", () => {
  const api = helper.create({runtime:{...bytes, sha256:hash}});
  const {input} = suppliedCutFixture("03");
  const library = suppliedCutFixture("09").input;
  const other = library.readback;
  for (const readback of [{...input.readback, jpeg:other.jpeg}, {...input.readback, source:other.source},
                          {...input.readback, image_bytes_verified:false}]) {
    assert.throws(() => api.prepareSuppliedCut({...input, readback}));
  }
  const tampered = Buffer.from(input.readback.jpeg.data);
  tampered[tampered.length >> 1] ^= 1;
  for (const [field, data] of [["source",undefined], ["source",Buffer.alloc(0)], ["jpeg",tampered]]) {
    const readback = {...input.readback, [field]:{...input.readback[field], data}};
    assert.throws(() => api.prepareSuppliedCut({...input, readback}));
  }
  const swappedTurn = {...input.candidate.source, turn_key:"d2e5940d-aaab-4771-820c-adb13b317288"};
  assert.throws(() => api.prepareSuppliedCut({...input, candidate:{...input.candidate, source:swappedTurn}}));
  assert.throws(() => api.prepareSuppliedCut({...input, candidate:{...input.candidate, source_ordinal:3}}));
  assert.throws(() => api.prepareSuppliedCut({...library, candidate:{...library.candidate, source_ordinal:4}}));
});


test("individual Library share URLs retain their original identity without a separate Library ID", () => {
  const api = helper.create({runtime:{...bytes, sha256:hash}});
  const issue = JSON.parse(readLocal("content/photo_issues.json")).issues.find(i => i.slug === "winter-coat-swimsuit-saya-001");
  const producer = {model_id:issue.model_id, source_chat_url:"https://chatgpt.com/c/6af854c9-0598-83ee-8958-b762ea028909"};
  const saved = "maintenance/photo-issues/" + issue.slug;
  const receipts = ["03/receipt-supplied-8d62bc357aec-e36627a40cfc.json", "04/receipt-supplied-12aa2bc5e5d4-059225e2d0aa.json"]
    .map(path => JSON.parse(readLocal(saved + "/" + path)));
  for (const receipt of receipts) {
    const cutId = receipt.cut_id, source_ordinal = Number(cutId);
    // Stage the historically retained original/JPEG pair under a test-only candidate prefix.
    const prefix = "maintenance/photo-issue-plans/" + issue.slug + "/";
    const source = {...receipt.source, path:prefix + cutId + ".png"};
    const jpeg = {...receipt.jpeg, path:prefix + cutId + ".jpg"};
    const commit = sha1("test candidate " + cutId);
    const candidate = {issue:issue.slug, ...producer, commit, source_ordinal, source, jpeg, transform:receipt.transform};
    const archived = {...receipt.jpeg, path:saved + "/retired/" + cutId + "-" + jpeg.sha256 + ".jpg"};
    const readback = {commit, source_ordinal, image_bytes_verified:true,
      source:{...readbackFile(receipt.source,source.path), url:source.url}, jpeg:readbackFile(archived,jpeg.path)};
    const input = {issue, cutId, producer, candidate, readback, importedAt:receipt.imported_at};
    const output = api.prepareSuppliedCut(input);
    const formal = JSON.parse(output.files.find(f => f.content).content);
    assert.deepEqual(formal.source, receipt.source);
    assert.deepEqual(formal.jpeg, receipt.jpeg);
    const other = receipts.find(r => r.cut_id !== cutId);
    assert.throws(() => api.prepareSuppliedCut({...input, readback:{...readback, source:{...readback.source, url:other.source.url}}}));
    assert.throws(() => api.prepareSuppliedCut({...input, candidate:{...candidate, source:{...source, archive:{format:"zip"}}}}));
  }
});

function mioArchiveCut(cutId, source_ordinal) {
  const slug = "november-city-evening-mio-012";
  const issue = JSON.parse(readLocal("content/photo_issues.json")).issues.find(issue => issue.slug === slug);
  const qa = JSON.parse(readLocal("maintenance/photo-issues/" + slug + "/final-jpeg-qa.json"));
  const record = JSON.parse(readLocal(issue.images[cutId].provenance.receipt_path));
  const prefix = "maintenance/photo-issue-plans/" + slug + "/";
  const source = {...record.source, path:prefix + "stock-recovery/originals/" + record.source.sha256 + ".png"};
  const jpeg = {...record.jpeg, path:prefix + "prepared-jpegs/" + cutId + ".jpg"};
  const producer = {model_id:issue.model_id, source_chat_url:qa.source_chat_url};
  const candidate = {issue:slug, ...producer, commit:qa.source_commit, source_ordinal,
    handoff_path:prefix + "stock-recovery/receipt.json", source, jpeg,
    transform:record.transform, encoding_details:record.encoding_details};
  const readback = {commit:qa.source_commit, source_ordinal, image_bytes_verified:true,
    source:{...readbackFile(record.source, source.path), url:source.url, library_file_id:source.library_file_id,
      archive:{...source.archive}}, jpeg:readbackFile(record.jpeg, jpeg.path)};
  return {record, input:{issue, cutId, producer, candidate, readback, importedAt:record.imported_at}};
}

test("prepares selected originals from both retained Mio ZIPs using the committed PNG/JPEG bytes", () => {
  const api = helper.create({runtime:{...bytes, sha256:hash}});
  for (const [cutId, ordinal] of [["01",8], ["02",1]]) {
    const {record, input} = mioArchiveCut(cutId, ordinal);
    const output = api.prepareSuppliedCut(input);
    assert.equal(output.status, "prepared_not_published");
    const formal = JSON.parse(output.files.find(file => file.content).content);
    assert.deepEqual(formal.source, record.source);
    assert.deepEqual(formal.jpeg, record.jpeg);
    assert.equal(formal.recovery_reference.candidate_commit, input.readback.commit);
    assert.equal(formal.recovery_reference.source_ordinal, ordinal);
    assert.equal(output.files.find(file => file.path === record.source.path).sha, input.readback.source.blob);
    assert.equal(output.files.find(file => file.path === record.jpeg.path).sha, input.readback.jpeg.blob);
  }
});

test("the observed archive and member cannot be substituted, dropped or passed as an individual file", () => {
  const api = helper.create({runtime:{...bytes, sha256:hash}});
  const {input} = mioArchiveCut("01", 8);
  const {candidate, readback} = input;
  const original = candidate.source, measured = readback.source;
  const other = mioArchiveCut("02", 1).record.source;
  const prepare = (source, observed) => api.prepareSuppliedCut({...input,
    candidate:{...candidate, source}, readback:{...readback, source:observed}});
  const {archive, ...asIndividual} = original;
  const {archive:measuredArchive, ...withoutMember} = measured;
  assert.throws(() => prepare(asIndividual, measured), /Archive/);
  assert.throws(() => prepare(original, withoutMember), /Archive/);

  for (const field of Object.keys(archive)) {
    const changed = {format:"tar", filename:other.archive.filename, sha256:other.archive.sha256,
      bytes:other.archive.bytes, member:"another.png"}[field];
    assert.throws(() => prepare(original, {...measured, archive:{...measuredArchive, [field]:changed}}), /Archive/);
  }
  assert.throws(() => prepare(original, {...measured, library_file_id:other.library_file_id}), /Library/);
  for (const url of ["https://chatgpt.com/space/file/" + original.library_file_id,
                     "https://chatgpt.com/library/share/" + original.library_file_id, candidate.source_chat_url]) {
    assert.throws(() => prepare({...original, url}, {...measured, url}), /Library query URL/);
  }
  for (const invalid of [null, {format:"zip"}, {...archive, extra:true}, {...archive, sha256:"invalid"},
                         {...archive, bytes:0}, {...archive, bytes:"41555760"}, {...archive, filename:"original.png"},
                         {...archive, member:"folder/original.png"}, {...archive, member:"..\\original.png"},
                         {...archive, member:".private.png"}, {...archive, member:"original.jpg"},
                         {...archive, filename:" photos.zip"}, {...archive, member:"x".repeat(252) + ".png"}]) {
    assert.throws(() => prepare({...original, archive:invalid}, {...measured, archive:invalid}), /[Aa]rchive/);
  }
  const changedMember = Buffer.from(measured.data);
  changedMember[changedMember.length >> 1] ^= 1;
  assert.throws(() => prepare(original, {...measured, data:changedMember}), /original PNG changed/);
});
