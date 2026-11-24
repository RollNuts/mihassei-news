const {test} = require("node:test");
const assert = require("node:assert/strict");
const {createHash} = require("node:crypto");
const {readFileSync} = require("node:fs");
const {join} = require("node:path");

const repository = "RollNuts/mihassei-news";
const actors = {
  "shioba-rio": "https://chatgpt.com/c/6af86744-fb88-83e8-991f-4e2e9d3d8a7f",
  "kiritani-sumire": "https://chatgpt.com/c/6af867a7-8a9c-83ee-b05d-abb1eadd9f5d"
};
const digest = value => createHash("sha1").update(JSON.stringify(value)).digest("hex");
const blob = content => createHash("sha1").update("blob " + Buffer.byteLength(content) + "\0").update(content).digest("hex");
const sibling = "maintenance/photo-issue-plans/prior-shoot/handoff.json";

// Model the connector's JSON-valued fetch response, exact ref creation and non-forced lease.
function fixture() {
  const files = {[sibling]:"original handoff"};
  const root = digest(files), main = digest(["main", root]);
  const trees = new Map([[root, files]]), commits = new Map([[main, {tree:root}]]);
  const refs = new Map([["main", main]]);
  const rollout = digest("merged exclusive routing");
  const state = {clock:"2026-11-29T00:30:00+09:00", active:true, events:[], cells:[], afterRef:null, afterBranch:null,
    earlierMarker:true, currentMarker:true, mergedAt:"2026-11-26T03:00:00Z", corruptReadback:false};
  const reply = result => ({result});
  const jsonReply = value => reply({content:JSON.stringify(value)});
  async function writeFile(method, {branch, path, sha, content, message}) {
    assert.match(branch, /^photo-issue-plan\/stock-/);
    assert.match(path, /^maintenance\/photo-issue-plans\/stock-[a-z0-9-]+\/generation-attempts\.json$/);
    const parent = refs.get(branch), files = trees.get(commits.get(parent).tree);
    if (method === "create_file") {
      if (Object.hasOwn(files, path)) throw Error("Contents path already exists");
    } else if (!Object.hasOwn(files, path) || sha !== blob(files[path])) {
      throw Error("Contents blob SHA no longer matches");
    }
    const next = {...files, [path]:content}, tree = digest(next), commit = digest([parent, tree, message]);
    trees.set(tree, next);
    commits.set(commit, {parent, tree});
    refs.set(branch, commit);
    if (method === "update_file" && state.afterRef) state.afterRef(branch, commit);
    return reply(method === "create_file" ? {commit_sha:commit} : {commit_sha:commit, content_sha:blob(content)});
  }
  const github = {
    async fetch({url}) {
      const prefix = "https://api.github.com/repos/" + repository;
      assert.ok(url.startsWith(prefix));
      const path = decodeURIComponent(url.slice(prefix.length));
      if (path === "") return jsonReply({full_name:repository, private:false, visibility:"public"});
      if (path.startsWith("/git/commits/")) {
        const sha = path.slice("/git/commits/".length);
        return jsonReply({sha, tree:{sha:commits.get(sha).tree}});
      }
      if (path.startsWith("/git/matching-refs/heads/")) {
        const search = path.slice("/git/matching-refs/heads/".length);
        return jsonReply([...refs].filter(([name]) => name.startsWith(search))
          .map(([name, sha]) => ({ref:"refs/heads/" + name, object:{type:"commit", sha}})));
      }
      if (path.startsWith("/git/ref/heads/")) {
        const name = path.slice("/git/ref/heads/".length);
        assert.ok(refs.has(name));
        return jsonReply({ref:"refs/heads/" + name, object:{type:"commit", sha:refs.get(name)}});
      }
      if (path.startsWith("/git/trees/")) {
        const sha = path.slice("/git/trees/".length).split("?")[0];
        assert.ok(trees.has(sha));
        const tree = Object.entries(trees.get(sha)).map(([path, content]) => ({
          path, sha:blob(content), type:"blob", mode:"100644", size:Buffer.byteLength(content)
        }));
        if (state.overCapTree === sha) tree.push({path:"assets/generated/collection.jpg",type:"blob",size:1536*1024*1024+1});
        return jsonReply({sha, truncated:false, tree});
      }
      if (path.startsWith("/branches/")) {
        const name = path.slice("/branches/".length), sha = refs.get(name);
        assert.ok(sha, "only read an existing ref");
        return jsonReply({name, commit:{sha, commit:{tree:{sha:commits.get(sha).tree}}}});
      }
      if (path.startsWith("/compare/")) {
        const [from, to] = path.slice("/compare/".length).split("?")[0].split("...");
        assert.equal(commits.get(to).parent, from);
        return jsonReply({base_commit:{sha:from}, merge_base_commit:{sha:from}, status:"ahead", behind_by:0});
      }
      if (path.startsWith("/commits?")) {
        const query = new URL(url).searchParams;
        assert.equal(query.get("sha"), main);
        assert.equal(query.get("path"), "docs/EDITORIAL_LOOP.md");
        assert.ok(Date.parse(query.get("until")) < Date.parse("2026-11-29T00:00:00+09:00"));
        return jsonReply([{sha:rollout, commit:{committer:{date:"2026-11-25T00:00:00Z"}}}]);
      }
      if (path === "/pulls/42") return jsonReply({number:42, state:"closed", merged_at:state.mergedAt,
        merge_commit_sha:rollout, base:{ref:"main", repo:{full_name:repository}}});
      if (path.split("?")[0] === "/commits/" + rollout + "/pulls")
        return jsonReply([{number:42, state:"closed", merged_at:state.mergedAt, merge_commit_sha:rollout,
          base:{ref:"main", repo:{full_name:repository}}}]);
      assert.fail("Unexpected connector URL " + path);
    },
    async fetch_file({ref, path, encoding}) {
      assert.equal(encoding, "utf-8");
      if (commits.has(ref) && ["scripts/photo_generation_quota.js","scripts/native_image_stage.js"].includes(path)) {
        return reply({encoding, content:readFileSync(join(__dirname,"..",path),"utf8")});
      }
      if (ref === "cast-source-drafts") {
        const model = /^drafts\/cast-voices\/([a-z-]+)\.json$/.exec(path)?.[1];
        assert.ok(Object.hasOwn(actors, model));
        return reply({encoding, content:JSON.stringify({model_id:model, source_chat_url:actors[model]})});
      }
      if (path === "docs/EDITORIAL_LOOP.md") {
        assert.ok(commits.has(ref) || ref === rollout);
        const enabled = ref === rollout ? state.earlierMarker : state.currentMarker;
        return reply({encoding, content:enabled ? "<!-- photo-stock-quota-exclusive-v1: 2026-11-29 JST -->" : "older protocol"});
      }
      const commit = refs.get(ref) ?? ref; // Current blob for Contents CAS, immutable read after the write.
      assert.ok(commits.has(commit));
      const content = trees.get(commits.get(commit).tree)[path];
      if (content === undefined) return {isError:true, result:{error_code:"NOT_FOUND", error_data:{message:"Not Found"}}};
      return reply({encoding, content:state.corruptReadback ? "{}" : content, sha:blob(content)});
    },
    async create_branch({branch_name, sha}) {
      assert.match(branch_name, /^photo-issue-plan\/stock-/);
      assert.ok(commits.has(sha));
      if (refs.has(branch_name)) throw Error("Reference already exists");
      refs.set(branch_name, sha);
      if (state.afterBranch) state.afterBranch(branch_name, sha);
      return reply({branch:branch_name});
    },
    create_file: args => writeFile("create_file", args),
    update_file: args => writeFile("update_file", args),
    create_tree: () => assert.fail("Quota writes use the contents API"),
    create_commit: () => assert.fail("Quota writes use the contents API"),
    create_blob: () => assert.fail("Quota changes only need a single tree entry")
  };
  for (const [name, operation] of Object.entries(github)) {
    github[name] = async args => {
      if (name !== "fetch") assert.equal(args.repository_full_name, repository);
      state.events.push({name, ...args});
      return operation(args);
    };
  }
  let reviewedRef; // Public run metadata survives a new cell; helpers and their caches do not.
  async function coldApi(calls) {
    const adapter = Object.fromEntries(Object.entries(github).map(([name, operation]) => [name, async args => {
      calls.push(name);
      return operation(args);
    }]));
    if (!reviewedRef) {
      const response = await adapter.fetch({url:"https://api.github.com/repos/"+repository+"/git/ref/heads/main"});
      const ref = JSON.parse(response.result.content);
      assert.equal(ref.ref, "refs/heads/main");
      reviewedRef = ref.object.sha;
    }
    async function load(path, name) {
      const file = await adapter.fetch_file({repository_full_name:repository,ref:reviewedRef,path,encoding:"utf-8"});
      return new Function(file.result.content+"\nreturn "+name+";")();
    }
    const quota = await load("scripts/photo_generation_quota.js","PhotoGenerationQuota");
    const stage = await load("scripts/native_image_stage.js","NativeImageStage");
    const guard = stage.create({github:adapter, repository, controlOnly:true,
      readCloudWorkflow:async ({nonce}) => {
        calls.push("read_image_control");
        state.events.push({name:"read_image_control"});
        return {version:1, repository, path:".github/workflows/free-cpu-scenes.yml",
          state:state.active ? "active" : "disabled_manually", checked_at:new Date().toISOString(), nonce};
      }}).cloudStatus;
    return quota.create({github:adapter, now:() => new Date(state.clock), cloudStatus:guard});
  }
  async function step(method, input, checkpoint) {
    const calls = [];
    try {
      return await (await coldApi(calls))[method](input, checkpoint && JSON.parse(JSON.stringify(checkpoint)));
    } finally {
      const mutations = calls.filter(name => /^(create_|update_)/.test(name)).length;
      state.cells.push({phase:checkpoint?.phase || "start", calls:calls.length, mutations});
      assert.ok(calls.length <= 16 && mutations <= 2, "cold cell " + (checkpoint?.phase || "start") +
        " used " + calls.length + " real callbacks and " + mutations + " mutations");
    }
  }
  async function drive(method, input, checkpoint) {
    for (let i = 0; i < 12; i++) {
      const result = await step(method, input, checkpoint);
      if (result.status !== "pending") return result;
      assert.equal(result.may_generate, false);
      assert.ok(result.checkpoint);
      checkpoint = result.checkpoint;
    }
    assert.fail("Weekly reservation did not terminate");
  }
  async function beforePhase(input, phase) {
    let checkpoint;
    for (let i = 0; i < 12; i++) {
      const result = await step("reserveStep", input, checkpoint);
      assert.equal(result.may_generate, false);
      assert.equal(result.status, "pending");
      checkpoint = result.checkpoint;
      if (checkpoint.phase === phase) return checkpoint;
    }
    assert.fail("Reservation did not reach " + phase);
  }
  const api = () => ({reserve:input => drive("reserveStep", input), finish:input => drive("finishStep", input)});
  const contents = sha => trees.get(commits.get(sha).tree);
  function advanceMain() {
    const parent = refs.get("main"), sha = digest([parent, "next main"]);
    const files = {...contents(parent), "maintenance/volume.txt":sha}, tree = digest(files);
    commits.set(sha, {parent, tree}); trees.set(tree, files);
    refs.set("main", sha);
    return tree;
  }
  return {api, drive, step, beforePhase, state, refs, contents, main, advanceMain};
}

