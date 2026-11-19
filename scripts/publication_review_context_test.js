"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const test = require("node:test");
const context = require("./publication_review_context.js");
const GIT = n => n.toString(16).padStart(40, "0");
const REF = GIT(1), BASE = GIT(2), REPO = "RollNuts/mihassei-news";
const ARTICLES = "content/articles.json", MARKET = "content/market_bulletins.json";
const clone = value => structuredClone(value);
function news(slug, extra = {}) {
  return { slug, title: "駅前で荷物を受け取る人", summary: "2031年、架空の駅で荷物を受け取った。",
    paragraphs: ["2031年、創作の駅には台車が来た。2031年、係員が箱を渡した。", "受け取った人は箱を持ち帰った。"],
    source: { topic: "SOURCE_SECRET", url: "https://secret.invalid" },
    reader_clarity: { rationale: "AUTHOR_RATIONALE_SECRET" }, ...extra };
}
function market(slug = "2026-11-19-noon") {
  return { slug, title: "日米市場で材料を見比べる", summary: "市場の値動きと材料を比較する。",
    lead: "LEAD_PUBLIC", day: "2026-11-19", published_at: "2026-11-19T12:40:00+09:00",
    slot: "noon", coverage: "investor",
    sections: [{ heading: "HEADING_PUBLIC", paragraphs: ["SECTION_PUBLIC"] }],
    figures: [{ id: "comparison", kind: "composition", title: "FIGURE_PUBLIC", note: "NOTE_PUBLIC",
      unit: "円", observed_at: "2026-11-19T12:00:00+09:00", source_url: "SOURCE_SECRET",
      values: [{ label: "VALUE_PUBLIC", value: 8, state: "実績" }, { label: "比較", value: 2, state: "予想" }],
      total: 10, total_label: "TOTAL_PUBLIC" }],
    conditions: [{ name: "CONDITION_PUBLIC", signal: "追い風", meaning: "MEANING_PUBLIC",
      observed_at: "2026-11-19T12:00:00+09:00", source_url: "SOURCE_SECRET" }],
    picks: [{ name: "PICK_PUBLIC", code: "1234", exchange: "東証", direction: "上昇",
      context: "CONTEXT_PUBLIC", adjustment_note: "ADJUSTMENT_PUBLIC",
      observed_at: "2026-11-19T12:00:00+09:00", previous_close: 10, close: 12,
      currency: "JPY", price_kind: "intraday", market_date: "2026-11-19", previous_market_date: "2026-11-18",
      session_close_at: "2026-11-19T15:30:00+09:00", session_source_url: "SOURCE_SECRET", source_url: "SOURCE_SECRET" }],
    sources: [{ title: "SOURCE_SECRET" }], editorial: { rationale: "AUTHOR_RATIONALE_SECRET" } };
}
function marker(head, extra = {}) {
  return "<!-- publication-finalize\n" + JSON.stringify({ version: 1, kind: "news",
    approved_head: head, authorized_by: "RollNuts", author_run: "chatgpt:author-12345",
    reviewer_run: "chatgpt:reviewer-12345", first_read: "passed", merge_after_ci: true,
    article_sha256: { "new-story": "a".repeat(64) }, ...extra }) + "\n-->";
}
function world() {
  const w = { prs: [], documents: new Map(), files: new Map(), checks: new Map(), runs: new Map(), jobs: new Map(), calls: [], fileCalls: [],
    hook: null, wrap: value => value };
  w.put = (ref, file, value) => w.documents.set(ref + ":" + file, clone(value));
  w.add = (number, file = ARTICLES, added = [news("new-story")]) => {
    const head = GIT(100 + number);
    const pr = { number, state: "open", draft: true, body: "PR_BODY_SECRET", title: "PR_TITLE_SECRET",
      head: { sha: head, repo: { full_name: REPO } }, base: { sha: REF, ref: "main", repo: { full_name: REPO } },
      changed_files: 1, mergeable: true, mergeable_state: "clean" };
    const before = file === ARTICLES ? [news("old-story")] : [];
    w.prs.push(pr);
    w.files.set(number, [{ filename: file, status: "modified" }]);
    w.put(BASE, file, before);
    w.put(REF, file, file === ARTICLES ? [...before, news("main-only-story")] : before);
    w.put(head, file, [...before, ...added]);
    w.checks.set(head, [{ id: number, name: "test", head_sha: head, status: "completed", conclusion: "success" }]);
    const run = { id: number * 1000, path: ".github/workflows/check.yml", event: "pull_request",
      head_sha: head, head_repository: { full_name: REPO }, run_attempt: 1,
      status: "completed", conclusion: "success",
      pull_requests: [{ number, head: { sha: head }, base: { ref: "main", sha: REF } }] };
    w.runs.set(head, [run]);
    w.jobs.set(run.id, [{ id: number * 1000 + 1, name: "test", run_id: run.id, run_attempt: 1,
      head_sha: head, status: "completed", conclusion: "success" }]);
    return pr;
  };
  w.fetchRepository = async args => {
    assert.deepEqual(Object.keys(args), ["url"]);
    const url = new URL(args.url);
    assert.equal(url.origin, "https://api.github.com");
    const route = url.pathname.replace("/repos/" + REPO, "");
    assert(url.pathname.startsWith("/repos/" + REPO + "/"));
    const page = Number(url.searchParams.get("page") || 1);
    w.calls.push({ route, page, search: url.search });
    if (w.hook) {
      const replacement = await w.hook({ route, page, url });
      if (replacement !== undefined) return replacement;
    }
    let value;
    if (route === "/git/ref/heads/main") value = { object: { sha: REF } };
    else if (route === "/pulls") {
      assert.equal(url.searchParams.get("state"), "open");
      assert.equal(url.searchParams.get("base"), "main");
      value = w.prs.slice((page - 1) * 100, page * 100);
    } else if (/^\/pulls\/\d+$/.test(route)) value = w.prs.find(pr => pr.number === Number(route.split("/")[2]));
    else if (/^\/pulls\/\d+\/files$/.test(route)) value = w.files.get(Number(route.split("/")[2])).slice((page - 1) * 100, page * 100);
    else if (route.startsWith("/compare/")) value = { merge_base_commit: { sha: BASE }, files: "not a complete file inventory" };
    else if (route === "/actions/runs") {
      const rows = w.runs.get(url.searchParams.get("head_sha")) || [];
      assert.equal(url.searchParams.get("event"), "pull_request");
      value = { total_count: rows.length, workflow_runs: rows.slice((page - 1) * 100, page * 100) };
    } else if (/^\/actions\/runs\/\d+$/.test(route)) {
      value = [...w.runs.values()].flat().find(run => run.id === Number(route.split("/")[3]));
    } else if (/^\/actions\/runs\/\d+\/jobs$/.test(route)) {
      assert.equal(url.searchParams.get("filter"), "latest");
      const rows = w.jobs.get(Number(route.split("/")[3]));
      value = { total_count: rows.length, jobs: rows.slice((page - 1) * 100, page * 100) };
    }
    else if (route.endsWith("/check-runs")) {
      const rows = w.checks.get(route.split("/")[2]);
      value = { total_count: rows.length, check_runs: rows.slice((page - 1) * 100, page * 100) };
    } else throw Error("Unexpected GET " + route);
    return w.wrap(clone(value));
  };
  w.fetchFile = async args => {
    assert.deepEqual(Object.keys(args).sort(), ["encoding", "path", "ref", "repository_full_name"]);
    assert.equal(args.repository_full_name, REPO); assert.equal(args.encoding, "utf-8");
    assert.match(args.ref, /^[0-9a-f]{40}$/);
    w.fileCalls.push(args);
    const doc = w.documents.get(args.ref + ":" + args.path);
    if (doc === undefined) throw Error("Missing fixed file");
    const content = JSON.stringify(doc);
    const sha = crypto.createHash("sha1").update("blob " + Buffer.byteLength(content) + "\0" + content).digest("hex");
    return { isError: false, structuredContent: { encoding: "utf-8", content, sha, path: args.path } };
  };
  w.read = (extra = {}, api = context) => api.read({ fetchFile: w.fetchFile,
    fetchRepository: w.fetchRepository, ref: REF, ...extra });
  return w;
}

