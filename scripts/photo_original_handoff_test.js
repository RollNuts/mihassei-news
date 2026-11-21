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
function largePng() {
  const header=Buffer.from(png.subarray(16,29));
  header.writeUInt32BE(1024);header.writeUInt32BE(512,4);
  return Buffer.concat([png.subarray(0,8),pngChunk("IHDR",header),
    pngChunk("IDAT",deflateSync(Buffer.alloc(512*(1+1024*3)), {level:0})),pngChunk("IEND",Buffer.alloc(0))]);
}

function setup(input = png, {fileAdapter = true} = {}) {
  const blobs = new Map(), trees = new Map([[baseTree,[]]]), branches = new Map();
  const commits = new Map([[main,{sha:main,tree:{sha:baseTree},parents:[]}]]);
  const state = {active:true,lost:false,writes:[],branches,trees,blobs,commits,
    fileReads:0,fileMetadata:{},stopAfterBlobs:Infinity};
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
      const entries = [...trees.get(base_tree_sha),...tree_elements];
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
      branches.set(branch_name,sha);return {sha};
    }
  };
  for (const name of ["create_blob","create_tree","create_commit","create_branch","update_ref"]) {
    const operation = github[name]; github[name] = async args => {
      assert.equal(args.repository_full_name,repo);
      state.writes.push(name);return operation(args);
    };
  }
  const info = bytes.inspectPng(input);
  const api = helper.create({runtime:{...bytes,sha256:hash},github,
    cloudStatus:async () => ({state:state.active && state.writes.filter(name => name === "create_blob").length < state.stopAfterBlobs
      ?"active":"disabled",public:true,main_commit_sha:main,jpeg_bytes:123}),
    readCandidatePng:fileAdapter ? async () => {
      state.fileReads++;
      return {structuredContent:{status:"read",mime_type:"image/png",sha256:hash(input),bytes:input.length,...info,
        image_url:dataUrl(input),...state.fileMetadata}};
    } : undefined});
  return {api,state};
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