const week = "2026-11-22";
const producer = model => ({model_id:model, source_chat_url:actors[model]});
const rio = producer("shioba-rio");
const runAt = "2026-11-27T18:00:00+09:00";
const request = (attempt, extra = {}) => ({...rio, run_id:"producer-run", attempt_id:attempt,
  run_started_at:runAt, ...extra});
const priorEvidence = "https://github.com/RollNuts/mihassei-news/commit/" + "1".repeat(40);
const reconciled = (count = 0, model = rio.model_id, date = week) => ({
  mode:"reconciled", week_start:date, model_id:model, coverage_ref:priorEvidence,
  prior_attempts:Array.from({length:count}, (_, i) => ({
    run_id:"earlier-run", attempt_id:"earlier-" + (i + 1), units:1, evidence_ref:priorEvidence
  }))
});
function currentWeek() {
  const env = fixture();
  env.state.clock = "2026-11-28T23:59:59+09:00";
  return env;
}
const grantCount = rows => rows.filter(row => row.status === "fulfilled" && row.value.may_generate === true).length;

test("keeps the 20-attempt charge for failures, interrupted and unconfirmed calls", async () => {
  const env = currentWeek(), api = env.api();
  const source = r => ({...rio, run_id:"producer-run", attempt_id:r, week_start:week,
    result_ref:priorEvidence});
  let result = await api.reserve(request("one", {baseline:reconciled(17)}));
  assert.equal(result.used, 18);
  assert.equal((await api.finish({...source("one"), status:"failed"})).may_generate, false);
  await api.reserve(request("two"));
  assert.equal((await api.finish({...source("two"), status:"interrupted"})).may_generate, false);
  result = await api.reserve(request("three"));
  assert.equal(result.used, 20);
  assert.equal(result.may_generate, true);
  const writeIndex = env.state.events.findLastIndex(e => e.name === "update_file");
  assert.ok(env.state.events.slice(writeIndex + 1).some(e =>
    e.name === "fetch_file" && e.ref === result.commit && e.path === result.path));
  assert.equal((await env.api().reserve(request("one"))).may_generate, false);
  assert.equal((await env.api().reserve(request("three"))).may_generate, false);
  await assert.rejects(api.reserve(request("over-limit", {baseline:reconciled(0)})));
  assert.equal(env.contents(env.refs.get(result.branch))[sibling], "original handoff");
  assert.equal(env.contents(env.main)[result.path], undefined);
});

