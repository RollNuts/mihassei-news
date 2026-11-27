/* Read-only, blinded input for the existing publication reviewer.
 * Load with new Function(source + '\nreturn PublicationReviewContext;')().
 * This is a bounded GitHub snapshot, never a first-read or publication approval.
 */
var PublicationReviewContext = (function () {
  "use strict";
  const REPOSITORY = "RollNuts/mihassei-news";
  const API = "https://api.github.com/repos/" + REPOSITORY;
  const GIT = /^[0-9a-f]{40}$/, SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  const HASH = /^[0-9a-f]{64}$/, LOGIN = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;
  const RUN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{7,199}$/;
  const INPUTS = ["content/articles.json", "content/market_bulletins.json"];
  const EDITORIAL_HANDOFF_FILES = new Set(["docs/DAILY_EDITORIAL_BRIEF.md", "docs/EDITORIAL_IDEAS.md",
    "content/broadcasts.json"]);
  const OTHER_MARKET = new Set(["content/market_outlook.json", "content/market_outlooks.json",
    "content/market_figures.json", "content/serials.json"]);
  const PHOTO_INPUT = /^(?:content\/photo_(?:issues|editions|stories)(?:\.json|\/)|maintenance\/photo-)/;
  const ARTICLE_IMAGE = /^(?:assets\/(?:generated|editorial-scenes|article-diagrams)\/|maintenance\/(?:native-image-candidates|editorial-scenes|article-diagrams)\/|content\/(?:visual_reviews|approved_photos)\.json$)/;
  const OPEN = "/pulls?state=open&base=main&sort=created&direction=asc";
  const CI_WORKFLOW = ".github/workflows/check.yml";
  const ACTIVE_RUN = new Set(["queued", "in_progress", "waiting", "pending", "requested"]);
  const MAX_PAGES = 10, PAGE_SIZE = 100;

  function object(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
  function matches(pattern, value) {
    return typeof value === "string" && !/[\r\n]/.test(value) && pattern.test(value);
  }
  function calendarDay(day) {
    if (!matches(/^\d{4}-\d\d-\d\d$/, day) || day.startsWith("0000-")) return false;
    const time = Date.parse(day + "T00:00:00Z");
    return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === day;
  }
  function require(value, message) { if (!value) throw Error(message + "; review scope is unverified"); }
  function text(value, label) {
    require(typeof value === "string" && value.trim(), "Invalid " + label);
    return value;
  }
  function strings(values, label) {
    require(Array.isArray(values) && values.length, "Invalid " + label);
    return values.map(value => text(value, label));
  }
  function amount(value, label) {
    require(typeof value === "number" && Number.isFinite(value), "Invalid " + label);
    return value;
  }
  function copy(row, keys, label) {
    require(object(row), "Invalid " + label);
    return Object.fromEntries(keys.map(key => [key, text(row[key], label + "." + key)]));
  }
  function readerCopy(value) {
    // Keep this small presentation rule aligned with editorial_copy.reader_copy.
    return value.replace(/架空の|創作の/g, "").replace(/(^|[。！？」])\s*2031年、/g, "$1");
  }
  function news(row) {
    return { title: text(row.title, "article title"),
      summary: readerCopy(text(row.summary, "article summary")),
      paragraphs: strings(row.paragraphs, "article paragraphs").map(readerCopy) };
  }
  function market(row) {
    require(row.coverage === undefined || row.coverage === "investor", "Unknown market coverage");
    require(["am", "open", "noon", "close"].includes(row.slot), "Unknown market slot");
    const result = copy(row, ["title", "summary", "lead", "day", "published_at", "slot"], "market bulletin");
    if (row.coverage !== undefined) result.coverage = row.coverage;
    require(Array.isArray(row.sections) && row.sections.length, "Invalid market sections");
    result.sections = row.sections.map(section => ({ ...copy(section, ["heading"], "market section"),
      paragraphs: strings(section.paragraphs, "market section paragraphs") }));
    require(Array.isArray(row.figures) && row.figures.length, "Invalid market figures");
    result.figures = row.figures.map(figure => {
      require(object(figure) && ["bars", "composition"].includes(figure.kind), "Unknown market figure");
      const value = copy(figure, ["id", "kind", "title", "note", "unit", "observed_at"], "market figure");
      require(Array.isArray(figure.values) && figure.values.length, "Invalid market figure values");
      value.values = figure.values.map(point => ({ ...copy(point, ["label", "state"], "market figure value"),
        value: amount(point.value, "market figure value") }));
      if (figure.kind === "composition") {
        value.total = amount(figure.total, "market figure total");
        value.total_label = text(figure.total_label, "market figure total label");
      }
      return value;
    });
    for (const key of ["conditions", "picks"])
      require(row[key] === undefined || Array.isArray(row[key]), "Invalid market " + key);
    result.conditions = (row.conditions || []).map(item =>
      copy(item, ["name", "signal", "meaning", "observed_at"], "market condition"));
    result.picks = (row.picks || []).map(pick => {
      const value = copy(pick, ["name", "code", "exchange", "direction", "context",
        "adjustment_note", "observed_at"], "market pick");
      value.previous_close = amount(pick.previous_close, "market pick previous close");
      value.close = amount(pick.close, "market pick close");
      if (row.coverage === "investor") {
        Object.assign(value, copy(pick, ["currency", "price_kind", "market_date",
          "previous_market_date"], "market pick"));
        if (pick.session_close_at !== undefined)
          value.session_close_at = text(pick.session_close_at, "market pick session close");
      }
      return value;
    });
    return result;
  }
  function records(document, path) {
    require(Array.isArray(document), "Unknown document schema at " + path);
    const rows = new Map();
    for (const row of document) {
      require(object(row) && matches(SLUG, row.slug) && !rows.has(row.slug), "Invalid/duplicate slug at " + path);
      rows.set(row.slug, path === INPUTS[0] ? news(row) : market(row));
    }
    return rows;
  }
  function unwrap(reply, label, file) {
    let value = reply;
    for (let depth = 0; depth < 7; depth++) {
      require(!object(value) || !(value.isError || value.error || value.error_code
        || value.status === "ERROR" || value.truncated || value.content_truncated
        || value.incomplete_results), "Failed/incomplete GitHub response for " + label);
      if (object(value) && value.structuredContent !== undefined) { value = value.structuredContent; continue; }
      if (object(value) && value.result !== undefined) { value = value.result; continue; }
      if (file && object(value) && value.encoding === "utf-8"
          && typeof value.content === "string" && matches(GIT, value.sha)) {
        require(value.path === undefined || value.path === label, "Wrong GitHub file path");
        return value;
      }
      if (!file && object(value) && typeof value.content === "string") {
        try { value = JSON.parse(value.content); } catch (_) { throw Error("Invalid GitHub JSON for " + label); }
        continue;
      }
      if (!file && (object(value) || Array.isArray(value))) return value;
      throw Error("Unrecognized GitHub response for " + label + "; review scope is unverified");
    }
    throw Error("Nested GitHub response limit reached for " + label);
  }
  function identity(pr) {
    require(object(pr) && Number.isSafeInteger(pr.number) && pr.number > 0
      && pr.state === "open" && typeof pr.draft === "boolean"
      && matches(GIT, pr.head?.sha) && matches(GIT, pr.base?.sha)
      && pr.base?.ref === "main" && pr.base?.repo?.full_name === REPOSITORY
      && (pr.body === null || typeof pr.body === "string"), "Invalid open main PR");
    return [pr.number, pr.head.sha, pr.draft, pr.head?.repo?.full_name, pr.body];
  }
  function handoff(pr, kinds = ["news", "market"]) {
    const body = pr.body || "", blocks = [...body.matchAll(/<!-- publication-finalize\s*\n([\s\S]*?)\n-->/g)];
    if (!body.includes("<!-- publication-finalize")) return "absent";
    if (blocks.length !== 1) return "invalid";
    let claim;
    try { claim = JSON.parse(blocks[0][1]); } catch (_) { return "invalid"; }
    const required = ["version", "kind", "approved_head", "authorized_by", "author_run",
      "reviewer_run", "first_read", "merge_after_ci"];
    // These are claimed states only. Existing publication/owner guards still
    // validate identities, fingerprints, trust, allowed files, CI and merge rights.
    // Match approval()'s marker syntax so a claim that it rejects cannot hide work.
    if (!object(claim) || !required.every(key => Object.hasOwn(claim, key))
        || !Object.keys(claim).every(key => required.includes(key) || key === "article_sha256")
        || claim.version !== 1 || !kinds.includes(claim.kind)
        || claim.first_read !== "passed" || claim.merge_after_ci !== true
        || !matches(GIT, claim.approved_head)
        || !matches(LOGIN, claim.authorized_by)
        || !["author_run", "reviewer_run"].every(key => matches(RUN, claim[key])))
      return "invalid";
    if (claim.kind !== "cast_story" && (claim.author_run === claim.reviewer_run || !object(claim.article_sha256)
        || claim.kind === "news" && !Object.keys(claim.article_sha256).length
        || !Object.entries(claim.article_sha256).every(([slug, hash]) =>
          matches(SLUG, slug) && matches(HASH, hash))))
      return "invalid";
    return claim.approved_head === pr.head.sha ? "current_head_claim" : "stale";
  }
  async function read({ fetchFile, fetchRepository, ref, limit = 3, photoReport } = {}) {
    require(typeof fetchFile === "function" && typeof fetchRepository === "function"
      && matches(GIT, ref) && Number.isInteger(limit) && limit >= 1 && limit <= 3,
    "Provide GitHub readers, a full main SHA and a limit from 1 to 3");
    let reads = 0;
    async function get(path) {
      reads++;
      let reply;
      try { reply = await fetchRepository({ url: API + path }); }
      catch (_) { throw Error("GitHub GET failed: " + path + "; review scope is unverified"); }
      return unwrap(reply, path, false);
    }
    async function pages(path, field) {
      const all = [], ids = new Set();
      let total;
      for (let page = 1; page <= MAX_PAGES; page++) {
        const payload = await get(path + (path.includes("?") ? "&" : "?")
          + "per_page=" + PAGE_SIZE + "&page=" + page);
        const rows = field ? payload[field] : payload;
        require(Array.isArray(rows) && rows.length <= PAGE_SIZE, "Incomplete GitHub listing at " + path);
        if (field) {
          require(Number.isSafeInteger(payload.total_count) && payload.total_count >= 0,
            "Unknown GitHub listing total at " + path);
          if (total === undefined) total = payload.total_count;
          require(total === payload.total_count, "GitHub listing changed at " + path);
        }
        for (const row of rows) {
          require(object(row), "Invalid GitHub listing row at " + path);
          const id = row.number ?? row.id ?? row.filename;
          require(id !== undefined && !ids.has(id), "Repeated/incomplete GitHub page at " + path);
          ids.add(id); all.push(row);
        }
        if (rows.length < PAGE_SIZE) {
          require(!field || all.length === total, "Incomplete GitHub listing at " + path);
          return all;
        }
      }
      throw Error("GitHub page limit reached: " + path + "; review scope is unverified");
    }
    const files = new Map();
    async function document(path, revision) {
      const key = revision + ":" + path;
      if (!files.has(key)) files.set(key, (async () => {
        let reply;
        reads++;
        try { reply = await fetchFile({ repository_full_name: REPOSITORY, path, ref: revision, encoding: "utf-8" }); }
        catch (_) { throw Error("GitHub file read failed: " + key + "; review scope is unverified"); }
        const file = unwrap(reply, path, true);
        let data;
        try { data = JSON.parse(file.content); } catch (_) { throw Error("Invalid JSON at " + key); }
        return { sha: file.sha, rows: records(data, path) };
      })());
      return files.get(key);
    }
    async function main() {
      const value = await get("/git/ref/heads/main");
      require(value.object?.sha === ref, "Main changed; refresh the fixed review ref");
    }
    async function inventory() {
      const prs = await pages(OPEN);
      prs.forEach(identity);
      return prs.sort((a, b) => a.number - b.number);
    }
    async function ciState(pr) {
      if (pr.mergeable === false || ["dirty", "blocked"].includes(pr.mergeable_state)) return { state: "merge_blocked" };
      if (pr.mergeable !== true || !["clean", "unstable"].includes(pr.mergeable_state)) return { state: "mergeability_unknown" };
      // A check called "test" does not identify the required workflow or this PR.
      // Use the repository runs route supported by the existing GitHub reader.
      const runs = await pages("/actions/runs?event=pull_request&head_sha=" + pr.head.sha, "workflow_runs");
      const candidates = runs.filter(run => run.path === CI_WORKFLOW
        && run.event === "pull_request" && run.head_sha === pr.head.sha);
      if (!candidates.length) return { state: "ci_unverified" };
      require(candidates.every(run => Number.isSafeInteger(run.id) && run.id > 0), "Invalid CI run identity");
      const latest = candidates.reduce((a, b) => a.id > b.id ? a : b);
      const run = await get("/actions/runs/" + latest.id);
      const binding = { ci_run_id: latest.id };
      if (run.id !== latest.id || run.path !== CI_WORKFLOW || run.event !== "pull_request"
          || run.head_sha !== pr.head.sha || run.head_repository?.full_name !== REPOSITORY
          || !Number.isSafeInteger(run.run_attempt) || run.run_attempt < 1
          || !Array.isArray(run.pull_requests)
          || run.pull_requests.filter(p => p.number === pr.number && p.head?.sha === pr.head.sha
            && p.base?.ref === "main" && matches(GIT, p.base?.sha)).length !== 1)
        return { ...binding, state: "ci_unverified" };
      binding.ci_run_attempt = run.run_attempt;
      if (ACTIVE_RUN.has(run.status)) return { ...binding, state: "ci_pending" };
      if (run.status !== "completed") return { ...binding, state: "ci_unverified" };
      if (run.conclusion !== "success") return { ...binding, state: "ci_failed" };
      const jobs = await pages("/actions/runs/" + run.id + "/jobs?filter=latest", "jobs");
      const tests = jobs.filter(job => job.name === "test");
      if (tests.length !== 1 || tests[0].run_id !== run.id || tests[0].head_sha !== pr.head.sha
          || tests[0].run_attempt !== run.run_attempt || tests[0].status !== "completed"
          || tests[0].conclusion !== "success") return { ...binding, state: "ci_unverified" };
      return { ...binding, state: "ci_success" };
    }

    await main();
    const prs = await inventory(), candidates = [], attention = [], machine = [], outside = [], current = [];
    const observed = JSON.stringify(prs.map(identity));
    for (const listed of prs) {
      const pr = await get("/pulls/" + listed.number);
      require(JSON.stringify(identity(pr)) === JSON.stringify(identity(listed)), "PR changed during input collection");
      const binding = { pr: pr.number, head: pr.head.sha };
      if (pr.head?.repo?.full_name !== REPOSITORY) {
        attention.push({ ...binding, reason: "foreign_head_requires_existing_review" });
        continue;
      }
      const changed = await pages("/pulls/" + pr.number + "/files");
      require(Number.isSafeInteger(pr.changed_files) && changed.length === pr.changed_files
        && changed.every(f => typeof f.filename === "string" && typeof f.status === "string"),
      "Incomplete PR changed-file inventory");
      const paths = changed.map(f => f.filename);
      const relevant = changed.filter(f => INPUTS.includes(f.filename) || INPUTS.includes(f.previous_filename));
      const unsupported = paths.filter(path => OTHER_MARKET.has(path));
      const photo = paths.some(path => PHOTO_INPUT.test(path));
      const images = paths.filter(path => ARTICLE_IMAGE.test(path) && !path.includes("/photo-issue-"));
      require(relevant.every(f => INPUTS.includes(f.filename) && f.status === "modified"
        && !f.previous_filename), "Removed/renamed/unknown publication input");
      const targets = [];
      let base;
      if (relevant.length) {
        const comparison = await get("/compare/" + ref + "..." + pr.head.sha);
        base = comparison.merge_base_commit?.sha;
        require(matches(GIT, base), "PR merge base is unavailable");
      }
      for (const path of relevant.map(f => f.filename)) {
        const [before, after, live] = await Promise.all([document(path, base), document(path, pr.head.sha), document(path, ref)]);
        for (const slug of before.rows.keys())
          require(after.rows.has(slug), "Reader text deletion in PR " + pr.number + ": " + path + "/" + slug);
        for (const [slug, reader] of after.rows) {
          if (JSON.stringify(reader) === JSON.stringify(before.rows.get(slug))) continue;
          const target = { ...binding, slug, path, merge_base: base, blob_sha: after.sha,
            baseline_blob_sha: before.sha, main_blob_sha: live.sha };
          if (JSON.stringify(reader) === JSON.stringify(live.rows.get(slug)))
            current.push({ ...target, reason: "same_reader_text_already_on_main" });
          else targets.push({ ...target, kind: path === INPUTS[0] ? "news" : "market_bulletin", reader });
        }
      }
      const state = handoff(pr), references = targets.map(({ reader, ...target }) => target);
      // The existing morning desk and broadcast PRs use prose handoffs. Surface the
      // section's presence only; its current owner and conditions need a separate read.
      if (!targets.length && state === "absent" && paths.some(path => EDITORIAL_HANDOFF_FILES.has(path))
          && changed.every(f => f.status === "modified" && !f.previous_filename
            && (EDITORIAL_HANDOFF_FILES.has(f.filename) || f.filename === INPUTS[0]))
          && /^##[ \t]+(?:安全な)?通常統合(?:の|／)既存引き?継ぎ[ \t]*\r?$/m.test(pr.body || "")) {
        attention.push({ ...binding, draft: pr.draft, reason: "ordinary_editorial_handoff",
          next_operation: "After first read, check the latest handoff, owner/run, changed files against current main, "
            + "then call readValidationStatus for this PR/head. Once ownership, required CI and current main "
            + "have been checked, continue the ordinary merge in the same run." });
        continue;
      }
      if (!relevant.length && !unsupported.length && (photo || !images.length)) {
        const story = !pr.draft && changed.length === 1 && changed[0].status === "modified"
          && /^content\/photo_stories\/[a-z0-9-]+\.json$/.test(paths[0]) && handoff(pr, ["cast_story"]) === "current_head_claim";
        outside.push({ ...binding, reason: photo ? "photo_or_cast_story" : "outside_first_read_scope",
          ...(story ? { finalizer_title_prefix: "Publication head " + pr.head.sha } : {}) });
        continue;
      }
      if (state === "current_head_claim" && pr.draft === false) {
        const ci = await ciState(pr);
        if (ci.state === "ci_pending") {
          machine.push({ ...binding, ...ci, verification: "self_reported_handoff_only", paths, targets: references });
          continue;
        }
        attention.push({ ...binding, ...ci, reason: ci.state === "ci_success" ? "publication_unverified" : ci.state,
          handoff: state, paths, targets: references,
          ...(ci.state === "ci_success" ? { next_operation: "Inspect the existing finalizer for this PR/head. "
            + "If it is active, leave it running. If blocked or missing, the current owner must recover this same "
            + "handoff after checking current main. Do not repeat first read, create another PR, or rerun blindly." } : {}) });
        // A current-head claimed first read needs gate recovery, not a duplicate
        // first read. This row is explicitly unresolved, never a normal zero.
        continue;
      }
      if (unsupported.length) attention.push({ ...binding, reason: "market_inputs_require_existing_review", paths: unsupported });
      if (!targets.length) {
        if (images.length) attention.push({ ...binding, reason: "image_only_requires_existing_review", paths: images });
        if (!unsupported.length && !images.length)
          outside.push({ ...binding, reason: "no_new_reader_text_against_merge_base_and_main" });
      }
      if (state !== "absent") attention.push({ ...binding,
        reason: state === "current_head_claim" ? "draft_with_handoff" : "handoff_" + state, targets: references });
      if (pr.mergeable === false || ["dirty", "blocked"].includes(pr.mergeable_state))
        attention.push({ ...binding, reason: "merge_blocked", targets: references });
      candidates.push(...targets);
    }
    require(JSON.stringify((await inventory()).map(identity)) === observed, "Open PR inventory changed during review");
    await main();
    const selected = candidates.slice(0, limit);
    const pending = candidates.slice(limit).map(({ reader, ...row }) => row);
    // Keep the existing first-read and the photograph lookup within the same cold cell
    // only when its four inventory reads leave room for every optional callback.
    let photoRecovery;
    if (selected.length) photoRecovery = { status: "after_first_read" };
    else if (reads > 4) photoRecovery = { status: "lookup_required", next_operation: "readPhotoHandoffs" };
    else photoRecovery = await readPhotoHandoffs({ fetchRepository, fetchFile, ref, report: photoReport }, 14 - reads);
    return { schema_version: 1, verification: "review_input_only", review_completed: false,
      publication_authorized: false, source: { repository: REPOSITORY, ref },
      scope: "open_pr_reader_text", open_prs: "complete", open_pr_count: prs.length,
      total: candidates.length, selected_count: selected.length, pending_count: pending.length,
      selected, pending, attention, machine_pending: machine, outside_scope: outside, already_on_main: current,
      photo_recovery: photoRecovery, open_prs_empty: prs.length === 0,
      empty: prs.length === 0 && photoRecovery.status === "checked" && !photoRecovery.selected.length
        && !photoRecovery.attention.length && photoRecovery.next_offset === null,
      note: "Read the reader fields before opening PR explanations. Retain pending and attention in the existing handoff. "
        + "This snapshot is not a lock; existing guards still decide ownership and publication. "
        + "Main-only changes and market outlook/serial widgets need their existing review." };
  }
  function docsOnlyFiles(files, pageLimit) {
    // At the API's page/file ceiling there may be unseen paths.
    const inside = path => typeof path === "string" && path.startsWith("docs/") && path.length > 5;
    require(Array.isArray(files) && files.length < pageLimit && files.every(f => f && inside(f.filename)
      && ["added", "modified", "removed", "renamed", "copied", "changed"].includes(f.status)
      && (!["renamed", "copied"].includes(f.status) || inside(f.previous_filename))
      && (f.previous_filename === undefined || inside(f.previous_filename))),
      "Incomplete or non-docs photograph-check history");
  }
  async function priorHandoffParent(get, ref) {
    // The merge-base and the actual first parent must agree. A net diff can
    // hide a content change later reverted inside a merged branch.
    const diff = await get(API + "/compare/" + ref + "~1..." + ref + "?per_page=8");
    const base = diff.base_commit?.sha, commits = diff.commits;
    require(diff.status === "ahead" && matches(GIT, base) && base !== ref && diff.merge_base_commit?.sha === base
      && diff.behind_by === 0 && Array.isArray(commits) && commits.length >= 1 && commits.length <= 8
      && diff.total_commits === commits.length && diff.ahead_by === commits.length
      && commits.every(c => matches(GIT, c?.sha) && c.sha !== base)
      && new Set(commits.map(c => c.sha)).size === commits.length
      && commits.at(-1).sha === ref && commits.at(-1).parents?.[0]?.sha === base,
      "Incomplete photograph-check ancestor comparison");
    // daily-metrics appends one dated snapshot in a direct commit. Neither
    // report_day nor the photograph inventory consumes audience counters.
    const file = diff.files?.[0];
    if (commits.length === 1 && Array.isArray(commits[0].parents) && commits[0].parents.length === 1 && Array.isArray(diff.files)
      && diff.files.length === 1 && file?.status === "added" && file.previous_filename === undefined
      && matches(/^analytics\/daily\/\d{4}-\d\d-\d\d\.json$/, file.filename)
      && calendarDay(file.filename.slice("analytics/daily/".length, -".json".length))) return base;
    docsOnlyFiles(diff.files, 300);
    for (const { sha } of commits.slice(0, -1)) {
      const commit = await get(API + "/commits/" + sha + "?per_page=100");
      require(commit.sha === sha, "Wrong photograph-check intermediate commit");
      docsOnlyFiles(commit.files, 100);
    }
    return base;
  }
  async function publishedCheckReport(get, ref) {
    let checkRef = ref;
    for (;;) {
      const data = await get(API + "/commits/" + checkRef + "/check-runs?check_name=Photograph%20completion&per_page=1");
      if (data.total_count === 0 && Array.isArray(data.check_runs) && data.check_runs.length === 0) {
        checkRef = await priorHandoffParent(get, checkRef);
        continue;
      }
      const check = data.check_runs?.[0];
      require(check?.name === "Photograph completion" && check.head_sha === checkRef
        && check.app?.slug === "github-actions" && check.status === "completed"
        && ["success", "failure"].includes(check.conclusion) && typeof check.output?.text === "string",
        "Revision-bound photograph check unavailable; use the same revision's live-photo-health artifact");
      return { checkRef, report: JSON.parse(check.output.text) };
    }
  }
  // One day's pre-writing input. Article validation and publication use their own checks.
  async function readEditorialGuidance({ fetchRepository, ref, day } = {}) {
    try {
      require(typeof fetchRepository === "function" && matches(GIT, ref) && calendarDay(day), "Invalid editorial guidance input");
      let reads = 0;
      async function get(url) {
        require(++reads <= 14, "Editorial guidance read budget reached");
        return unwrap(await fetchRepository({ url }), url, false);
      }
      const { checkRef, report } = await publishedCheckReport(get, ref);
      require(report?.checkout_sha === checkRef && object(report.editorial_guidance), "Missing editorial guidance");
      const value = report.editorial_guidance[day];
      require(typeof value === "string" && value.startsWith(day + ": published="), "Missing requested day");
      const [summary, topicHeading, ...topics] = value.split("\n");
      require(/^\d+; tracked=\d+; /.test(summary.slice((day + ": published=").length))
        && /; desk_next=[^;\s][^;]*/.test(summary) && topicHeading?.startsWith("題材比較（指定JST日までの直近7本")
        && topics.length >= 1 && topics.length <= 7
        && (topics.every(line => /^- \d\d\/\d\d \d\d:\d\d｜[^｜]+｜.+｜.+$/.test(line))
          || (topics.length === 1 && topics[0] === "- 比較できる公開日時のある記事なし。")), "Incomplete editorial guidance");
      if (checkRef !== ref) require((await get(API + "/git/ref/heads/main")).object?.sha === ref, "Main moved");
      return { status: "verified", ref, check_sha: checkRef, day, report: value };
    } catch (_) {
      return { status: "unavailable" };
    }
  }
  // A merged news PR can still own its missing photograph. A source pointer
  // does not grant generation, adoption, or permission to write.
  async function readPhotoHandoffs({ fetchRepository, fetchFile, ref, report, offset = 0, batchSize = 4 } = {}, readBudget = 14) {
    try {
      require(typeof fetchRepository === "function" && matches(GIT, ref)
        && Number.isInteger(readBudget) && readBudget >= 1 && readBudget <= 14
        && Number.isSafeInteger(offset) && offset >= 0 && Number.isInteger(batchSize) && batchSize >= 1 && batchSize <= 4,
          "Invalid photograph lookup");
      let operations = 0;
      function countRead() { require(++operations <= readBudget, "Photo read budget reached; narrow batchSize or check the open owner"); }
      async function get(url) { countRead(); return unwrap(await fetchRepository({ url }), url, false); }
      let checkRef = ref;
      if (report === undefined) ({ checkRef, report } = await publishedCheckReport(get, ref));
      require(report?.checkout_sha === checkRef && Array.isArray(report.photo_followup), "Stale or missing photo inventory");
      const seen = new Set();
      for (const row of report.photo_followup) {
        require(matches(SLUG, row?.slug) && matches(HASH, row.article_sha256) && !seen.has(row.slug)
          && ["missing", "pending", "rejected"].includes(row.status), "Invalid photograph inventory row");
        seen.add(row.slug);
      }
      const rows = report.photo_followup.filter(row => row.status !== "rejected");
      let selected = [];
      const attention = [];
      let cursor = offset;
      let open;
      async function listOpen() {
        const prs = await get(API + OPEN + "&per_page=100");
        require(Array.isArray(prs) && prs.length < 100, "Open photograph owner inventory incomplete");
        prs.forEach(identity);
        return prs;
      }
      // Keep each read within the connector callback budget, even with a large backlog.
      for (; cursor < rows.length && cursor < offset + batchSize && selected.length < 3; cursor++) {
        // After a carried check yields a candidate, reserve one file listing
        // per open PR as well as the final owner and main checks.
        if (checkRef !== ref && selected.length && !open) open = await listOpen();
        if (checkRef !== ref && operations + (open ? 4 + open.length : 5) > readBudget) {
          require(cursor > offset, "Photo read budget reached before checking a handoff");
          break;
        }
        const row = rows[cursor];
        const query = "repo:" + REPOSITORY + ' is:pr is:merged base:main in:body "photo-recovery" "' + row.article_sha256 + '"';
        const hits = await get("https://api.github.com/search/issues?q=" + encodeURIComponent(query) + "&per_page=2");
        require(hits.incomplete_results === false && Number.isSafeInteger(hits.total_count)
          && hits.total_count >= 0 && Array.isArray(hits.items) && hits.items.length === Math.min(2, hits.total_count), "Incomplete handoff search");
        const source = { slug: row.slug, article_sha256: row.article_sha256, status: row.status };
        if (hits.total_count !== 1) {
          attention.push({ ...source, reason: hits.total_count ? "ambiguous_source_pr" : "source_pr_not_found" });
          continue;
        }
        const number = hits.items[0].number;
        require(Number.isSafeInteger(number) && number > 0, "Invalid handoff PR number");
        const pr = await get(API + "/pulls/" + number);
        if (pr.number !== number || pr.state !== "closed" || pr.merged !== true
            || pr.base?.ref !== "main" || pr.base.repo?.full_name !== REPOSITORY
            || pr.head?.repo?.full_name !== REPOSITORY || !matches(GIT, pr.head.sha)
            || !["OWNER", "MEMBER", "COLLABORATOR"].includes(pr.author_association)) {
          attention.push({ ...source, pr: number, reason: "source_pr_mismatch" });
        } else if (!/^<!-- photo-recovery(?::[1-9][0-9]*)? -->\r?$/m.test(pr.body || "")) {
          attention.push({ ...source, pr: number, reason: "source_recovery_marker_missing" });
        } else if (handoff(pr, ["news"]) !== "current_head_claim") {
          attention.push({ ...source, pr: number, reason: "source_approval_not_current" });
        } else {
          const approval = JSON.parse(pr.body.match(/<!-- publication-finalize\s*\n([\s\S]*?)\n-->/)[1]);
          if (approval.article_sha256[row.slug] === row.article_sha256)
            selected.push({ ...source, pr: number, head: pr.head.sha });
          else attention.push({ ...source, pr: number, reason: "source_article_sha_mismatch" });
        }
      }
      if (selected.length) {
        if (!open) open = await listOpen();
        const occupied = new Set();
        const shared = {
          "content/articles.json": null, "assets/generated/manifest.json": ["images", "rejected"],
          "content/visual_reviews.json": ["reviews"], "content/approved_photos.json": ["approved"] };
        async function recordMap(path, revision) {
          require(typeof fetchFile === "function", "Use fetchFile to verify the open owner's article data");
          countRead();
          const raw = unwrap(await fetchFile({ repository_full_name: REPOSITORY, path, ref: revision, encoding: "utf-8" }), path, true);
          const data = JSON.parse(raw.content);
          if (path === "content/articles.json") {
            require(Array.isArray(data), "Invalid open article records");
            return slug => data.filter(row => row.slug === slug);
          }
          require(shared[path].every(field => object(data[field])), "Invalid open photograph records");
          return slug => shared[path].map(field => data[field][slug] ?? null);
        }
        for (const pr of open) {
          const changes = await get(API + "/pulls/" + pr.number + "/files?per_page=100");
          require(Array.isArray(changes) && changes.length < 100 && changes.every(f => typeof f.filename === "string"),
            "Incomplete open PR files");
          const paths = new Set(changes.flatMap(f => [f.filename, f.previous_filename].filter(Boolean)));
          const mark = row => { if (!occupied.has(row.slug)) attention.push({
            slug: row.slug, reason: "open_owner", pr: pr.number }); occupied.add(row.slug); };
          const declared = new Set([...(pr.body || "").matchAll(/^[ *-]*(?:対象\s*slug|slug)\s*[:：]\s*`?([a-z0-9-]+)`?[ *]*$/gmi)].map(m => m[1]));
          for (const section of (pr.body || "").matchAll(/^##[ \t]+対象[ \t]*\r?\n([\s\S]*?)(?=^##[ \t]|(?![\s\S]))/gm))
            for (const match of section[1].matchAll(/公開記事\s+`([a-z0-9-]+)`/g)) declared.add(match[1]);
          for (const row of selected) {
            if (declared.has(row.slug) || [...paths].some(path => path.includes("/" + row.slug + ".")
                || path.includes("/" + row.slug + "/"))) mark(row);
          }
          const common = Object.keys(shared).filter(path => paths.has(path));
          if (!common.length || occupied.size === selected.length) continue;
          const base = (await get(API + "/compare/" + pr.base.sha + "..." + pr.head.sha + "?per_page=1")).merge_base_commit?.sha;
          require(matches(GIT, base), "Open PR merge base unavailable");
          for (const path of common) {
            const before = await recordMap(path, base), after = await recordMap(path, pr.head.sha);
            for (const row of selected.filter(r => !occupied.has(r.slug))) {
              const records = [before(row.slug), after(row.slug)];
              // Large generation seeds can lose precision when parsed by JavaScript.
              let unsafe = false;
              const serial = records.map(value => JSON.stringify(value, (_key, item) => {
                if (Number.isInteger(item) && !Number.isSafeInteger(item)) unsafe = true;
                return item;
              }));
              if (unsafe || serial[0] !== serial[1]) mark(row);
            }
          }
        }
        require(JSON.stringify(open.map(identity)) === JSON.stringify((await listOpen()).map(identity)),
          "Open photograph owners changed during lookup");
        selected = selected.filter(row => !occupied.has(row.slug));
      }
      const main = await get(API + "/git/ref/heads/main");
      require(main.object?.sha === ref, "Main moved during photograph lookup");
      return { status: "checked", check_sha: checkRef, selected: selected.sort((a, b) => a.pr - b.pr), attention,
        next_offset: cursor < rows.length ? cursor : null, may_generate: false };
    } catch (error) {
      const reason = String(error.message || error);
      if (readBudget < 14 && reason.startsWith("Photo read budget reached"))
        return { status: "lookup_required", next_operation: "readPhotoHandoffs", may_generate: false };
      return { status: "unverified", reason, may_generate: false };
    }
  }
  // One bounded observation of an existing handoff, not a watcher or a new
  // approval. A discovered run must also supply evidence for the exact handoff.
  async function readHandoffStatus(options = {}) {
    return readStatus(options, false);
  }
  // Use after the independent first read. This separate entry point can inspect
  // an unapproved draft's existing CI without changing the blinded read payload
  // or relaxing any publication handoff requirement.
  async function readValidationStatus(options = {}) {
    return readStatus(options, true);
  }
  async function readStatus({ fetchRepository, fetchJobLogs, ref, pr: number, head,
    ci_run_id, finalizer_run_id, verification_run_id } = {}, validationOnly = false) {
    const positive = value => Number.isSafeInteger(value) && value > 0;
    const result = { schema_version: 1, verification: validationOnly ? "ci_validation_only" : "handoff_status_only",
      publication_authorized: false, review_completed: false, photo_adoption: "not_assessed",
      ...(validationOnly ? { release_readiness: "not_assessed" } : {}),
      source: { repository: REPOSITORY, ref: matches(GIT, ref) ? ref : null },
      pr: positive(number) ? number : null, head: matches(GIT, head) ? head : null,
      status: "attention", stage: "input", evidence: {} };
    const VERIFY = ".github/workflows/verify-public-photos.yml";
    const FINALIZE = "Finalize only the exact explicitly approved publication head";
    const LIVE_STEPS = ["Bind verification to a full main revision before selecting its files",
      "Wait for the public revision or a proven docs-only ancestor",
      "Verify the published revision and live stories without generating traffic",
      "Verify that Twitterbot can fetch each first-party image card"];
    const discoverCI = validationOnly && ci_run_id === undefined;
    const runs = [], maxLog = 1024 * 1024;
    let snapshot, ciListing, reads = 0, logReads = 0;
    function check(ok, reason) { if (!ok) throw Error(reason); }
    async function get(path) {
      check(++reads <= 22, "request_bound_exceeded");
      let reply;
      try { reply = await fetchRepository({ url: API + path }); }
      catch (_) { const error = Error("github_get_failed"); error.operation = path; throw error; }
      try { return unwrap(reply, path, false); }
      catch (_) { const error = Error("github_response_unverified"); error.operation = path; throw error; }
    }
    const prIdentity = value => [value.number, value.state, value.draft, value.merged,
      value.head?.sha, value.head?.ref, value.head?.repo?.full_name, value.base?.ref,
      value.base?.repo?.full_name, value.merge_commit_sha, value.body];
    const runIdentity = value => [value.id, value.path, value.event, value.head_sha,
      value.head_branch, value.repository?.full_name, value.head_repository?.full_name,
      value.run_attempt, value.status, value.conclusion, value.updated_at];
    async function latestCI() {
      const listing = await get("/actions/runs?event=pull_request&head_sha=" + head + "&per_page=100");
      check(Array.isArray(listing.workflow_runs) && listing.total_count === listing.workflow_runs.length
        && listing.total_count <= 100, "ci_inventory_incomplete");
      const candidates = listing.workflow_runs.filter(run => run.path === CI_WORKFLOW
        && run.event === "pull_request" && run.head_sha === head);
      if (discoverCI && !candidates.length) throw Error("ci_run_not_observed");
      check(candidates.length > 0 && candidates.every(run => positive(run.id))
        && new Set(candidates.map(run => run.id)).size === candidates.length, "ci_inventory_unverified");
      const latest = candidates.reduce((a, b) => a.id > b.id ? a : b);
      check((discoverCI || latest.id === ci_run_id) && positive(latest.run_attempt), "newer_or_unrelated_ci_run");
      return [latest.id, latest.run_attempt];
    }
    async function run(id, workflow, events, sha, branch, label, allowMain = false) {
      check(positive(id), label + "_run_id_required");
      const value = await get("/actions/runs/" + id);
      const exactHead = value.head_sha === sha && value.head_branch === branch;
      // GitHub reports workflow_run on trusted main. Its PR/CI identity must
      // come from the completed finalizer result, never from main alone.
      const boundMetadata = allowMain
        ? value.event === "pull_request_target" && exactHead
          || value.head_branch === "main" && matches(GIT, value.head_sha)
        : exactHead;
      check(value.id === id && value.path === workflow && events.includes(value.event)
        && boundMetadata
        && value.repository?.full_name === REPOSITORY && value.head_repository?.full_name === REPOSITORY
        && positive(value.run_attempt), label + "_run_unbound");
      runs.push(value);
      result.evidence[label] = { run_id: id, run_attempt: value.run_attempt,
        status: value.status, conclusion: value.conclusion,
        url: "https://github.com/" + REPOSITORY + "/actions/runs/" + id };
      return value;
    }
    async function job(value, name, label) {
      // The dedicated connector jobs wrapper omits head/attempt and total_count.
      // Read the complete REST object rather than accepting that lossy wrapper.
      const listing = await get("/actions/runs/" + value.id + "/jobs?filter=latest&per_page=100");
      check(Array.isArray(listing.jobs) && listing.total_count === listing.jobs.length
        && listing.total_count <= 100 && listing.jobs.every(item => object(item) && positive(item.id))
        && new Set(listing.jobs.map(item => item.id)).size === listing.jobs.length, label + "_jobs_incomplete");
      const matching = listing.jobs.filter(item => item.name === name);
      check(matching.length === 1, label + "_job_unverified");
      const selected = matching[0];
      check(selected.run_id === value.id && selected.head_sha === value.head_sha
        && selected.head_branch === value.head_branch && selected.run_attempt === value.run_attempt,
      label + "_job_attempt_unbound");
      Object.assign(result.evidence[label], { job_id: selected.id, job_status: selected.status,
        job_conclusion: selected.conclusion,
        url: result.evidence[label].url + "/job/" + selected.id });
      check(selected.conclusion !== "skipped", label + "_job_skipped");
      return selected;
    }
    function completed(value, selected, label) {
      check(value.status === "completed" && value.conclusion === "success"
        && selected.status === "completed" && selected.conclusion === "success", label + "_not_successful");
    }
    function step(selected, name, conclusion = "success") {
      check(Array.isArray(selected.steps) && selected.steps.length <= 100, "steps_unverified");
      const found = selected.steps.filter(item => item.name === name);
      check(found.length === 1 && found[0].status === "completed" && found[0].conclusion === conclusion
        && Number.isFinite(Date.parse(found[0].started_at)) && Number.isFinite(Date.parse(found[0].completed_at))
        && Date.parse(found[0].started_at) <= Date.parse(found[0].completed_at),
      conclusion === "failure" ? "failed_finalizer_step_unverified" : "required_step_not_successful");
      return found[0];
    }
    async function log(selected) {
      check(typeof fetchJobLogs === "function" && ++logReads <= 2, "job_log_reader_required");
      let value;
      try { value = await fetchJobLogs({ repo_full_name: REPOSITORY, job_id: selected.id }); }
      catch (_) { throw Error("job_log_fetch_failed"); }
      for (let depth = 0; depth < 7; depth++) {
        check(!object(value) || !(value.isError || value.error || value.error_code
          || value.status === "ERROR" || value.truncated || value.content_truncated || value.incomplete_results), "job_log_incomplete");
        if (object(value) && value.structuredContent !== undefined) { value = value.structuredContent; continue; }
        if (object(value) && value.result !== undefined) { value = value.result; continue; }
        const content = typeof value === "string" ? value : value?.content;
        check(typeof content === "string" && content.length > 0 && content.length <= maxLog, "job_log_unverified_or_too_large");
        return content.split(/\r?\n/).flatMap(line => {
          const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z) (.*)$/.exec(line);
          return match && Number.isFinite(Date.parse(match[1])) ? [{ at: match[1], ms: Date.parse(match[1]), text: match[2] }] : [];
        });
      }
      throw Error("job_log_wrapper_unverified");
    }
    function within(lines, execution) {
      const start = Date.parse(execution.started_at), end = Date.parse(execution.completed_at) + 1000;
      return lines.filter(line => line.ms >= start && line.ms < end);
    }
    async function finish(status, stage) {
      // A stability recheck, with no sleep or retry, prevents old attempts/logs
      // from becoming a success if this handoff changes during the snapshot.
      result.stage = "snapshot";
      const fresh = await get("/pulls/" + number);
      check(JSON.stringify(prIdentity(fresh)) === JSON.stringify(prIdentity(snapshot)), "pr_changed_during_snapshot");
      for (const value of runs) {
        const latest = await get("/actions/runs/" + value.id);
        check(JSON.stringify(runIdentity(latest)) === JSON.stringify(runIdentity(value)), "run_changed_during_snapshot");
      }
      check(JSON.stringify(await latestCI()) === JSON.stringify(ciListing), "ci_changed_during_snapshot");
      check((await get("/git/ref/heads/main")).object?.sha === ref, "main_changed_during_snapshot");
      result.status = status;
      result.stage = stage || (validationOnly ? "ci" : status === "verified" ? "delivery"
        : status === "verification_pending" ? "live" : status.replace(/_pending$/, ""));
      result.reads = { repository: reads, logs: logReads };
      return result;
    }
    try {
      check(typeof fetchRepository === "function" && matches(GIT, ref) && positive(number)
        && matches(GIT, head) && (positive(ci_run_id) || discoverCI), "invalid_status_input");
      check((await get("/git/ref/heads/main")).object?.sha === ref, "main_changed");
      result.stage = "pr";
      snapshot = await get("/pulls/" + number);
      check(snapshot.number === number && snapshot.head?.sha === head
        && typeof snapshot.head?.ref === "string" && snapshot.head.ref.length > 0
        && snapshot.head.repo?.full_name === REPOSITORY && snapshot.base?.repo?.full_name === REPOSITORY
        && snapshot.base.ref === "main"
        && (validationOnly ? typeof snapshot.draft === "boolean" && snapshot.state === "open" && snapshot.merged === false
          : snapshot.draft === false && ((snapshot.state === "open" && snapshot.merged === false)
            || (snapshot.state === "closed" && snapshot.merged === true))), "pr_head_or_state_unbound");
      const story = !validationOnly && !snapshot.merged && handoff(snapshot, ["cast_story"]) === "current_head_claim";
      if (!validationOnly) check(story || handoff(snapshot) === "current_head_claim", "current_head_handoff_required");
      if (snapshot.merged) {
        check(matches(GIT, snapshot.merge_commit_sha), "merge_sha_unverified");
        const commit = await get("/git/commits/" + snapshot.merge_commit_sha);
        check(commit.sha === snapshot.merge_commit_sha && Array.isArray(commit.parents) && commit.parents.length === 2
          && matches(GIT, commit.parents[0]?.sha) && commit.parents[1]?.sha === head, "approved_head_not_merge_parent");
        const ancestry = await get("/compare/" + snapshot.merge_commit_sha + "..." + ref);
        check(["ahead", "identical"].includes(ancestry.status)
          && ancestry.merge_base_commit?.sha === snapshot.merge_commit_sha, "merge_not_on_fixed_main");
        result.merge_sha = snapshot.merge_commit_sha;
      }
      result.stage = "ci";
      ciListing = await latestCI();
      const ci = await run(ciListing[0], CI_WORKFLOW, ["pull_request"], head, snapshot.head.ref, "ci");
      check(ci.run_attempt === ciListing[1], "ci_attempt_unbound");
      check(Array.isArray(ci.pull_requests), "ci_pr_binding_unverified");
      const linked = ci.pull_requests.filter(item => item.number === number && item.head?.sha === head
        && item.base?.ref === "main" && matches(GIT, item.base?.sha));
      // GitHub removes pull_requests after merge. The exact normal-merge parent,
      // repository, branch and CI head above preserve the historical binding.
      check(linked.length === 1 || (snapshot.merged && ci.pull_requests.length === 0), "ci_pr_binding_unverified");
      if (validationOnly) {
        result.evidence.ci.base_sha = linked[0].base.sha;
        result.evidence.ci.base_matches_main = linked[0].base.sha === ref;
        if (ACTIVE_RUN.has(ci.status)) return await finish("ci_pending");
        if (ci.status === "completed" && ["failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"].includes(ci.conclusion))
          return await finish("ci_failed");
      }
      const ciJob = await job(ci, "test", "ci");
      if (ACTIVE_RUN.has(ci.status) && (ACTIVE_RUN.has(ciJob.status) || ciJob.conclusion === "success")) return await finish("ci_pending");
      if (validationOnly && ciJob.status === "completed" && ciJob.conclusion === "failure") return await finish("ci_failed");
      completed(ci, ciJob, "ci");
      if (validationOnly) {
        const names = ["Validate article fields, editorial lanes and exact duplicate guards",
          "Run Python checks with a fresh full site build"];
        result.evidence.ci.validated_steps = names.map(name => {
          const value = step(ciJob, name);
          return { name, status: value.status, conclusion: value.conclusion, completed_at: value.completed_at };
        });
        return await finish("ci_verified");
      }
      result.stage = "finalizer";
      const failureScope = story ? "story" : "publication";
      if (!snapshot.merged && finalizer_run_id === undefined) {
        // workflow_run belongs to main. The title locates its candidate; only the
        // trusted step output below can bind the failure to this PR and CI.
        check(Number.isFinite(Date.parse(ci.created_at)), "ci_creation_time_unverified");
        const listing = await get("/actions/runs?event=workflow_run&branch=main&status=failure&created="
          + encodeURIComponent(">=" + ci.created_at) + "&per_page=100");
        check(Array.isArray(listing.workflow_runs) && listing.total_count === listing.workflow_runs.length
          && listing.total_count <= 100, failureScope + "_finalizer_inventory_incomplete");
        const title = "Publication head " + head + " (CI " + ci.id + "/" + ci.run_attempt + ")";
        const matching = listing.workflow_runs.filter(item => item.path === VERIFY && item.event === "workflow_run"
          && item.head_branch === "main" && item.display_title === title && positive(item.id));
        check(matching.length === 1, failureScope + "_finalizer_unverified");
        finalizer_run_id = matching[0].id;
      }
      const finalizer = await run(finalizer_run_id, VERIFY, ["pull_request_target", "workflow_run"],
        head, snapshot.head.ref, "finalizer", true);
      const mainFinalizer = finalizer.head_branch === "main";
      const failed = !snapshot.merged && finalizer.status === "completed" && finalizer.conclusion === "failure";
      if (story) check(failed, "story_finalizer_failure_unconfirmed");
      const finalizerJob = await job(finalizer, "finalize", "finalizer");
      if (ACTIVE_RUN.has(finalizer.status) && (ACTIVE_RUN.has(finalizerJob.status) || finalizerJob.conclusion === "success")) {
        check(!mainFinalizer, "finalizer_handoff_not_yet_bound");
        return await finish("finalizer_pending");
      }
      if (failed) check(finalizerJob.status === "completed" && finalizerJob.conclusion === "failure", failureScope + "_finalizer_failure_unconfirmed");
      else completed(finalizer, finalizerJob, "finalizer");
      const execution = step(finalizerJob, FINALIZE, failed ? "failure" : "success");
      const finalizerLines = within(await log(finalizerJob), execution);
      const outcomes = finalizerLines.flatMap(line => {
        if (!line.text.startsWith("{")) return [];
        try { const value = JSON.parse(line.text); return object(value) && typeof value.status === "string" ? [value] : []; }
        catch (_) { return []; }
      });
      check(outcomes.length === 1, "finalizer_result_unverified");
      const outcome = outcomes[0];
      if (failed) {
        check(outcome.status === "unverified" && outcome.stage === "latest_main"
          && handoff(snapshot, [outcome.kind]) === "current_head_claim"
          && outcome.pr === number && outcome.expected_head === head && outcome.current_head === head
          && outcome.ci_run_id === ci.id && outcome.ci_run_attempt === ci.run_attempt
          && outcome.tested_main === linked[0].base.sha && matches(GIT, outcome.current_main)
          && outcome.current_main !== outcome.tested_main, failureScope + "_failure_binding_unverified");
      } else {
        check(outcome.status === "verification_dispatched" && snapshot.merged
          && outcome.merge_sha === result.merge_sha, "finalizer_merge_unverified");
        const binding = ["pr", "head", "ci_run_id", "ci_run_attempt"];
        if (mainFinalizer || binding.some(key => Object.hasOwn(outcome, key))) {
          check(outcome.pr === number && outcome.head === head && outcome.ci_run_id === ci.id
            && outcome.ci_run_attempt === ci.run_attempt, "finalizer_handoff_binding_unverified");
        }
      }
      if (mainFinalizer) {
        const ancestry = await get("/compare/" + finalizer.head_sha + "..." + ref);
        check(["ahead", "identical"].includes(ancestry.status)
          && ancestry.merge_base_commit?.sha === finalizer.head_sha, "finalizer_not_on_fixed_main");
      }
      if (failed) {
        if (outcome.current_main !== ref && !(mainFinalizer && outcome.current_main === finalizer.head_sha)) {
          const ancestry = await get("/compare/" + outcome.current_main + "..." + ref);
          check(ancestry.status === "ahead" && ancestry.merge_base_commit?.sha === outcome.current_main, "stopped_main_unverified");
        }
        await finish("attention", "latest_main");
        result.reason = "latest_main_requires_fresh_ci";
        result.evidence.finalizer.tested_main = outcome.tested_main;
        result.evidence.finalizer.current_main = outcome.current_main;
        result.next_operation = "Check the current owner and active runs before recovering this same PR under docs/OPERATIONS.md.";
        return result;
      }
      result.stage = "live";
      check(positive(verification_run_id), "verification_run_id_required");
      const live = await run(verification_run_id, VERIFY, ["push", "workflow_dispatch"], result.merge_sha, "main", "live");
      const liveJob = await job(live, "check", "live");
      if (ACTIVE_RUN.has(live.status) && (ACTIVE_RUN.has(liveJob.status) || liveJob.conclusion === "success")) return await finish("verification_pending");
      completed(live, liveJob, "live");
      const steps = LIVE_STEPS.map(name => step(liveJob, name)), lines = await log(liveJob);
      const waited = within(lines, steps[1]).filter(line => line.text.startsWith("Public site confirmed serving"));
      const verified = within(lines, steps[2]).filter(line => line.text.startsWith("Public revision verified:"));
      const cards = within(lines, steps[3]).filter(line => /^Verified [1-9][0-9]* first-party share images with Twitterbot UA\.$/.test(line.text));
      check(waited.length === 1 && waited[0].text === "Public site confirmed serving requested commit: " + result.merge_sha
        && verified.length === 1 && verified[0].text === "Public revision verified: live=" + result.merge_sha + " expected=" + result.merge_sha
        && cards.length === 1, "exact_live_revision_or_cards_unverified");
      result.expected_sha = result.merge_sha;
      result.live_sha = result.merge_sha;
      result.verified_at = cards[0].at;
      return await finish("verified");
    } catch (error) {
      result.reason = error.message;
      if (error.operation) result.operation = error.operation;
      result.reads = { repository: reads, logs: logReads };
      return result;
    }
  }
  return Object.freeze({ read, readHandoffStatus, readValidationStatus, readPhotoHandoffs, readEditorialGuidance });
})();
if (typeof module !== "undefined" && module.exports) module.exports = PublicationReviewContext;