test("plain V8 and CommonJS produce a blinded, fixed-head input without mutating source", async () => {
  const w = world(), pr = w.add(10), before = JSON.stringify([...w.documents]);
  const plain = new Function(fs.readFileSync(path.join(__dirname, "publication_review_context.js"), "utf8")
    + "\nreturn PublicationReviewContext;")();
  w.wrap = value => ({ content: [{ type: "text", text: "Action completed." }],
    structuredContent: { content: JSON.stringify(value) }, isError: false });
  const report = await w.read(), second = await w.read({}, plain);
  assert.deepEqual(report, second);
  assert.equal(JSON.stringify([...w.documents]), before);
  assert.equal(report.selected_count, 1);
  const selected = report.selected[0];
  assert.equal(selected.head, pr.head.sha); assert.equal(selected.merge_base, BASE);
  assert.match(selected.blob_sha, /^[0-9a-f]{40}$/);
  assert.equal(selected.reader.summary, "駅で荷物を受け取った。");
  assert.deepEqual(selected.reader.paragraphs, ["駅には台車が来た。係員が箱を渡した。", "受け取った人は箱を持ち帰った。"]);
  for (const secret of ["PR_BODY_SECRET", "PR_TITLE_SECRET", "SOURCE_SECRET", "AUTHOR_RATIONALE_SECRET"])
    assert.equal(JSON.stringify(report).includes(secret), false, secret);
  assert.equal(report.review_completed, false); assert.equal(report.publication_authorized, false);
  assert.equal(report.open_prs, "complete"); assert.equal(report.empty, false);
});