test("JST Sunday, each producer, and the start of a run determine the applicable quota", async () => {
  const env = currentWeek(), api = env.api();
  const last = await api.reserve(request("saturday", {baseline:reconciled(19)}));
  assert.equal(last.week_start, week);
  const nextModel = producer("kiritani-sumire");
  const other = await api.reserve(request("saturday", {...nextModel, baseline:reconciled(0, nextModel.model_id)}));
  assert.notEqual(other.branch, last.branch);
  assert.equal(other.used, 1);
  env.state.clock = "2026-11-28T15:00:00.000Z"; // Precisely Sunday at midnight in JST.
  await assert.rejects(api.reserve(request("same-run-after-midnight")));
  const next = await api.reserve(request("sunday", {run_id:"new-sunday-run", run_started_at:env.state.clock,
    baseline:reconciled(0, rio.model_id, "2026-11-29")}));
  assert.equal(next.week_start, "2026-11-29");
  assert.notEqual(next.branch, last.branch);
  assert.equal(next.used, 1);
  assert.equal((await api.finish({...rio, run_id:"producer-run", attempt_id:"saturday",
    week_start:week, status:"failed", result_ref:priorEvidence})).may_generate, false);
});

test("unreconciled transition usage, mismatched producers, and a stopped workflow grant nothing", async () => {
  const env = currentWeek(), api = env.api();
  await assert.rejects(api.reserve(request("unknown")));
  for (const baseline of [reconciled(0, "kiritani-sumire"), reconciled(0, rio.model_id, "2026-11-15")])
    await assert.rejects(api.reserve(request("bad-baseline", {baseline})));
  await assert.rejects(api.reserve(request("wrong-chat", {source_chat_url:actors["kiritani-sumire"], baseline:reconciled()})));
  env.state.active = false;
  await assert.rejects(api.reserve(request("stopped", {baseline:reconciled()})));
  assert.equal(env.state.events.filter(e => e.name === "update_file").length, 0);
});

