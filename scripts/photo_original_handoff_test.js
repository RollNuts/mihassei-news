const {test} = require("node:test");
const assert = require("node:assert/strict");
const {createHash} = require("node:crypto");
const {crc32, deflateSync} = require("node:zlib");
const bytes = require("./native_image_bytes.js");
const helper = require("./photo_original_handoff.js");
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

function setup(input = png, {fileAdapter = true} = {}) {
  const blobs = new Map(), trees = new Map([[baseTree,[]]]), branches = new Map();
  const commits = new Map([[main,{sha:main,tree:{sha:baseTree},parents:[]}]]);
  const state = {active:true,lost:false,writes:[],reads:[],branches,trees,blobs,commits,
    fileReads:0,fileMetadata:{},stopAfterBlobs:Infinity,stopAfterWrites:Infinity};
  const github = {
    async fetch({url}) {
      const path = url.slice(("https://api.github.com/repos/" + repo).length);
      if (path.startsWith("/git/commits/")) return commits.get(path.slice(13));
      if (path.startsWith("/git/trees/")) {
        const id = path.split("/")[3].split("?")[0];
        return {sha:id,truncated:false,tree:trees.get(id)};
      }
      const branch = path.slice("/git/ref/heads/".length);
      return {ref:"refs/heads/"+branch,object:{sha:branches.get(branch)}};
    },
    async fetch_file({ref,path,encoding}) {
      if (ref === "cast-source-drafts") return {encoding,content:JSON.stringify({model_id:"kiritani-sumire",source_chat_url:chat})};
      const tree = trees.get(commits.get(ref).tree.sha);
      const entry = tree.find(e => e.path === path);
      return {encoding,content:entry.content ?? blobs.get(entry.sha).toString("base64")};
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
  const createApi = () => helper.create({runtime:{...bytes,sha256:hash},github,
    cloudStatus:async () => ({state:state.active && state.writes.length < state.stopAfterWrites &&
      state.writes.filter(name => name === "create_blob").length < state.stopAfterBlobs
      ?"active":"disabled",public:true,main_commit_sha:main,jpeg_bytes:123}),
    readCandidatePng:fileAdapter ? async () => {
      state.fileReads++;
      return {structuredContent:{status:"read",mime_type:"image/png",sha256:hash(input),bytes:input.length,...info,
        image_url:dataUrl(input),...state.fileMetadata}};
    } : undefined});
  return {api:createApi(),state,reopen:createApi};
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

test("lost checkpoint create and update acknowledgements recover only matching remote records", async () => {
  const {api,state}=setup(),input=packet(),sha=hash(png);
  input.assets=[{...await api.uploadDataUrl(dataUrl(png),sha),source_ordinal:1}];
  state.lost=true;
  const plan=await api.prepareCheckpoint(input),saved=await api.publishCheckpoint(input,plan);
  assert.equal((await api.publishCheckpoint(input,plan)).commit,saved.commit);
  assert.deepEqual(Buffer.from(await api.readCheckpointOriginal({issue:input.issue,...saved,source_sha256:sha})),png);
  const update=await api.prepareCheckpoint(input,saved.commit),advanced=await api.publishCheckpoint(input,update);
  assert.equal(advanced.commit,state.branches.get("photo-issue-plan/"+input.issue));
  assert.deepEqual(Buffer.from(await api.readCheckpointOriginal({issue:input.issue,...advanced,source_sha256:sha})),png);
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
  state.active=true;state.stopAfterWrites=state.writes.length+1;
  await assert.rejects(api.publishCheckpoint(input,plan),/stopped|Unverified/);
  assert.equal(state.branches.size,0);
  state.stopAfterWrites=Infinity;
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
