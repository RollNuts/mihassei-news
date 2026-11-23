/* Reserve a single new original on the registered producer's existing photo-plan
 * namespace. This helper only reserves through GitHub Contents; the producer owns the image call. */
var PhotoGenerationQuota = (() => {
  "use strict";
  const REPO = "RollNuts/mihassei-news";
  const ROOT = "https://api.github.com/repos/" + REPO;
  const POLICY = "docs/EDITORIAL_LOOP.md";
  const MARKER = "<!-- photo-stock-quota-exclusive-v1: 2026-11-29 JST -->";
  const FIRST_WEEK = "2026-11-29";
  const LIMIT = 20, DAY = 86400000, JST = 9 * 3600000;
  const sha = value => typeof value === "string" && /^[0-9a-f]{40}$/.test(value);
  const id = value => typeof value === "string" && /^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,239}$/.test(value);
  const chat = value => typeof value === "string" && /^https:\/\/chatgpt\.com\/c\/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value);
  const evidence = value => typeof value === "string" && value.length <= 600 && (
    /^https:\/\/chatgpt\.com\/c\/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?:#[a-zA-Z0-9_-]+)?$/.test(value) ||
    /^https:\/\/github\.com\/RollNuts\/mihassei-news\/(?:commit\/[a-f0-9]{40}|blob\/[a-f0-9]{40}\/[a-zA-Z0-9._/-]+|actions\/runs\/[1-9]\d*)(?:#[a-zA-Z0-9_-]+)?$/.test(value));
  const statuses = ["succeeded","failed","interrupted"];
  function check(ok, message) { if (!ok) throw Error(message); }
  function stamp(value) {
    check(typeof value === "string" && /^\d{4}-\d\d-\d\dT(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/.test(value)
      && Number.isFinite(Date.parse(value)) && new Date(value.slice(0,10)+"T00:00:00Z").toISOString().startsWith(value.slice(0,10)), "Invalid actual timestamp");
    return Date.parse(value);
  }
  function week(time) {
    const local = new Date(time + JST);
    return new Date(local.getTime() - local.getUTCDay()*DAY).toISOString().slice(0,10);
  }
  function start(day) {
    check(typeof day === "string" && /^\d{4}-\d{2}-\d{2}$/.test(day), "Expected a JST Sunday");
    const time = Date.parse(day+"T00:00:00+09:00");
    check(Number.isFinite(time) && week(time) === day, "Expected a JST Sunday");
    return time;
  }
  function context(input, day) {
    start(day);
    check(typeof input.model_id === "string" && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(input.model_id)
      && input.model_id.length <= 64 && chat(input.source_chat_url) && id(input.run_id) && id(input.attempt_id), "Invalid registered producer, run or attempt");
    const slug = "stock-" + input.model_id + "-" + day;
    return {day, branch:"photo-issue-plan/" + slug, path:"maintenance/photo-issue-plans/"+slug+"/generation-attempts.json"};
  }
  function validate(ledger, input, day) {
    check(ledger?.version === 1 && ledger.model_id === input.model_id && ledger.source_chat_url === input.source_chat_url
      && ledger.week_start === day && Array.isArray(ledger.attempts) && ledger.attempts.length <= LIMIT, "Mismatched weekly ledger");
    const base = ledger.baseline;
    check(base?.week_start === day && base.model_id === input.model_id && Array.isArray(base.prior_attempts)
      && base.prior_attempts.length <= LIMIT, "Unknown weekly baseline");
    if (base.mode === "exclusive") {
      check(day >= FIRST_WEEK && sha(base.policy_commit) && base.prior_attempts.length === 0 &&
        stamp(base.merged_at) < start(day), "Unproven exclusive baseline");
    } else {
      check(base.mode === "reconciled" && evidence(base.coverage_ref), "Complete prior usage evidence required");
    }
    let used = 0;
    const ids = new Set(), starts = new Map();
    for (const row of base.prior_attempts) {
      check(id(row?.run_id) && id(row.attempt_id) && !ids.has(row.attempt_id) && evidence(row.evidence_ref) &&
        Number.isInteger(row.units) && row.units >= 1, "Invalid prior attempt evidence");
      ids.add(row.attempt_id); used += row.units;
    }
    for (const row of ledger.attempts) {
      check(id(row?.run_id) && id(row.attempt_id) && !ids.has(row.attempt_id) && week(stamp(row.reserved_at)) === day &&
        week(stamp(row.run_started_at)) === day && stamp(row.run_started_at) <= stamp(row.reserved_at), "Invalid reserved attempt");
      check(!starts.has(row.run_id) || starts.get(row.run_id) === stamp(row.run_started_at), "Conflicting run start");
      starts.set(row.run_id,stamp(row.run_started_at));
      if (row.status === "reserved") check(row.result_ref === undefined && row.finished_at === undefined, "Invalid pending attempt");
      else check(statuses.includes(row.status) && evidence(row.result_ref) && stamp(row.finished_at) >= stamp(row.reserved_at), "Invalid attempt result");
      ids.add(row.attempt_id); used++;
    }
    check(used <= LIMIT, "Invalid quota total");
    return used;
  }
  function create({github, cloudStatus, now = () => new Date()}) {
    async function call(name,args) {
      const r = await github[name](args), p = r?.structuredContent ?? r?.result ?? r;
      check(p && !r.isError && !p.isError && !r.error && !p.error && !r.error_code && !p.error_code, "GitHub operation failed: "+name);
      return p;
    }
    const git = (name,args) => call(name,{repository_full_name:REPO,...args});
    async function get(path) {
      const r = await call("fetch",{url:ROOT+path});
      return typeof r.content === "string" ? JSON.parse(r.content) : r;
    }
    async function read(ref,path) {
      const r = await git("fetch_file",{ref,path,encoding:"utf-8"});
      check(r.encoding === "utf-8" && typeof r.content === "string" && r.content.length <= 262144, "Incomplete GitHub text: "+path);
      return r;
    }
    async function guard() {
      const s = await cloudStatus();
      check(s?.public === true && s.state === "active" && sha(s.main_commit_sha) && Number.isSafeInteger(s.jpeg_bytes)
        && s.jpeg_bytes >= 0 && s.jpeg_bytes <= 1536*1024*1024, "Cloud image operations stopped");
      return s.main_commit_sha;
    }
    function branchInfo(branch, info) {
      const head = info?.commit?.sha, tree = info?.commit?.commit?.tree?.sha;
      check(info?.name === branch && sha(head) && sha(tree), "Incomplete branch information");
      return {head, tree};
    }
    async function branchHead(branch) {
      const ref = await get("/git/ref/heads/"+branch);
      check(ref.ref === "refs/heads/"+branch && sha(ref.object?.sha), "Invalid branch head");
      return ref.object.sha;
    }
    async function registered(input) {
      const p = JSON.parse((await read("cast-source-drafts","drafts/cast-voices/"+input.model_id+".json")).content);
      check(p.model_id === input.model_id && p.source_chat_url === input.source_chat_url, "Wrong registered producer chat");
    }
    async function exactRef(branch) {
      const prefix = "refs/heads/"+branch, refs = await get("/git/matching-refs/heads/"+branch);
      check(Array.isArray(refs) && refs.every(r=>r.ref?.startsWith(prefix) && sha(r.object?.sha)
        && r.object.type === "commit"), "Incomplete candidate ref lookup");
      const exact = refs.filter(r=>r.ref === prefix);
      check(exact.length <= 1, "Ambiguous candidate ref");
      return exact[0]?.object.sha;
    }
    async function clearSeed(tree, path) {
      const files = await get("/git/trees/"+tree+"?recursive=1");
      check(files.sha === tree && files.truncated === false && Array.isArray(files.tree) &&
        files.tree.every(f=>typeof f.path === "string" && f.path.length && ["tree","blob","commit"].includes(f.type)
          && f.path !== path && (!path.startsWith(f.path+"/") || f.type === "tree")), "Occupied or incomplete seed tree");
    }
    const onMainBefore = (p,day) => Number.isSafeInteger(p?.number) && p.number>0 && p.base?.ref === "main"
      && p.base.repo?.full_name === REPO && sha(p.merge_commit_sha) && p.merged_at && stamp(p.merged_at)<start(day);
    async function discover(day) {
      const main = await branchHead("main"), until = new Date(start(day)-1).toISOString();
      const changes = await get("/commits?sha="+main+"&path="+encodeURIComponent(POLICY)+"&until="+encodeURIComponent(until)+"&per_page=5");
      check(Array.isArray(changes) && changes.length<=5, "Unavailable policy history");
      for (const c of changes) {
        check(sha(c.sha),"Invalid historical commit");
        const pulls = await get("/commits/"+c.sha+"/pulls");
        check(Array.isArray(pulls),"Unavailable policy merge history");
        const p = pulls.find(r=>onMainBefore(r,day));
        if (p && (await read(p.merge_commit_sha,POLICY)).content.includes(MARKER)) return p.number;
      }
      throw Error("No verified exclusive policy on main before the JST week: reconcile actual usage");
    }
    async function baselineFor(input,day,main,pr) {
      if (input.baseline !== undefined) return input.baseline;
      check(day>=FIRST_WEEK && Number.isSafeInteger(pr) && pr>0, "No verified baseline");
      const p = await get("/pulls/"+pr);
      check(p.number===pr && onMainBefore(p,day) && (await read(p.merge_commit_sha,POLICY)).content.includes(MARKER)
        && (await read(main,POLICY)).content.includes(MARKER), "Exclusive routing not proven on main before this week");
      return {mode:"exclusive",week_start:day,model_id:input.model_id,policy_commit:p.merge_commit_sha,merged_at:p.merged_at,prior_attempts:[]};
    }
    function initial(input,day,baseline) {
      const ledger = {version:1,model_id:input.model_id,source_chat_url:input.source_chat_url,week_start:day,baseline,attempts:[]};
      const used = validate(ledger,input,day);
      check(!baseline.prior_attempts.some(r=>r.attempt_id===input.attempt_id),"Attempt already counted in reconciled baseline");
      check(used<LIMIT,"Weekly generation quota exhausted");
      return ledger;
    }
    async function apply(input, checkpoint, finishing) {
      const time = now().getTime(), at = new Date(time).toISOString();
      const day = finishing ? input.week_start : week(time), loc = context(input,day);
      if (finishing) check(start(day)<=time && statuses.includes(input.status) && evidence(input.result_ref), "Provide an observed terminal result");
      else {
        check(input.units===undefined || input.units===1, "Reserve exactly one original");
        check(week(stamp(input.run_started_at))===day && stamp(input.run_started_at)<=time, "Run did not start in this JST week");
      }
      if (input.baseline !== undefined) check(input.baseline?.mode === "reconciled", "Unverified caller baseline");
      const baselineInput = input.baseline === undefined ? null : JSON.stringify(input.baseline);
      check(baselineInput === null || baselineInput.length <= 32768, "Oversized reconciliation evidence");
      const kind = finishing ? "finish" : "reserve";
      const identity = JSON.stringify([kind,day,input.model_id,input.source_chat_url,input.run_id,input.attempt_id,
        input.run_started_at,input.status,input.result_ref,baselineInput]);
      if (checkpoint) check(checkpoint.version===1 && checkpoint.identity===identity && typeof checkpoint.phase==="string", "Unrelated quota checkpoint");
      const phase = checkpoint?.phase ?? "lookup", pr = checkpoint?.pr;
      const pending = (next,proof=pr) => ({status:"pending",may_generate:false,phase:next,
        checkpoint:{version:1,identity,phase:next,...(proof===undefined?{}:{pr:proof})}});
      const result = (status,may_generate,commit,used) => ({status,may_generate,branch:loc.branch,path:loc.path,commit,week_start:day,used});
      const inWeek = () => check(finishing || week(now().getTime())===day, "Week changed during reservation");
      if (phase==="policy") {
        check(!finishing && day>=FIRST_WEEK && input.baseline===undefined,"Unexpected policy discovery");
        return pending("seed",await discover(day));
      }
      // Checkpoints carry only a phase and an optional public PR number. Every
      // write re-derives its branch/path and authority from live repository data.
      await registered(input);
      if (phase==="lookup") {
        const head = await exactRef(loc.branch);
        if (head) {
          try { await read(head,loc.path); return pending("publish"); }
          catch (missing) {
            const tip = branchInfo(loc.branch,await get("/branches/"+loc.branch));
            check(tip.head===head, "Candidate branch moved during lookup");
            await clearSeed(tip.tree,loc.path);
          }
        }
        check(!finishing,"No original reservation");
        if (input.baseline!==undefined) {
          initial(input,day,input.baseline);
          return pending("seed");
        }
        check(day>=FIRST_WEEK,"Unknown transition-week consumption: reconcile producer runs and parallel candidates");
        return pending("policy");
      }
      if (phase==="seed" || phase==="initialize") {
        check(!finishing,"Cannot initialize a result");
        const main = branchInfo("main",await get("/branches/main"));
        const ledger = initial(input,day,await baselineFor(input,day,main.head,pr));
        if (phase==="seed") {
          if (!await exactRef(loc.branch)) {
            await clearSeed(main.tree,loc.path);
            check(await guard()===main.head,"Main moved before initialization"); inWeek();
            const ack = await git("create_branch",{branch_name:loc.branch,sha:main.head});
            check(ack.branch===loc.branch && (!ack.sha || ack.sha===main.head) && await branchHead(loc.branch)===main.head,
              "Unconfirmed seed branch; restart the same attempt");
          }
          return pending("initialize");
        }
        const tip = branchInfo(loc.branch,await get("/branches/"+loc.branch));
        if (tip.head!==main.head) {
          const cmp = await get("/compare/"+tip.head+"..."+main.head+"?per_page=1");
          check(cmp.base_commit?.sha===tip.head && cmp.merge_base_commit?.sha===tip.head &&
            cmp.status==="ahead" && cmp.behind_by===0, "Ledger missing outside a verified main seed");
        }
        await clearSeed(tip.tree,loc.path);
        check(await guard()===main.head,"Main moved before initialization"); inWeek();
        const content = JSON.stringify(ledger,null,2)+"\n";
        const ack = await git("create_file",{branch:loc.branch,path:loc.path,content,message:"Initialize weekly photo generation quota "+day});
        check(sha(ack.commit_sha) && (await read(ack.commit_sha,loc.path)).content===content,"Unconfirmed ledger initialization; restart");
        return pending("publish");
      }
      check(phase==="publish","Unknown quota phase");
      const before = await read(loc.branch,loc.path);
      check(sha(before.sha),"Missing current quota blob SHA");
      const ledger = JSON.parse(before.content), used = validate(ledger,input,day);
      const row = ledger.attempts.find(r=>r.attempt_id===input.attempt_id);
      const prior = ledger.baseline.prior_attempts.find(r=>r.attempt_id===input.attempt_id);
      if (finishing) {
        check(row?.run_id===input.run_id,"No matching original reservation");
        if (row.status!=="reserved") {
          check(row.status===input.status && row.result_ref===input.result_ref,"Cannot replace terminal result");
          return result("already_recorded",false,await branchHead(loc.branch),used);
        }
        Object.assign(row,{status:input.status,result_ref:input.result_ref,finished_at:at});
      } else {
        if (prior || row) {
          check((prior || row).run_id===input.run_id,"Attempt belongs to another run");
          return result("already_recorded",false,await branchHead(loc.branch),used);
        }
        check(used<LIMIT,"Weekly generation quota exhausted");
        ledger.attempts.push({run_id:input.run_id,run_started_at:input.run_started_at,attempt_id:input.attempt_id,reserved_at:at,status:"reserved"});
      }
      const count = validate(ledger,input,day), content = JSON.stringify(ledger,null,2)+"\n";
      await guard(); inWeek();
      // A one-file compare-and-swap changes the old blob. Two final cells,
      // including concurrent replays of the very same checkpoint, cannot both
      // replace that blob. Never recover a lost write acknowledgement as a grant.
      const ack = await git("update_file",{branch:loc.branch,path:loc.path,sha:before.sha,content,message:"Record weekly photo attempt "+input.attempt_id});
      check(sha(ack.commit_sha) && sha(ack.content_sha) && ack.content_sha!==before.sha,"Unconfirmed quota write; recover without generating");
      const saved = await read(ack.commit_sha,loc.path);
      check(saved.sha===ack.content_sha && saved.content===content && await branchHead(loc.branch)===ack.commit_sha,
        "Quota readback changed; recover without generating");
      await guard(); inWeek();
      return result(finishing?input.status:"reserved",!finishing,ack.commit_sha,count);
    }
    return {reserveStep:(input,checkpoint)=>apply(input,checkpoint,false),finishStep:(input,checkpoint)=>apply(input,checkpoint,true)};
  }
  return {create};
})();
if (typeof module !== "undefined" && module.exports) module.exports = PhotoGenerationQuota;