for (const existing of [false, true]) {
  test("concurrent claims allow one winner at slot 20 " + (existing ? "on update" : "on branch creation"), async () => {
    const env = currentWeek();
    let base;
    if (existing) await env.api().reserve(request("seeding", {baseline:reconciled(18)}));
    else base = {baseline:reconciled(19)};
    const raced = await Promise.allSettled(["alpha", "beta"].map(id => env.api().reserve(request(id, base))));
    assert.equal(grantCount(raced), 1);
    const winner = raced.find(row => row.status === "fulfilled" && row.value.may_generate).value;
    assert.equal(winner.used, 20);
    assert.equal(env.contents(env.refs.get(winner.branch))[sibling], "original handoff");
    await assert.rejects(env.api().reserve(request("extra")));
  });

  test("lost final Contents update consumes the attempt without replaying permission, " + (existing ? "existing" : "new") + " branch", async () => {
    const env = currentWeek();
    if (existing) await env.api().reserve(request("earlier", {baseline:reconciled(18)}));
    env.state.afterRef = () => { throw Error("lost confirmed-write response"); };
    const input = request("lost", existing ? {} : {baseline:reconciled(19)});
    const result = await Promise.allSettled([env.api().reserve(input)]);
    assert.equal(grantCount(result), 0);
    env.state.afterRef = null;
    const retry = await env.api().reserve(request("lost"));
    assert.equal(retry.status, "already_recorded");
    assert.equal(retry.may_generate, false);
    assert.equal(retry.used, 20);
    await assert.rejects(env.api().reserve(request("extra")));
  });
}