test("uses merge-base rather than current main and skips text already present on main", async () => {
  const w = world(), pr = w.add(10);
  const report = await w.read();
  assert.deepEqual(report.selected.map(row => row.slug), ["new-story"], "main-only addition is not a PR deletion");
  w.put(REF, ARTICLES, [...w.documents.get(REF + ":" + ARTICLES), news("new-story")]);
  const already = await w.read();
  assert.equal(already.selected_count, 0); assert.equal(already.empty, false);
  assert.deepEqual(already.already_on_main.map(row => row.slug), ["new-story"]);
  assert.equal(already.already_on_main[0].head, pr.head.sha);
});

test("limits articles, not PRs, and retains every deferred slug/head/blob without body", async () => {
  const w = world(), pr = w.add(10, ARTICLES, [1, 2, 3, 4, 5].map(n => news("story-" + n)));
  const report = await w.read();
  assert.equal(report.total, 5); assert.equal(report.selected_count, 3); assert.equal(report.pending_count, 2);
  assert.deepEqual(report.pending.map(row => row.slug), ["story-4", "story-5"]);
  assert(report.pending.every(row => row.head === pr.head.sha && row.blob_sha && !Object.hasOwn(row, "reader")));
  await assert.rejects(w.read({ limit: 4 }), /limit/);
});

test("market lead, sections, figures, conditions and picks are complete while sources stay hidden", async () => {
  const w = world(); w.add(10, MARKET, [market()]);
  const report = await w.read(), reader = report.selected[0].reader;
  for (const field of ["LEAD_PUBLIC", "HEADING_PUBLIC", "SECTION_PUBLIC", "FIGURE_PUBLIC", "NOTE_PUBLIC",
    "VALUE_PUBLIC", "TOTAL_PUBLIC", "CONDITION_PUBLIC", "MEANING_PUBLIC", "PICK_PUBLIC",
    "CONTEXT_PUBLIC", "ADJUSTMENT_PUBLIC"]) assert(JSON.stringify(reader).includes(field), field);
  assert.equal(reader.figures[0].total, 10);
  assert.equal(reader.picks[0].previous_market_date, "2026-11-18");
  assert.equal(reader.picks[0].session_close_at, "2026-11-19T15:30:00+09:00");
  assert.equal(JSON.stringify(report).includes("SECRET"), false);
  for (const edit of [
    row => row.lead += " changed",
    row => row.sections[0].paragraphs.push("changed"),
    row => row.figures[0].values[0].value++,
    row => row.conditions[0].meaning += " changed",
    row => row.picks[0].context += " changed",
  ]) {
    const next = world(), old = market(), changed = clone(old); edit(changed);
    const pr = next.add(10, MARKET, [changed]);
    next.put(BASE, MARKET, [old]); next.put(REF, MARKET, [old]); next.put(pr.head.sha, MARKET, [changed]);
    assert.equal((await next.read()).selected_count, 1);
  }
});

