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
  function handoff(pr) {
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
        || claim.version !== 1 || !["news", "market"].includes(claim.kind)
        || claim.first_read !== "passed" || claim.merge_after_ci !== true
        || !matches(GIT, claim.approved_head)
        || !matches(LOGIN, claim.authorized_by)
        || !["author_run", "reviewer_run"].every(key => matches(RUN, claim[key]))
        || claim.author_run === claim.reviewer_run || !object(claim.article_sha256)
        || claim.kind === "news" && !Object.keys(claim.article_sha256).length
        || !Object.entries(claim.article_sha256).every(([slug, hash]) =>
          matches(SLUG, slug) && matches(HASH, hash)))
      return "invalid";
    return claim.approved_head === pr.head.sha ? "current_head_claim" : "stale";
  }
  async function read({ fetchFile, fetchRepository, ref, limit = 3 } = {}) {
    require(typeof fetchFile === "function" && typeof fetchRepository === "function"
      && matches(GIT, ref) && Number.isInteger(limit) && limit >= 1 && limit <= 3,
    "Provide GitHub readers, a full main SHA and a limit from 1 to 3");
    async function get(path) {
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
      if (!relevant.length && !unsupported.length && (photo || !images.length)) {
        outside.push({ ...binding, reason: photo ? "photo_or_cast_story" : "outside_first_read_scope" });
        continue;
      }
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
    return { schema_version: 1, verification: "review_input_only", review_completed: false,
      publication_authorized: false, source: { repository: REPOSITORY, ref },
      scope: "open_pr_reader_text", open_prs: "complete", open_pr_count: prs.length,
      total: candidates.length, selected_count: selected.length, pending_count: pending.length,
      selected, pending, attention, machine_pending: machine, outside_scope: outside, already_on_main: current,
      empty: prs.length === 0,
      note: "Read the reader fields before opening PR explanations. Retain pending and attention in the existing handoff. "
        + "This snapshot is not a lock; existing guards still decide ownership and publication. "
        + "Main-only changes and market outlook/serial widgets need their existing review." };
  }
  return Object.freeze({ read });
})();
if (typeof module !== "undefined" && module.exports) module.exports = PublicationReviewContext;