test("only a rollout proven on main before the JST week can start the ledger at zero", async () => {
  const input = request("auto", {run_started_at:"2026-11-29T00:00:00+09:00"});
  for (const changes of [{earlierMarker:false}, {currentMarker:false}, {mergedAt:"2026-11-28T15:00:00Z"}]) {
    const env = fixture();
    Object.assign(env.state, changes);
    await assert.rejects(env.api().reserve(input));
    assert.equal(env.state.events.filter(e => e.name === "update_file").length, 0);
  }
  const env = fixture(), result = await env.api().reserve(input);
  assert.equal(result.used, 1);
  assert.equal(result.week_start, "2026-11-29");
  assert.equal(result.may_generate, true);
});

test("an incorrect post-write immutable readback never grants permission", async () => {
  const env = currentWeek();
  env.state.afterRef = () => { env.state.corruptReadback = true; };
  assert.equal(grantCount(await Promise.allSettled([
    env.api().reserve(request("readback", {baseline:reconciled(19)}))
  ])), 0);
  env.state.corruptReadback = false;
  assert.equal((await env.api().reserve(request("readback"))).may_generate, false);
});

test("a second cold worker cannot obtain the same grant by racing identical input", async () => {
  const env = currentWeek(), input = request("same-attempt", {baseline:reconciled()});
  const results = await Promise.allSettled([env.api().reserve(input), env.api().reserve(input)]);
  assert.equal(grantCount(results), 1);
  assert.equal((await env.api().reserve(input)).may_generate, false);
});

test("a similarly named ref is preserved and cannot stand in for the person's exact ledger", async () => {
  const env = currentWeek(), branch = "photo-issue-plan/stock-shioba-rio-" + week;
  env.refs.set(branch + "-other", env.main);
  const reserved = await env.api().reserve(request("exact-ref", {baseline:reconciled()}));
  assert.equal(reserved.branch, branch);
  assert.equal(env.refs.get(branch + "-other"), env.main);
  assert.deepEqual(env.contents(env.refs.get(branch))[sibling], "original handoff");
});

test("same-run evidence already included in a reconciled baseline cannot grant another attempt", async () => {
  const env = currentWeek(), baseline = reconciled();
  baseline.prior_attempts.push({run_id:"producer-run", attempt_id:"prior", units:1, evidence_ref:priorEvidence});
  assert.equal(grantCount(await Promise.allSettled([env.api().reserve(request("prior", {baseline}))])), 0);
});

test("one reservation only authorizes one original and a workflow stop just before the grant wins", async () => {
  const env = currentWeek();
  await assert.rejects(env.api().reserve(request("multiple", {baseline:reconciled(19), units:2})));
  env.state.afterRef = () => { env.state.active = false; };
  const result = await Promise.allSettled([env.api().reserve(request("stopped-after-write", {baseline:reconciled(19)}))]);
  assert.equal(grantCount(result), 0);
  env.state.active = true;
  assert.equal((await env.api().reserve(request("stopped-after-write"))).may_generate, false);
});