test("real main bulletin schema is accepted including legacy and investor stock examples", async () => {
  const reports = JSON.parse(fs.readFileSync(path.join(__dirname, "../content/market_bulletins.json"), "utf8"));
  const w = world(); w.add(10, MARKET, reports);
  const report = await w.read();
  assert.equal(report.total, reports.length);
  assert.equal(report.selected_count + report.pending_count, reports.length);
});

test("current ready handoff distinguishes active CI from an open PR awaiting finalizer verification", async () => {
  for (const status of ["completed", "in_progress"]) {
    const w = world(), pr = w.add(10, MARKET, [market()]);
    pr.draft = false; pr.body = marker(pr.head.sha, { kind: "market", article_sha256: {} });
    w.files.get(10).push({ filename: "content/market_outlooks.json", status: "modified" }); pr.changed_files++;
    w.runs.get(pr.head.sha)[0].status = status;
    w.runs.get(pr.head.sha)[0].conclusion = status === "completed" ? "success" : null;
    const report = await w.read();
    assert.equal(report.selected_count, 0);
    if (status === "in_progress") {
      assert.deepEqual(report.attention, []); assert.equal(report.machine_pending.length, 1);
      assert.equal(report.machine_pending[0].state, "ci_pending");
      assert.equal(report.machine_pending[0].verification, "self_reported_handoff_only");
      assert.equal(report.machine_pending[0].ci_run_id, 10000);
    } else {
      assert.equal(report.machine_pending.length, 0); assert.equal(report.attention.length, 1);
      assert.equal(report.attention[0].reason, "publication_unverified");
      assert.equal(report.attention[0].ci_run_id, 10000);
      assert.match(report.attention[0].next_operation, /If it is active, leave it running/);
      assert.match(report.attention[0].next_operation, /Do not repeat first read, create another PR, or rerun blindly/);
    }
    assert.equal(report.empty, false); assert.equal(report.publication_authorized, false);
  }
});

test("stale, duplicate, same-author and structurally invalid markers cannot hide first-read work", async () => {
  const cases = [
    head => marker(GIT(999)),
    head => marker(head) + marker(head),
    head => marker(head, { author_run: "chatgpt:same-12345", reviewer_run: "chatgpt:same-12345" }),
    head => marker(head, { article_sha256: null }),
    head => marker(head, { article_sha256: {} }),
    head => marker(head, { extra: true }),
    head => marker(head, { authorized_by: "not a login" }),
    head => marker(head, { authorized_by: 123 }),
    head => marker(head, { approved_head: [head] }),
    head => marker(head, { author_run: "chatgpt:author-12345\n" }),
    head => marker(head, { article_sha256: { "new-story": "a".repeat(64) + "\n" } }),
    head => "<!-- publication-finalize\n{}\n-->",
  ];
  for (const body of cases) {
    const w = world(), pr = w.add(10); pr.draft = false; pr.body = body(pr.head.sha);
    const report = await w.read();
    assert.equal(report.selected_count, 1); assert.equal(report.machine_pending.length, 0);
    assert(report.attention.some(row => /^handoff_(invalid|stale)$/.test(row.reason)));
  }
});

test("failed, absent, skipped or unknown workflow runs and merge conflicts remain explicit unresolved work", async () => {
  for (const state of ["failure", "cancelled", "missing", "skipped", "conflict", "unknown"]) {
    const w = world(), pr = w.add(10); pr.draft = false; pr.body = marker(pr.head.sha);
    if (state === "missing") w.runs.set(pr.head.sha, []);
    else if (state === "conflict") { pr.mergeable = false; pr.mergeable_state = "dirty"; }
    else if (state === "unknown") { pr.mergeable = null; pr.mergeable_state = "unknown"; }
    else w.runs.get(pr.head.sha)[0].conclusion = state;
    const report = await w.read();
    assert.equal(report.machine_pending.length, 0); assert(report.attention.length);
    assert.equal(report.empty, false);
  }
});

