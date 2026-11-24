const {test} = require("node:test");
const {join} = require("node:path");
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
      if (path.startsWith("/git/blobs/")) {
        const content = blobs.get(path.slice("/git/blobs/".length))?.toString("utf8");
        assert.ok(content?.startsWith('{"version":1,"status":"originals_'), "binary blobs require immutable fetch_file");
        return {url,content};
      }
      if (path === "") return {full_name:repo,private:false,visibility:"public"};
      if (path === "/git/ref/heads/main") return {ref:"refs/heads/main",object:{sha:state.liveMain ?? main}};
      if (path.startsWith("/branches/")) {
        const name = decodeURIComponent(path.slice("/branches/".length));
        const sha = name === "main" ? (state.liveMain ?? main) : branches.get(name);
        return {name, commit:{sha, commit:{tree:commits.get(sha)?.tree}}};
      }
      if (path === "/actions/workflows/free-cpu-scenes.yml")
        return {path:".github/workflows/free-cpu-scenes.yml",state:state.active ? "active" : "disabled_manually"};
      if (path === "/compare/main..." + main + "?per_page=1")
        return state.comparison ?? {base_commit:{sha:state.liveMain ?? main},merge_base_commit:{sha:main},
          status:state.liveMain && state.liveMain!==main ? "behind" : "identical"};
      if (path.startsWith("/compare/")) {
        const [base, head] = path.slice(9).split("?")[0].split("...");
        const branch = decodeURIComponent(base);
        if (branch.startsWith("photo-issue-plan/")) {
          const tip = branches.get(branch);
          let ancestor = tip;
          while (ancestor && ancestor !== head) ancestor = commits.get(ancestor)?.parents[0]?.sha;
          return {base_commit:{sha:tip,commit:{tree:commits.get(tip)?.tree}}, merge_base_commit:{sha:ancestor},
            status:tip === head ? "identical" : ancestor === head ? "behind" : "diverged"};
        }
        let cursor = head;
        while (cursor && cursor !== base) cursor = commits.get(cursor)?.parents[0]?.sha;
        return {merge_base_commit:{sha:cursor}, status:cursor === base ? "ahead" : "diverged"};
      }
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
      if (!state.discardUpdate) branches.set(branch_name,sha);
      if (state.lost) throw Error("lost response");
      return {sha};
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
    readWeeklyPng:async ({commit,path,expected_sha256}) => {
      state.reads.push("weekly PNG");
      const entry = trees.get(commits.get(commit).tree.sha).find(f => f.path === path);
      const input = blobs.get(entry.sha);
      assert.equal(hash(input), expected_sha256, "Numbered PNG bytes differ");
      return {structuredContent:{status:"read", mime_type:"image/png", commit, path,
        sha256:hash(input), bytes:input.length, ...bytes.inspectPng(input), image_url:dataUrl(input)}};
    },
    readCandidatePng:fileAdapter ? async () => {
      state.fileReads++;
      return {structuredContent:{status:"read",mime_type:"image/png",sha256:hash(input),bytes:input.length,...info,
        image_url:dataUrl(input),...state.fileMetadata}};
    } : undefined});
  return {api:createApi(),state,reopen:createApi,resetGuard,github,runtime};
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