test("a final checkpoint is single-use even when exact replays race and main moves during the grant", async () => {
  const env = currentWeek(), input = request("same-final", {baseline:reconciled(19)});
  const checkpoint = await env.beforePhase(input, "publish");
  env.state.afterRef = () => env.advanceMain();
  const results = await Promise.allSettled([
    env.step("reserveStep", input, checkpoint), env.step("reserveStep", input, checkpoint)
  ]);
  assert.equal(grantCount(results), 1);
  assert.equal((await env.step("reserveStep", input, checkpoint)).may_generate, false);
  const writeIndex = env.state.events.findIndex(e => e.name === "update_file");
  assert.ok(env.state.events.slice(writeIndex+1).some(e => e.url?.endsWith("/branches/main")));
  assert.ok(env.state.events.slice(writeIndex+1).some(e => e.url?.includes("/git/trees/")));
});

test("rollout initialization handles main advancing after the branch seed within cold callback limits", async () => {
  const env = fixture(), input = request("future", {run_started_at:env.state.clock});
  const checkpoint = await env.beforePhase(input, "initialize");
  env.advanceMain();
  assert.equal((await env.drive("reserveStep", input, checkpoint)).may_generate, true);
  assert.ok(env.state.events.some(e => e.url?.includes("/compare/" + env.main + "..." + env.refs.get("main"))));
});

test("the initial usage baseline cannot be reduced while resuming the same reservation", async () => {
  const env = currentWeek(), input = request("preserve-19", {baseline:reconciled(19)});
  const lookup = await env.step("reserveStep", input);
  assert.equal(lookup.may_generate, false);
  await assert.rejects(env.drive("reserveStep", {...input, baseline:reconciled(0)}, lookup.checkpoint));
  assert.equal(env.state.events.filter(e => e.name === "update_file").length, 0);
  const result = await env.drive("reserveStep", input, lookup.checkpoint);
  assert.equal(result.used, 20);
});

test("losing the branch-only seed reply gives no rights and a fresh lookup can safely complete", async () => {
  const env = currentWeek(), input = request("lost-seed", {baseline:reconciled(19)});
  env.state.afterBranch = () => { throw Error("lost seed response"); };
  await assert.rejects(env.api().reserve(input), /lost seed response/);
  assert.equal(env.state.events.filter(e => e.name === "update_file").length, 0);
  env.state.afterBranch = null;
  const result = await env.api().reserve(input);
  assert.equal(result.used, 20);
  assert.equal((await env.api().reserve(input)).may_generate, false);
});


test("both final operations recheck new main volume and live stop after CAS in a cold cell", async () => {
  for (const method of ["reserveStep","finishStep"]) {
    for (const afterWrite of ["active","stopped","over-cap"]) {
      const env = currentWeek(), input = request("final", {baseline:reconciled(19)});
      let finalInput, checkpoint;
      if (method === "reserveStep") {
        finalInput = input;
        checkpoint = await env.beforePhase(input, "publish");
      } else {
        const grant = await env.api().reserve(input);
        finalInput = {...rio, run_id:input.run_id, attempt_id:input.attempt_id, week_start:grant.week_start,
          status:"succeeded", result_ref:priorEvidence};
        checkpoint = (await env.step(method, finalInput)).checkpoint;
      }
      let newTree;
      env.state.afterRef = () => {
        newTree = env.advanceMain();
        if (afterWrite === "over-cap") env.state.overCapTree = newTree;
        if (afterWrite === "stopped") env.state.active = false;
      };
      const offset = env.state.events.length;
      const result = env.step(method, finalInput, checkpoint);
      if (afterWrite === "active") {
        assert.equal((await result).may_generate, method === "reserveStep");
      } else {
        await assert.rejects(result, /Cloud image operations stopped/);
      }
      const events = env.state.events.slice(offset);
      const write = events.findIndex(e => e.name === "update_file");
      assert.ok(write > 0);
      assert.ok(events.slice(write+1).some(e => e.url?.endsWith("/git/trees/"+newTree+"?recursive=1")));
      if (afterWrite !== "over-cap") assert.equal(events.at(-1).name,"read_image_control");
      assert.equal(env.state.cells.at(-1).calls, afterWrite === "over-cap" ? 14 : 15);
      env.state.active = true; env.state.overCapTree = null;
      const retry = await env.drive(method,finalInput);
      assert.equal(retry.status,"already_recorded");
      assert.equal(retry.may_generate,false);
    }
  }
});