test("unhandled market and article-image work needs attention, unrelated code/photo/story work does not", async () => {
  for (const [file, expected] of [
    ["content/market_outlooks.json", "market_inputs_require_existing_review"],
    ["assets/generated/example.jpg", "image_only_requires_existing_review"],
    ["scripts/build.py", null], ["content/photo_issues.json", null], ["content/photo_stories/nagino-saya.json", null],
  ]) {
    const w = world(), pr = w.add(10); w.files.set(10, [{ filename: file, status: "modified" }]);
    const report = await w.read();
    assert.equal(report.selected_count, 0); assert.equal(report.empty, false);
    if (expected) assert.equal(report.attention[0].reason, expected);
    else { assert.deepEqual(report.attention, []); assert.equal(report.outside_scope[0].pr, pr.number); }
  }
});

test("ready current-head image-only and outlook-only handoffs retain finalizer attention without duplicate first read", async () => {
  for (const file of ["assets/generated/example.jpg", "content/market_outlooks.json"]) {
    const w = world(), pr = w.add(10);
    w.files.set(10, [{ filename: file, status: "modified" }]);
    pr.draft = false; pr.body = marker(pr.head.sha);
    const report = await w.read();
    assert.equal(report.selected_count, 0); assert.equal(report.attention[0].reason, "publication_unverified");
    assert.equal(report.machine_pending.length, 0);
    assert.equal(w.fileCalls.length, 0, "no duplicate approved-image or article retrieval");
  }
});

test("a successful check named test cannot replace the exact required CI workflow", async () => {
  const w = world(), pr = w.add(10);
  pr.draft = false; pr.body = marker(pr.head.sha);
  w.runs.get(pr.head.sha)[0].path = ".github/workflows/unrelated.yml";
  const report = await w.read();
  assert.equal(w.checks.get(pr.head.sha)[0].conclusion, "success");
  assert.equal(report.machine_pending.length, 0);
  assert.equal(report.attention[0].reason, "ci_unverified");
  assert.equal(report.selected_count, 0, "an existing claimed review needs gate recovery, not another first read");
  assert.equal(w.calls.some(call => call.route.endsWith("/check-runs")), false);
});

test("workflow, repository, PR/head/base and job attempt must all identify the same current CI", async () => {
  for (const fault of ["workflow", "event", "head", "repository", "pr", "pr_head", "base", "attempt",
    "job_run", "job_head", "job_attempt", "job_status", "job_skipped", "duplicate_test"]) {
    const w = world(), pr = w.add(10);
    pr.draft = false; pr.body = marker(pr.head.sha);
    const original = clone(w.runs.get(pr.head.sha)[0]), detail = clone(original);
    if (fault === "workflow") detail.path = ".github/workflows/other.yml";
    if (fault === "event") detail.event = "push";
    if (fault === "head") detail.head_sha = GIT(999);
    if (fault === "repository") detail.head_repository.full_name = "someone/else";
    if (fault === "pr") detail.pull_requests[0].number = 999;
    if (fault === "pr_head") detail.pull_requests[0].head.sha = GIT(999);
    if (fault === "base") detail.pull_requests[0].base.ref = "other";
    if (fault === "attempt") detail.run_attempt = 0;
    const job = w.jobs.get(original.id)[0];
    if (fault === "job_run") job.run_id++;
    if (fault === "job_head") job.head_sha = GIT(999);
    if (fault === "job_attempt") job.run_attempt++;
    if (fault === "job_status") job.status = "in_progress";
    if (fault === "job_skipped") job.conclusion = "skipped";
    if (fault === "duplicate_test") w.jobs.get(original.id).push({ ...job, id: job.id + 1 });
    w.hook = ({ route }) => route === "/actions/runs/" + original.id ? detail : undefined;
    const report = await w.read();
    assert.equal(report.attention[0].reason, "ci_unverified", fault);
    assert.equal(report.machine_pending.length, 0, fault);
  }
});