test("producer checkpoint, append, finalization and lost replies fit fresh 16-callback cells",async t => {
  const {api,state,github,runtime}=setup();
  const input=packet(), branch="photo-issue-plan/"+input.issue, base="maintenance/photo-issue-plans/"+input.issue+"/";
  const first={...await api.uploadDataUrl(dataUrl(png),hash(png)),source_ordinal:1};
  const secondPng=largePng(20,20);
  const second={...await api.uploadDataUrl(dataUrl(secondPng),hash(secondPng)),source_ordinal:2};
  input.assets=[first];
  const maxima={};
  async function cell(options,continuation=null,packet=input) {
    const start=state.reads.length+state.writes.length;
    const fetchHelper=(name,symbol)=>{
      state.reads.push(name);
      const source=readFileSync(join(__dirname,name),"utf8");
      return new Function(source+"\nreturn "+symbol)();
    };
    const B=fetchHelper("native_image_bytes.js","NativeImageBytes");
    const S=fetchHelper("native_image_stage.js","NativeImageStage");
    const H=fetchHelper("photo_original_handoff.js","PhotoOriginalHandoff");
    // Same four cold source callbacks as weekly-original-transport. Offline suites use Node's SHA
    // after the pinned source lookup; set the source path to also exercise the full production loader.
    const loadDigest=async request=>{
      assert.equal(request.ref,B.SOURCES.sha.ref);assert.equal(request.path,B.SOURCES.sha.path);
      state.reads.push(request.path);
      if(process.env.MIHASSEI_TEST_SHA_SOURCE) return {result:{encoding:"utf-8",
        content:readFileSync(process.env.MIHASSEI_TEST_SHA_SOURCE,"utf8")}};
    };
    let verifier=runtime;
    if(process.env.MIHASSEI_TEST_SHA_SOURCE) verifier=await B.verificationRuntime(loadDigest);
    else await loadDigest(B.SOURCES.sha);
    const readCloudWorkflow=async({nonce})=>{
      state.reads.push("read_image_control");
      return {version:1,repository:repo,nonce,checked_at:new Date().toISOString(),
        path:".github/workflows/free-cpu-scenes.yml",state:state.active?"active":"disabled_manually"};
    };
    const {cloudStatus}=S.create({controlOnly:true,github,repository:repo,readCloudWorkflow});
    const fresh=H.create({github,runtime:verifier,cloudStatus});
    try {
      return await fresh.saveTransferStep(packet,options,continuation && JSON.parse(JSON.stringify(continuation)));
    } finally {
      const count=state.reads.length+state.writes.length-start;
      const phase=options.kind+"/"+(continuation?.phase??"tree");
      maxima[phase]=Math.max(maxima[phase]??0,count);
      assert.ok(count<=16,phase+" required "+count+" cold callbacks");
    }
  }
  const options={kind:"checkpoint",expectedHead:null};
  const tree=await cell(options);
  // Main moves on naturally between cold cells. The new branch may use its proven main ancestor.
  const advanced=sha1("new main");
  state.commits.set(advanced,{sha:advanced,parents:[{sha:main}],tree:{sha:baseTree}});
  state.liveMain=advanced;
  const writes=state.writes.length;
  state.active=false;
  await assert.rejects(cell(options,tree.checkpoint),/stopped/);
  assert.equal(state.writes.length,writes);
  state.active=true;
  const commit=await cell(options,tree.checkpoint);
  state.lost=true;
  const one=await cell(options,commit.checkpoint);
  assert.equal(one.status,"checkpoint_saved_pending_readback");
  assert.equal(one.commit,state.branches.get(branch));
  assert.equal((await cell(options,commit.checkpoint)).commit,one.commit);
  const append={kind:"checkpoint",expectedHead:one.commit};
  const noOpWrites=state.writes.length;
  assert.equal((await cell(append)).commit,one.commit);
  assert.equal(state.writes.length,noOpWrites);
  input.assets.push(second);
  const appendedTree=await cell(append);
  const forged=structuredClone(appendedTree.checkpoint);
  const files=state.trees.get(forged.plan.tree);
  const extra={path:"unrelated.txt",type:"blob",mode:"100644",sha:sha1("unrelated")};
  forged.plan.tree=sha1("forged transfer");
  state.trees.set(forged.plan.tree,[...files,extra]);
  const priorWrites=state.writes.length;
  await assert.rejects(cell(append,forged),/differs/);
  await assert.rejects(cell(append,appendedTree.checkpoint,{...input,source_chat_url:chat.replace("867","977")}),/Changed transfer/);
  assert.equal(state.writes.length,priorWrites);
  const appendedCommit=await cell(append,appendedTree.checkpoint);
  const two=await cell(append,appendedCommit.checkpoint);
  const receiptOptions={kind:"receipt",expectedHead:two.commit};
  const finalTree=await cell(receiptOptions), finalCommit=await cell(receiptOptions,finalTree.checkpoint);
  const final=await cell(receiptOptions,finalCommit.checkpoint);
  assert.equal(final.status,"staged_pending_readback");
  assert.deepEqual(Buffer.from(await api.readOriginal({issue:input.issue,...final,source_sha256:hash(secondPng)})),secondPng);
  const receiptHead=sha1("later unrelated change"),receiptTree=sha1("later files");
  state.trees.set(receiptTree,[...state.trees.get(state.commits.get(final.commit).tree.sha),extra]);
  state.commits.set(receiptHead,{sha:receiptHead,parents:[{sha:final.commit}],tree:{sha:receiptTree}});
  state.branches.set(branch,receiptHead);
  const recovery=await cell(receiptOptions,finalCommit.checkpoint);
  assert.equal(recovery.checkpoint.phase,"recover");
  const beforeRecovery=state.writes.length;
  assert.equal((await cell(receiptOptions,recovery.checkpoint)).commit,receiptHead);
  assert.equal(state.writes.length,beforeRecovery);
  const newer=await cell(options,commit.checkpoint,{...input,assets:[first]});
  await assert.rejects(cell(options,newer.checkpoint,{...input,assets:[first]}),/Concurrent transfer record differs/);
  const part=state.trees.get(receiptTree).find(f=>f.path.startsWith(base+"originals/")&&f.path.includes(".png.part"));
  part.sha=sha1("unrelated bytes");
  await assert.rejects(cell(receiptOptions,recovery.checkpoint),/Concurrent transfer files differ/);
  assert.equal(state.branches.get(branch),receiptHead);
  t.diagnostic(JSON.stringify(maxima));
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

test("restores only the chosen numbered PNG under the candidate lease in bounded cells", async () => {
  const source = largePng(), sha = hash(source), {api,state,resetGuard} = setup(source);
  const partial = await api.uploadDataUrl(dataUrl(source),sha,0,2);
  const end = await api.uploadDataUrl(dataUrl(source),sha,2,2);
  const input = {...packet(), assets:[{...partial, parts:[...partial.parts,...end.parts], source_ordinal:4}]};
  const staged = await api.publish(input, await api.prepare(input));
  const ref = {issue:input.issue,...staged,source_sha256:sha};
  const step = await api.readOriginalStep(ref,null,2);
  assert.equal(step.status, "readback_pending");
  const verified = await api.readOriginalStep(ref,step.state,2);
  const writes = state.writes.length;
  await assert.rejects(api.uploadNumberedOriginal(ref,step),/completed split/);
  await assert.rejects(api.uploadNumberedOriginal(ref,{...verified,bytes:new Uint8Array(source.length)}),/PNG changed/);
  state.active = false;
  await assert.rejects(api.uploadNumberedOriginal(ref,verified),/stopped/);
  assert.equal(writes, state.writes.length);
  state.active = true;

  async function cell(operation) {
    resetGuard();
    const before = state.writes.length + state.reads.length;
    const result = await operation();
    assert.ok(state.writes.length + state.reads.length - before <= 15);
    return result;
  }
  const upload = await cell(()=>api.uploadNumberedOriginal(ref,verified));
  assert.match(upload.path,/originals\/04\.png$/);
  const plan = await cell(()=>api.prepareNumberedOriginal(ref,upload,upload.base));
  const baseFiles = state.trees.get(state.commits.get(upload.base).tree.sha);
  const proposedFiles = state.trees.get(state.commits.get(plan.commit).tree.sha);
  const extra = {path:"other.txt",type:"blob",mode:"100644",sha:sha1("unowned")};
  proposedFiles.push(extra);
  await assert.rejects(api.publishNumberedOriginal(ref,upload,plan),/other candidate files/);
  proposedFiles.pop();
  const pathEntry = proposedFiles.find(f => f.path === upload.path), badBlob = sha1("wrong");
  state.blobs.set(badBlob,Buffer.from("wrong"));
  pathEntry.sha = badBlob;
  await assert.rejects(api.publishNumberedOriginal(ref,{...upload,blob:badBlob},plan),/bytes differ/);
  pathEntry.sha = upload.blob;
  const part = baseFiles.find(f => f.path.endsWith("part002"));
  part.mode = "120000";
  await assert.rejects(api.publishNumberedOriginal(ref,upload,plan),/changed candidate parts/);
  part.mode = "100644";
  baseFiles.push({path:upload.path,mode:"120000",type:"blob",sha:upload.blob});
  await assert.rejects(api.prepareNumberedOriginal(ref,upload,upload.base),/Occupied numbered/);
  baseFiles.pop();
  baseFiles.push({path:upload.path,mode:"100644",type:"blob",sha:badBlob});
  await assert.rejects(api.prepareNumberedOriginal(ref,upload,upload.base),/occupied numbered/);
  baseFiles.pop();

  state.active = false;
  await assert.rejects(api.publishNumberedOriginal(ref,upload,plan),/stopped/);
  assert.equal(state.branches.get(staged.branch),upload.base);
  state.active = true;
  state.lost = true;
  await assert.rejects(cell(()=>api.publishNumberedOriginal(ref,upload,plan)),/lost response/);
  assert.equal(state.branches.get(staged.branch),plan.commit);
  state.lost = false;
  const afterWrite = state.writes.length;
  const saved = await cell(()=>api.publishNumberedOriginal(ref,upload,plan));
  assert.equal(saved.commit,plan.commit);
  assert.deepEqual(Buffer.from(state.blobs.get(saved.blob)),source);
  const oldHead = {...upload,base:upload.base};
  await assert.rejects(api.prepareNumberedOriginal(ref,oldHead,upload.base),/branch moved/);
  const again = await cell(()=>api.uploadNumberedOriginal(ref,verified));
  const noChange = await cell(()=>api.prepareNumberedOriginal(ref,again,again.base));
  assert.equal(noChange.base,noChange.commit);
  assert.deepEqual(await cell(()=>api.publishNumberedOriginal(ref,again,noChange)),saved);
  assert.equal(state.writes.length,afterWrite);
});

test("numbered upload, prepare, submit and exact readback each fit 16 calls including cold imports", async () => {
  const {api,state,resetGuard} = setup(), input = packet(), sha = hash(png);
  input.assets = [{...await api.uploadDataUrl(dataUrl(png),sha),source_ordinal:2}];
  const staged = await api.publish(input,await api.prepare(input));
  const ref = {issue:input.issue,...staged,source_sha256:sha};
  // A producer's branch can be several commits ahead of its immutable split receipt.
  const base = sha1("advanced candidate");
  state.commits.set(base,{sha:base,tree:state.commits.get(staged.commit).tree,parents:[{sha:staged.commit}]});
  state.branches.set(staged.branch,base);
  const verified = await api.readOriginalStep(ref);
  async function cell(operation) {
    resetGuard();
    const before = state.reads.length + state.writes.length;
    try {return await operation();}
    finally {
      const calls = 4 + state.reads.length + state.writes.length - before;
      assert.ok(calls <= 16, "Cold verification imports and callbacks: " + calls);
    }
  }
  const upload = await cell(() => api.uploadNumberedOriginal(ref,verified));
  const plan = await cell(() => api.prepareNumberedOriginal(ref,upload,base));
  state.branches.set(staged.branch,main);
  await assert.rejects(cell(() => api.submitNumberedOriginal(ref,upload,plan)), /outside its candidate branch/);
  state.branches.set(staged.branch,base);
  state.discardUpdate = true;
  const submit = () => cell(() => api.submitNumberedOriginal(ref,upload,plan));
  const confirm = () => cell(() => api.confirmNumberedOriginal(ref,upload,plan));
  assert.equal((await submit()).status,"submitted_pending_branch_readback");
  const attempts = state.writes.length;
  await assert.rejects(confirm(),/update not confirmed/);
  assert.equal(state.writes.length,attempts);
  assert.equal(state.branches.get(staged.branch),base);
  state.discardUpdate = false;
  state.lost = true;
  await assert.rejects(submit(), /lost response/);
  state.lost = false;
  const saved = await confirm();
  assert.equal(saved.status,"numbered_original_staged");
  assert.equal(saved.commit,plan.commit);
  assert.equal(state.branches.get(staged.branch),saved.commit);
  assert.equal(state.writes.length,attempts+1);

  const landedFiles = state.trees.get(state.commits.get(saved.commit).tree.sha);
  landedFiles.push({path:"content/articles.json",type:"blob",mode:"100644",sha:sha1("unrelated change")});
  await assert.rejects(confirm(), /other candidate files/);
  landedFiles.pop();
  const later = sha1("later candidate with the same PNG");
  state.commits.set(later,{sha:later,tree:state.commits.get(saved.commit).tree,parents:[{sha:saved.commit}]});
  state.branches.set(staged.branch,later);
  await assert.rejects(confirm(), /moved beyond/);
  assert.equal(state.writes.length,attempts+1);
});

test("a cold JPEG conversion can read a larger numbered original without carrying part bytes", async () => {
  const source = largePng(1024, 980), sha = hash(source), encoded = dataUrl(source);
  const jpeg = Buffer.from([255,216,255,217]);
  const convertPhoto = input => {
    assert.deepEqual(Buffer.from(input), source);
    return {width:1024,height:980,bytes:jpeg,sha256:hash(jpeg),base64:jpeg.toString("base64")};
  };
  const {api,state,resetGuard} = setup(source,{convertPhoto});
  const parts = [];
  let slice;
  do {
    slice = await api.uploadDataUrl(encoded,sha,slice?.next ?? 0,2);
    parts.push(...slice.parts);
  } while (slice.next !== null);
  assert.equal(parts.length,6);
  const input = {...packet(), assets:[{...slice,parts,source_ordinal:1}]};
  const receipt = await api.publish(input,await api.prepare(input));
  const ref = {issue:input.issue,...receipt,source_sha256:sha};
  const verified = await api.readOriginalStep(ref);
  const uploaded = await api.uploadNumberedOriginal(ref,verified);
  const plan = await api.prepareNumberedOriginal(ref,uploaded,uploaded.base);
  const saved = await api.publishNumberedOriginal(ref,uploaded,plan);
  resetGuard();
  const count = () => state.writes.length + state.reads.length;
  const before = count();
  const prepared = await api.uploadReviewJpeg(ref,{numberedCommit:saved.commit});
  assert.equal(6 + count() - before,13);
  assert.equal(prepared.source_sha256,sha);
  assert.equal(prepared.jpeg.sha256,hash(jpeg));
  const first = await api.readOriginalStep(ref,null,3);
  assert.equal(first.status,"readback_pending");
  resetGuard();
  const beforeLast = count();
  const verifiedLast = await api.readOriginalStep(ref,first.state,3);
  assert.deepEqual(await api.uploadReviewJpeg(ref,verifiedLast),prepared);
  assert.equal(6 + count() - beforeLast,16);
  const writes = state.writes.length;
  await assert.rejects(api.uploadReviewJpeg(ref,{numberedCommit:"main"}), /Invalid numbered original/);
  state.blobs.set(saved.blob, png);
  await assert.rejects(api.uploadReviewJpeg(ref,{numberedCommit:saved.commit}));
  assert.equal(state.writes.length,writes);
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
  async function callbacks(expected, operation, imports = 4) {
    resetGuard();
    const {length:reads} = state.reads, {length:mutations} = state.writes;
    const result = await operation();
    const count = state.reads.length - reads + state.writes.length - mutations;
    assert.equal(count, expected);
    assert.ok(imports + count <= 16, "Fresh helper/dependency reads and callbacks exceed one cell");
    return result;
  }
  const prepared = await callbacks(6,()=>api.uploadReviewJpeg(ref,verified),6);
  const recoveredBlob = await callbacks(7,()=>api.uploadReviewJpeg(ref),6);
  assert.deepEqual(recoveredBlob,prepared);
  const plan = await callbacks(12,()=>api.prepareReviewJpeg(ref,prepared,staged.commit));
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
  await callbacks(12,()=>assert.rejects(api.publishReviewJpeg(ref,prepared,plan),/lost response/));
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

function originalTransportFixture({active = true, changedReadback = false, fileError = false} = {}) {
  const path = "assets/favicon-192.png", ref = "1d9b571b914d6c323a3821e46d23ae0d8e354f95";
  const file = readbackFile({path}, path, "6608aa987c11d56ea9d7910eb4c0770bb529da9d");
  assert.equal(file.sha256, "3713afa375160c13556a90f2347b151725ac5922498211d2d980c7c58176c40e");
  const calls = [];
  const github = {
    async fetch_file(args) {
      assert.deepEqual(args, {repository_full_name:repo, ref, path, encoding:"base64"});
      const bin = changedReadback && calls.includes("create_blob") ? png : file.data;
      calls.push("fetch_file");
      return {encoding:"base64", sha:file.blob, content:bin.toString("base64")};
    },
    async create_blob(args) {
      calls.push("create_blob");
      assert.deepEqual(args, {repository_full_name:repo, content:file.data.toString("base64"), encoding:"base64"});
      return {sha:file.blob};
    }
  };
  const handle = "fixture-file-handle";
  const readCandidatePng = async ({file:actual}) => {
    calls.push("readCandidatePng");
    assert.equal(actual, handle);
    if (fileError) return {isError:true, error_code:"BLOCKED_FILE_REFERENCE"};
    return {status:"read", mime_type:"image/png", sha256:file.sha256, bytes:file.bytes,
      ...bytes.inspectPng(file.data), image_url:dataUrl(file.data)};
  };
  const api = helper.create({github, readCandidatePng, runtime:{...bytes, sha256:hash},
    cloudStatus:async () => ({public:true, state:active ? "active" : "disabled", main_commit_sha:main, jpeg_bytes:123})});
  return {api, calls, file, handle};
}

test("original transport checks inline and file bytes by writing only the existing immutable blob", async () => {
  for (const route of ["inline_png", "file"]) {
    const {api, calls, file, handle} = originalTransportFixture();
    const result = await api.checkOriginalTransport({route, ...(route === "file" ? {file:handle} : {})});
    assert.deepEqual(result, {status:"verified", route, sha256:file.sha256, blob:file.blob, bytes:file.bytes});
    const fileReads = route === "file" ? ["readCandidatePng"] : [];
    assert.deepEqual(calls, ["fetch_file", ...fileReads, "create_blob", "fetch_file"]);
  }
});

test("original transport blocks a disabled workflow and rejects substituted readback bytes", async () => {
  const stopped = originalTransportFixture({active:false});
  assert.deepEqual(await stopped.api.checkOriginalTransport({route:"inline_png"}), {
    status:"blocked", route:"inline_png", operation:"uploadDataUrl", reason:"Cloud image operations stopped"
  });
  assert.deepEqual(stopped.calls, ["fetch_file"]);

  const changed = originalTransportFixture({changedReadback:true});
  assert.deepEqual(await changed.api.checkOriginalTransport({route:"inline_png"}), {
    status:"blocked", route:"inline_png", operation:"fetch_file_readback", reason:"Probe bytes changed"
  });
  assert.deepEqual(changed.calls, ["fetch_file", "create_blob", "fetch_file"]);
});

test("a missing or unreadable file handle cannot pass the original transport check via inline bytes", async () => {
  const {api, calls, handle} = originalTransportFixture({fileError:true});
  assert.deepEqual(await api.checkOriginalTransport({route:"file"}), {
    status:"blocked", route:"file", operation:"select_route", reason:"Missing file handle or readCandidatePng"
  });
  assert.deepEqual(calls, []);
  assert.deepEqual(await api.checkOriginalTransport({route:"file", file:handle}), {
    status:"blocked", route:"file", operation:"readCandidatePng/upload", reason:"Photo transport tool failed (BLOCKED_FILE_REFERENCE)"
  });
  assert.deepEqual(calls, ["fetch_file", "readCandidatePng"]);
});

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
  return {open:()=>api.openUnassignedStock(params),params,handoff,receipt,inventory,requests,item,github,
    cut:{sourceOrdinal:1,cutId:"09",importedAt:input.importedAt,readback}};
}

test("an editor can prepare an unassigned split-receipt original for a first draft at a different slug", async () => {
  const fixture = unassignedStockFixture(), {cut,item,params,handoff,requests} = fixture;
  const batch = await fixture.open(), reads = requests.length;
  assert.deepEqual(batch.nearSimilarGroups, []);
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

test("stock respects the editor's similar pairs in one feature, including a reopened draft", async () => {
  const {open, cut, item, inventory, handoff, params} = unassignedStockFixture();
  const groups = [[1,8], [2,6], [3,4]];
  handoff.editorial = {near_similar_source_ordinal_groups:groups};
  for (let ordinal=2; ordinal<=8; ordinal++) {
    handoff.items.push({...item, source_ordinal:ordinal, source_sha256:hash("original " + ordinal),
      prepared_jpeg:{...item.prepared_jpeg, sha256:hash("jpeg " + ordinal)}});
  }
  const batch = await open();
  assert.deepEqual(batch.nearSimilarGroups, groups);
  const first = batch.prepareCut(cut);
  assert.deepEqual(batch.prepareCut(cut), first);
  assert.throws(() => batch.prepareCut({...cut, cutId:"08", sourceOrdinal:8}), /Near-similar photos/);
  inventory.editing = [{...params.issue, images:{"09":first.image}}];
  const reopened = await open();
  assert.throws(() => reopened.prepareCut({...cut, cutId:"08", sourceOrdinal:8}), /Near-similar photos/);
  inventory.editing = [{...params.issue, slug:"previous-feature", images:{"08":{source_sha256:hash("original 8")}}}];
  assert.deepEqual((await open()).prepareCut(cut), first);
  for (const invalid of [null, 1, [[1]], [[1,1]], [[1,9]], [[1,"8"]]]) {
    handoff.editorial.near_similar_source_ordinal_groups = invalid;
    await assert.rejects(open, /near-similar/);
  }
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

// The 016 handoff kept numbered blobs and a later selected[] with a different page order.
// Exercise that format offline using the original bytes of another ten-cut release.
function reviewedSelectionFixture() {
  const issue = {...rio, slug:"editor-feature", state:"draft", images:{}};
  const prefix = "maintenance/photo-issue-plans/" + issue.slug + "/";
  const commit = sha1("pinned image pairs"), selectionCommit = sha1("selected ten"), releaseCommit = sha1("editor branch");
  const rows = [], selected = [], originals = [], files = new Map(), reads = [];
  for (const {id} of issue.shots) {
    const {input} = suppliedCutFixture(id);
    const number = String(11 - Number(id)).padStart(2, "0");
    const source = {...input.candidate.source, format:"png", path:prefix+"originals/"+number+".png", blob:input.readback.source.blob};
    const jpeg = {...input.candidate.jpeg, path:prefix+"jpeg/"+number+".jpg", blob:input.readback.jpeg.blob};
    rows.push({...input.candidate, candidate_number:number, issue:issue.slug, handoff_path:prefix+"handoff.json", source, jpeg});
    selected.push({cut_id:id, candidate_number:number, source_sha256:source.sha256, jpeg_sha256:jpeg.sha256,
      source_bytes:source.bytes, jpeg_bytes:jpeg.bytes, review:"offline selection fixture " + id});
    originals.push({type:"file", path:source.path, size:source.bytes, sha:source.blob});
    files.set(source.path, input.readback.source.data);
    files.set(jpeg.path, input.readback.jpeg.data);
  }
  const review = {status:"selected_pending_release_receipts", model_id:issue.model_id, candidate_commit:commit, issue_edition:"issue-003",
    face_and_body_reference_checked:true, byte_readback_count:10, reviewed_jpeg_count:14, previous_catalog_sha_overlap:0, selected};
  const handoff = {version:1, kind:"weekly_photo_candidate_handoff", issue:issue.slug, ...rioProducer,
    branch:"photo-issue-plan/"+issue.slug, handoff_path:prefix+"handoff.json", candidates:rows};
  const inventory = {live:[], editing:[], interrupt:null};
  const github = {
    async fetch({url}) {
      const path = url.split("/"+repo)[1];
      if (path === "/git/ref/heads/main") return {ref:"refs/heads/main", object:{sha:main}};
      if (path === "/contents/"+prefix+"originals?ref="+commit) return {content:JSON.stringify(originals)};
      throw Error("Unexpected request " + path);
    },
    async fetch_file({ref, path, encoding}) {
      let data;
      if (ref === selectionCommit && path === handoff.handoff_path) data = {...handoff, editorial_review:review};
      else if (ref === commit && path === handoff.handoff_path) data = handoff;
      else if (ref === "cast-source-drafts" && path === "drafts/cast-voices/"+issue.model_id+".json") data = rioProducer;
      else if ([releaseCommit,main].includes(ref) && path === "content/photo_issues.json")
        data = {schema_version:1, issues:ref === main ? inventory.live : inventory.editing};
      else if (ref === commit && encoding === "base64" && files.has(path)) {
        if (inventory.interrupt === path) throw Error("Interrupted JPEG read");
        return {encoding, sha:rows.find(row => row.jpeg.path === path).jpeg.blob, content:files.get(path).toString("base64")};
      } else throw Error("Unexpected file " + path);
      return {encoding,content:JSON.stringify(data)};
    }
  };
  async function readWeeklyPng({issue:slug, commit:ref, path, expected_sha256}) {
    assert.equal(slug, issue.slug);
    assert.equal(ref, commit);
    const source = rows.find(row => row.source.path === path).source;
    assert.equal(expected_sha256, source.sha256);
    reads.push(path);
    return {structuredContent:{...source, status:"read", mime_type:"image/png", commit, image_url:dataUrl(files.get(path))}};
  }
  const api = helper.create({runtime:{...bytes,sha256:hash}, github, readWeeklyPng});
  const open = () => api.openReviewedSelection({issue, selectionCommit, releaseCommit, importedAt:"2026-11-24T00:00:00Z"});
  return {open, rows, review, handoff, issue, inventory, files, reads, originals, github, readWeeklyPng, releaseCommit, selectionCommit};
}

test("saved editorial selection prepares ten existing pairs, resumes at a matching draft, and preserves real source ordinals", async () => {
  const {open, rows, issue, inventory, reads} = reviewedSelectionFixture();
  const initial = await open(), first = await initial.prepareCut("01");
  assert.deepEqual(initial.cutIds, issue.shots.map(s => s.id));
  inventory.interrupt = rows[1].jpeg.path;
  await assert.rejects(initial.prepareCut("02"), /Interrupted/);
  inventory.editing = [{...issue, images:{"01":first.image}}];
  inventory.interrupt = null;
  const resumed = await open();
  assert.deepEqual(resumed.existing, ["01"]);
  const results = [first];
  for (const cutId of resumed.cutIds) results.push(await resumed.prepareCut(cutId));
  assert.equal(reads.filter(path => path === rows[0].source.path).length, 1);
  assert.equal(results.length, 10);
  for (const [index,result] of results.entries()) {
    const row = rows[index], receipt = JSON.parse(result.files[2].content);
    assert.equal(receipt.cut_id, issue.shots[index].id);
    assert.equal(receipt.recovery_reference.source_ordinal, row.source_ordinal);
    assert.equal(result.files[0].sha, row.source.blob);
    assert.equal(result.files[1].sha, row.jpeg.blob);
    assert.equal(result.image.sha256, row.jpeg.sha256);
    assert.ok(!Object.hasOwn(result.image, "review"));
  }
});

test("numbered selection resolves similarity by source ordinal and refuses ambiguous archive ordinals", async () => {
  const {open, rows, handoff} = reviewedSelectionFixture();
  rows.forEach((row, index) => { row.source_ordinal = index + 1; });
  handoff.editorial = {near_similar_source_ordinal_groups:[[1,8]]};
  await assert.rejects(open, /Near-similar photos/);
  rows[1].source_ordinal = 1;
  await assert.rejects(open, /ambiguous near-similar/);
});

test("saved selection refuses substitutions, duplicate cuts, occupied releases and corrupted pairs", async () => {
  const {open, rows, review, inventory, issue, files, originals} = reviewedSelectionFixture();
  for (const [obj, field, wrong] of [
    [review, "face_and_body_reference_checked", false], [review.selected[0], "cut_id", "02"],
    [review.selected[0], "source_sha256", rows[2].source.sha256], [rows[1], "candidate_number", rows[0].candidate_number],
    [inventory, "live", [{...issue, state:"published"}]],
    [inventory, "live", [{...issue, slug:"older", state:"published", images:{"01":{source_sha256:rows[1].source.sha256}}}]],
    [inventory, "editing", [{...issue, images:{"05":{source_sha256:rows[0].source.sha256,sha256:rows[0].jpeg.sha256}}}]],
  ]) {
    const old = obj[field]; obj[field] = wrong;
    await assert.rejects(open);
    obj[field] = old;
  }
  originals[0].sha = sha1("substitute");
  await assert.rejects(open, /Git blob changed/);
  originals[0].sha = rows[0].source.blob;
  const bad = Buffer.from(files.get(rows[0].jpeg.path));
  bad[40] ^= 1; files.set(rows[0].jpeg.path, bad);
  await assert.rejects((await open()).prepareCut("01"), /JPEG bytes changed/);
});

test("fresh bounded cells resume the Git packet after lost responses and build only the selected release files", async () => {
  const f = reviewedSelectionFixture(), {issue, selectionCommit} = f;
  const ref = {slug:issue.slug, edition:"issue-003", selectionCommit};
  const branch = "photo-issue/issue-003", path = "maintenance/photo-issue-plans/" + issue.slug + "/release-packet.json";
  const history = new Map(), treeSha = sha1("release tree");
  let head = f.releaseCommit, liveMain = main, lost = false, occupied = false, competing = false, stopped = false, proposed;
  let unrelatedEdit = false, diverged = false;
  // Recreate both helpers on every operation; charge the six source/dependency reads before entry.
  async function cell(work, expectedCalls) {
    let calls = 6;
    function transportCall() { assert.ok(++calls <= 16, "Exceeded cold cell limit"); }
    const github = {
      async fetch({url}) {
        transportCall();
        const apiPath = url.split("/"+repo)[1];
        if (apiPath === "") return {full_name:repo, private:false,visibility:"public"};
        if (apiPath === "/branches/"+branch || apiPath === "/branches/photo-issue/issue-004") {
          return {name:apiPath.slice("/branches/".length),commit:{sha:head,commit:{tree:{sha:treeSha}}}};
        }
        if (apiPath === "/branches/main") return {name:"main",commit:{sha:liveMain,commit:{tree:{sha:baseTree}}}};
        if (apiPath === "/git/ref/heads/main") return {ref:"refs/heads/main",object:{sha:liveMain}};
        if (apiPath === "/git/commits/"+liveMain || apiPath === "/git/commits/"+head) {
          const sha = apiPath.slice(13);
          return {sha,tree:{sha:sha===liveMain ? baseTree : treeSha}};
        }
        if (apiPath === "/git/trees/"+baseTree+"?recursive=1") return {sha:baseTree,truncated:false,tree:[]};
        if (apiPath === "/git/trees/"+treeSha+"?recursive=1") {
          const tree = history.has(head) ? [{path, type:"blob",mode:"100644",sha:history.get(head).sha}] : [];
          if (occupied) tree.push({path:"assets/generated/photo-issue-"+issue.slug+"-01.jpg",type:"blob",mode:"100644",sha:f.rows[0].jpeg.blob});
          return {sha:treeSha,truncated:false,tree};
        }
        if (apiPath === "/compare/"+f.releaseCommit+"..."+head+"?per_page=1") {
          const files = [{filename:path,status:"added",sha:history.get(head).sha}];
          if (unrelatedEdit) files.push({filename:"content/photo_issues.json", status:"modified"});
          return {status:diverged ? "diverged" : "ahead",merge_base_commit:{sha:f.releaseCommit},files};
        }
        if (apiPath === "/actions/workflows/free-cpu-scenes.yml") return {path:".github/workflows/free-cpu-scenes.yml",state:stopped ? "disabled_manually" : "active"};
        return f.github.fetch({url});
      },
      async fetch_file(args) {
        transportCall();
        if (args.path === path) {
          const entry = history.get(args.ref === branch ? head : args.ref);
          assert.ok(entry);
          return {...entry,encoding:"utf-8"};
        }
        if (history.has(args.ref) && args.path === "content/photo_issues.json") args = {...args,ref:f.releaseCommit};
        if (args.ref === liveMain && args.path === "content/photo_issues.json") args = {...args,ref:main};
        return f.github.fetch_file(args);
      },
      async create_file(args) {
        transportCall();
        assert.equal(args.branch,branch); assert.equal(args.path,path); assert.equal(history.size,0);
        head = sha1("empty packet");
        history.set(head,{sha:sha1(args.content),content:args.content});
        if (lost) throw Error("create response lost after commit");
        return {commit_sha:head};
      },
      async update_file(args) {
        transportCall();
        assert.equal(args.branch,branch); assert.equal(args.path,path); assert.equal(args.sha,history.get(head).sha);
        if (competing) throw Error("409 stale blob");
        head = sha1([head,args.content]);
        history.set(head,{sha:sha1(args.content),content:args.content});
        if (lost) throw Error("response lost after commit");
        return {commit_sha:head};
      },
      async create_tree(args) {
        transportCall();
        proposed = args;
        return {sha:sha1(args)};
      },
      create_blob(){throw Error("unexpected upload");}, create_branch(){throw Error("unexpected branch");},
      create_commit(){throw Error("unexpected commit");}
    };
    const runtime = {...bytes, sha256:hash, convert(){throw Error("unexpected conversion");}};
    const stager = stage.create({runtime, github, repository:repo});
    const api = helper.create({runtime, github, cloudStatus:stager.cloudStatus,
      async readWeeklyPng(args) { transportCall(); return f.readWeeklyPng(args); }});
    const result = await work(api);
    if (expectedCalls) assert.equal(calls,expectedCalls);
    return result;
  }
  const preflight = () => cell(async api => (await api.openReviewedSelection({
    issue, selectionCommit, releaseCommit:head, importedAt:"2026-11-24T00:00:00Z"})).plan,13);
  const initial = await preflight();
  const start = plan => cell(api => api.startReviewedPacket(plan),15);
  lost = true;
  await assert.rejects(cell(api => api.startReviewedPacket(initial)),/create response lost/);
  lost = false;
  assert.deepEqual(await start(initial),{commit:head,next:"01"});
  const next = () => cell(api => api.continueReviewedPacket(ref),15);
  lost = true;
  await assert.rejects(next, /response lost/);
  lost = false;
  const second = await next();
  assert.equal(second.prepared,2);
  assert.equal(f.reads.filter(path => path===f.rows[0].source.path).length,1);
  const afterTwo = head;
  const partialPlan = await preflight();
  assert.deepEqual(await start(partialPlan),{commit:afterTwo,next:"03"});
  assert.deepEqual(await start(initial),{commit:afterTwo,next:"03"});
  for (const changed of [{selectionCommit:main}, {edition:"issue-004"}, {selectionHash:hash("different")},
    {importedAt:"2026-11-24T03:00:00Z"}, {candidateCommit:main}]) {
    await assert.rejects(start({...partialPlan,...changed}), /release packet/);
  }
  diverged = true;
  await assert.rejects(start(partialPlan), /Unverified release changes/);
  diverged = false; unrelatedEdit = true;
  await assert.rejects(start(initial), /current head/);
  assert.deepEqual(await start(partialPlan),{commit:afterTwo,next:"03"});
  unrelatedEdit = false;
  const record = history.get(head);
  const damaged = JSON.parse(record.content);
  damaged.prepared["02"] = hash("different receipt");
  history.set(head,{sha:sha1(damaged),content:JSON.stringify(damaged)});
  await assert.rejects(start(partialPlan), /changed verified receipt/);
  history.set(head,record);
  liveMain = sha1("new main");
  await assert.rejects(start(partialPlan), /Main moved/);
  assert.deepEqual(await start(await preflight()),{commit:afterTwo,next:"03"});
  competing = true;
  await assert.rejects(next,/409 stale/);
  competing = false; stopped = true;
  await assert.rejects(next,/stopped/);
  await assert.rejects(start(await preflight()),/stopped/);
  assert.equal(head,afterTwo);
  stopped = false;
  for (let i=3; i<=10; i++) assert.equal((await next()).prepared,i);
  assert.equal((await cell(api=>api.continueReviewedPacket(ref),7)).next,null);
  await assert.rejects(cell(api=>api.continueReviewedPacket({...ref, selectionCommit:main})), /Unrecognized release packet/);
  const fresh = await preflight();
  assert.deepEqual(await start(fresh),{commit:head,next:null});
  ref.commit = head;
  occupied = true;
  await assert.rejects(cell(api=>api.prepareReviewedTree(ref,fresh)), /Occupied release destination/);
  occupied = false;
  const tree = await cell(api=>api.prepareReviewedTree(ref,fresh),15);
  assert.equal(tree.cuts,10);
  assert.equal(proposed.base_tree_sha,treeSha);
  assert.equal(proposed.tree_elements.length,31);
  assert.deepEqual(proposed.tree_elements.at(-1),{path,mode:"100644",type:"blob",sha:null});
  assert.equal(new Set(proposed.tree_elements.map(entry=>entry.path)).size,31);
  const readback = await cell(api=>api.readReviewedRelease(ref),8);
  assert.equal(Object.keys(readback.images).length,10);
  assert.equal(history.size,11);
});

test("fresh bounded cells stage mixed shoots on one release and recover the exact cut after a lost update", async () => {
  const indoor = unassignedStockFixture(), beach = suppliedCutFixture("03").input;
  const {issue, releaseCommit} = indoor.params, edition = "issue-003", branch = "photo-issue/"+edition;
  const stockIssue = "separate-beach-shoot", prefix = "maintenance/photo-issue-plans/"+stockIssue+"/";
  const commit = sha1("beach source"), candidateNumber = "02", importedAt = "2026-11-24T00:00:00Z";
  const source = {...beach.candidate.source, format:"png",path:prefix+"originals/02.png",blob:beach.readback.source.blob};
  if (source.image_number) source.image_number = 1;
  const jpeg = {...beach.candidate.jpeg,path:prefix+"jpeg/02.jpg",blob:beach.readback.jpeg.blob};
  const row = {...beach.candidate,issue:stockIssue,handoff_path:prefix+"handoff.json",source_ordinal:1,candidate_number:candidateNumber,source,jpeg};
  const stock = {version:1,kind:"weekly_photo_candidate_handoff",issue:stockIssue,branch:"photo-issue-plan/"+stockIssue,
    handoff_path:row.handoff_path,...rioProducer,candidates:[row]};
  let head = releaseCommit, nowMain = main, active = true, loseReply = false, extraChange = false;
  const trees = new Map([[releaseCommit,[]]]), commits = new Map(), contents = new Map();
  const originals = [{path:source.path,type:"file",sha:source.blob,size:source.bytes}];
  const read = (text) => JSON.parse(JSON.stringify(text));
  async function cell(operation) {
    let calls = 6;
    function call() { assert.ok(++calls <= 16, "Exceeded 16 cold callbacks"); }
    const github = {
      async fetch({url}) {
        call();
        const path = url.split("/"+repo)[1];
        if (path === "/branches/"+branch) return {name:branch,commit:{sha:head,commit:{tree:{sha:head}}}};
        if (path === "/git/ref/heads/"+branch) return {ref:"refs/heads/"+branch,object:{sha:head}};
        if (path === "/git/ref/heads/main") return {ref:"refs/heads/main",object:{sha:nowMain}};
        for (const [candidateBranch, pinned] of [[indoor.handoff.candidate_branch,indoor.params.commit],[stock.branch,commit]]) {
          const later = sha1("later "+pinned);
          if (path === "/git/ref/heads/"+candidateBranch) return {ref:"refs/heads/"+candidateBranch,object:{sha:later}};
          if (path === "/compare/"+pinned+"..."+later+"?per_page=1") return {status:"ahead",merge_base_commit:{sha:pinned}};
        }
        if (path === "/contents/"+prefix+"originals?ref="+commit) return originals;
        if (path.startsWith("/git/commits/")) {
          const rev = path.split("/").pop(); assert.ok(trees.has(rev));
          return {sha:rev,tree:{sha:rev}};
        }
        if (path.startsWith("/git/trees/")) {
          const rev=path.split("/").pop().split("?")[0]; assert.ok(trees.has(rev));
          return {sha:rev,truncated:false,tree:trees.get(rev)};
        }
        if (path.startsWith("/compare/")) {
          const [base, rev] = path.slice(9).split("?")[0].split("...");
          assert.equal(commits.get(rev).base,base);
          const files = trees.get(rev).filter(f=>!trees.get(base).some(old=>old.path===f.path))
            .map(f=>({filename:f.path,sha:f.sha,status:"added"}));
          if (extraChange) files.push({filename:"README.md",status:"modified"});
          return {status:"ahead",total_commits:1,merge_base_commit:{sha:base},commits:[{sha:rev,parents:[{sha:base}]}],files};
        }
        return indoor.github.fetch({url});
      },
      async fetch_file(args) {
        call();
        if (args.ref === commit && args.path === row.handoff_path) return {encoding:"utf-8",content:JSON.stringify(stock)};
        if (args.ref === commit && args.path === jpeg.path) return {encoding:"base64",sha:jpeg.blob,content:beach.readback.jpeg.data.toString("base64")};
        if (args.ref === indoor.params.commit && args.path === indoor.item.prepared_jpeg.path) return indoor.cut.readback.jpeg;
        if (trees.has(args.ref) && args.path === "content/photo_issues.json") return {encoding:"utf-8",content:'{"schema_version":1,"issues":[]}'};
        const file = trees.get(args.ref)?.find(f=>f.path===args.path);
        if (file) return {encoding:"utf-8",sha:file.sha,content:contents.get(file.sha)};
        return indoor.github.fetch_file(args);
      },
      async create_tree({base_tree_sha:base,tree_elements:entries}) {
        call(); const tree = sha1([base,entries]), list = [...trees.get(base)];
        for (const f of entries) {
          assert.ok(!list.some(e=>e.path===f.path));
          const sha = f.sha ?? sha1(f.content); if (f.content) contents.set(sha,f.content);
          list.push({path:f.path, type:f.type, mode:f.mode, sha});
        }
        trees.set(tree,list); return {sha:tree};
      },
      async create_commit({tree_sha:tree,parent_sha:base}) {
        call(); const rev = sha1([tree,base]); commits.set(rev,{base}); trees.set(rev,trees.get(tree)); return {sha:rev};
      },
      async update_ref({branch_name,sha,force,expected_sha}) {
        call(); assert.equal(branch_name,branch);assert.equal(force,false);assert.equal(expected_sha,head);
        head=sha; if(loseReply) throw Error("response lost"); return {object:{sha}};
      }
    };
    const api = helper.create({github,runtime:{...bytes,sha256:hash},cloudStatus:async()=>{
      for(let i=0;i<5;i++) call();
      return {state:active?"active":"stopped",public:true,main_commit_sha:nowMain,jpeg_bytes:0};
    },readWeeklyPng:async args=>{
      call(); if (args.issue===indoor.params.stockIssue) return indoor.cut.readback.source;
      assert.deepEqual(args,{issue:stockIssue,commit,path:source.path,expected_sha256:source.sha256});
      return {status:"read",mime_type:"image/png",commit,path:source.path,sha256:source.sha256,width:source.width,
        height:source.height,bytes:source.bytes,image_url:dataUrl(beach.readback.source.data)};
    }});
    return read(await operation(api));
  }
  const blockedHash = hash("another photo in the indoor near-twin group");
  indoor.handoff.items.push({...indoor.item,source_ordinal:2,source_sha256:blockedHash,
    prepared_jpeg:{...indoor.item.prepared_jpeg,sha256:hash("another jpeg")}});
  indoor.handoff.editorial={near_similar_source_ordinal_groups:[[1,2]]};
  const first = await cell(async api=>(await api.openUnassignedStock(indoor.params)).planCut({sourceOrdinal:1,cutId:"01",importedAt}));
  const firstCut = await cell(api=>api.readStockCut(first));
  const tree = await cell(api=>api.prepareStockCutTree(edition,first));
  const firstCommit = await cell(api=>api.commitStockCutTree(first,tree));
  const saveFirst = ()=>cell(api=>api.publishStockCut(edition,first,firstCommit));
  extraChange=true;
  await assert.rejects(saveFirst,/unverified release/);
  extraChange=false;active=false;
  await assert.rejects(saveFirst,/stopped/);
  active=true;nowMain=sha1("unrelated new main");
  await assert.rejects(saveFirst,/Main moved/);
  nowMain=main;loseReply=true;
  await assert.rejects(saveFirst,/response lost/);
  loseReply=false;
  assert.deepEqual((await saveFirst()).image,firstCut.image);
  const makeSecond = (cutId="02")=>cell(api=>api.planNumberedStockCut({issue,stockIssue,commit,releaseCommit:head,
    cutId,candidateNumber,importedAt}));
  stock.candidates.push({...row,candidate_number:"08",source_ordinal:2,source:{...source,sha256:first.candidate.source.sha256},
    jpeg:{...jpeg,sha256:first.candidate.jpeg.sha256}});
  stock.editorial={near_similar_source_ordinal_groups:[[1,2]]};
  const similar = await makeSecond();
  await assert.rejects(cell(api=>api.prepareStockCutTree(edition,similar)),/near-similar/);
  delete stock.editorial;stock.candidates.pop();
  const sourceHash = source.sha256;
  source.sha256 = blockedHash;
  const copiedTwin = await makeSecond();
  await assert.rejects(cell(api=>api.checkStockSiblings(copiedTwin)),/earlier selection excludes/);
  source.sha256 = sourceHash;
  const second = await makeSecond(), secondCut = await cell(api=>api.readStockCut(second));
  await assert.rejects(cell(api=>api.prepareStockCutTree(edition,second)),/Check earlier stock/);
  const siblings = await cell(api=>api.checkStockSiblings(second));
  const secondTree = await cell(api=>api.prepareStockCutTree(edition,second,siblings));
  const secondCommit = await cell(api=>api.commitStockCutTree(second,secondTree));
  await cell(api=>api.publishStockCut(edition,second,secondCommit));
  const firstReceived = JSON.parse(firstCut.files[2].content), secondReceived = JSON.parse(secondCut.files[2].content);
  assert.deepEqual([firstReceived.recovery_reference.source_ordinal,secondReceived.recovery_reference.source_ordinal],[1,1]);
  assert.equal(secondReceived.recovery_reference.source_path,prefix+"originals/02.png");
  assert.equal(secondReceived.recovery_reference.source_issue,stockIssue);
  assert.equal(firstReceived.recovery_reference.originals_receipt_sha256,indoor.handoff.originals_receipt.sha256);
  assert.equal(trees.get(head).length,6);
  assert.deepEqual(await cell(api=>api.readStagedStockCut({issue,edition,cutId:"01",commit:firstCommit.commit})),firstCut.image);
  assert.deepEqual(await cell(api=>api.readStagedStockCut({issue,edition,cutId:"01",commit:head})),firstCut.image);
  assert.deepEqual(await cell(api=>api.readStagedStockCut({issue,edition,cutId:"02",commit:head})),secondCut.image);
  assert.equal((await cell(api=>api.prepareStockCutTree(edition,first))).commit,head);
  const again=await makeSecond("03");
  await assert.rejects(cell(api=>api.prepareStockCutTree(edition,again)),/Repeated/);
  await assert.rejects(cell(api=>api.readStockCut({...second,candidate:{...row,commit,source:{...source,path:"README.md"}}})),/Invalid or changed/);
  // Nine checked sibling receipts still fit a cold cell. Their individual bytes never enter the conversation.
  const ninthPlan = await makeSecond("10"), originalTree = trees.get(head);
  const extras = [];
  for (let n=3;n<=9;n++) {
    const id = String(n).padStart(2,"0"), path=secondCut.image.provenance.receipt_path.replace("/02/","/"+id+"/");
    const contentsText = JSON.stringify({...secondReceived,cut_id:id});
    const blob = sha1(contentsText);
    contents.set(blob,contentsText);
    extras.push({path,sha:blob,type:"blob",mode:"100644"});
  }
  trees.set(head,[...originalTree,...extras]);
  assert.match(await cell(api=>api.checkStockSiblings(ninthPlan)),/^[0-9a-f]{64}$/);
  trees.set(head,originalTree);
  await assert.rejects(cell(api=>api.publishStockCut("issue-004",second,secondCommit)),/Wrong stock commit/);
});