test("latest current-head CI supersedes an older success and cancelled work remains visible", async () => {
  const w = world(), pr = w.add(10);
  pr.draft = false; pr.body = marker(pr.head.sha);
  const latest = { ...clone(w.runs.get(pr.head.sha)[0]), id: 10002, status: "queued", conclusion: null };
  w.runs.get(pr.head.sha).push(latest);
  let report = await w.read();
  assert.equal(report.machine_pending[0].ci_run_id, 10002);
  assert.equal(report.machine_pending[0].state, "ci_pending");
  latest.status = "completed"; latest.conclusion = "cancelled";
  report = await w.read();
  assert.equal(report.machine_pending.length, 0);
  assert.equal(report.attention[0].reason, "ci_failed");
  assert.equal(report.attention[0].ci_run_id, 10002);
});

test("empty is only reported after successful complete inventories at the fixed main", async () => {
  const w = world(), report = await w.read();
  assert.equal(report.empty, true); assert.equal(report.open_pr_count, 0);
  assert.equal(w.calls.filter(c => c.route === "/pulls").length, 2);
  assert.equal(w.fileCalls.length, 0);
});

test("failed or truncated second PR page and a listing at the page bound fail instead of returning zero", async () => {
  for (const failure of ["throw", "truncated", "cap"]) {
    const w = world(), template = w.add(1);
    w.hook = ({ route, page }) => {
      if (route !== "/pulls") return;
      if (page === 2 && failure === "throw") throw Error("network disconnected");
      if (page === 2 && failure === "truncated") return { truncated: true, structuredContent: [] };
      return Array.from({ length: 100 }, (_, i) => ({ ...clone(template), number: (page - 1) * 100 + i + 1 }));
    };
    await assert.rejects(w.read(), /failed|incomplete|page limit/i);
    assert.equal(w.fileCalls.length, 0);
  }
});

test("a second PR file page failure and mismatched detail count are not empty diffs", async () => {
  const w = world(), pr = w.add(10);
  w.files.set(10, Array.from({ length: 101 }, (_, n) => ({ filename: "docs/file-" + n + ".md", status: "modified" })));
  pr.changed_files = 101;
  w.hook = ({ route, page }) => { if (route === "/pulls/10/files" && page === 2) throw Error("unavailable"); };
  await assert.rejects(w.read(), /GET failed/);
  const count = world(); count.add(10).changed_files = 2;
  await assert.rejects(count.read(), /Incomplete PR/);
});

test("unavailable file, duplicate records, unknown schema, body deletion and renames stop explicitly", async () => {
  for (const fault of ["missing", "schema", "duplicate", "deletion", "rename", "body"]) {
    const w = world(), pr = w.add(10), key = pr.head.sha + ":" + ARTICLES;
    if (fault === "missing") w.documents.delete(key);
    if (fault === "schema") w.documents.set(key, { articles: [] });
    if (fault === "duplicate") w.documents.set(key, [news("same"), news("same")]);
    if (fault === "deletion") w.documents.set(key, [news("new-story")]);
    if (fault === "rename") w.files.set(10, [{ filename: ARTICLES, status: "renamed", previous_filename: "content/old.json" }]);
    if (fault === "body") w.documents.set(key, [news("old-story"), news("new-story", { paragraphs: null })]);
    await assert.rejects(w.read(), /failed|schema|duplicate|deletion|renamed|paragraphs/i);
  }
});

test("provider error wrappers, incomplete run job inventory and changed head/main cannot become normal results", async () => {
  for (const fault of ["provider", "jobs", "head", "main"]) {
    const w = world(), pr = w.add(10);
    if (fault === "jobs") { pr.draft = false; pr.body = marker(pr.head.sha); }
    let mainReads = 0, inventories = 0;
    w.hook = ({ route }) => {
      if (fault === "provider" && route === "/pulls") return { isError: true, structuredContent: [] };
      if (fault === "jobs" && route.endsWith("/jobs")) return { total_count: 2, jobs: w.jobs.get(10000) };
      if (fault === "head" && route === "/pulls" && ++inventories === 2)
        return [{ ...clone(pr), head: { ...pr.head, sha: GIT(999) } }];
      if (fault === "main" && route === "/git/ref/heads/main" && ++mainReads === 2)
        return { object: { sha: GIT(999) } };
    };
    await assert.rejects(w.read(), /Failed|Incomplete|changed/);
  }
});
