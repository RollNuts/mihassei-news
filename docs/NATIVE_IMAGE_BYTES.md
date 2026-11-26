# Native image byte helper

`scripts/native_image_bytes.js` converts an actual ImageGen PNG byte array or base64 data URL to a 1200×675 JPEG at quality 84. It uses a centered, aspect-preserving cover crop, bilinear interpolation, and a white background for transparency. It performs no network requests, file writes, generation, publishing, or approval.

## Preferred path: reserve generation and save its original candidate

Cloud image operations use the live GitHub image-workflow stop state defined in [VISUAL_OPERATIONS](VISUAL_OPERATIONS.md), without a Mac CLI requirement. The helper fails closed for a private repository, disabled/unknown workflow, failed control lookup, or an excessive public JPEG total. It rechecks before generation and remote writes; stopped candidates remain readable. This changes stop control, not byte-bridge availability or visual approval.

When the native generator is available only as a top-level tool outside code mode, use `reserveExternalGenerationStep` → one actual native tool call → `stageReservedStep`. The reservation reads an existing PNG through the actual byte callback, converts it, saves and reads back PNG/JPEG hashes, acquires a durable create-only claim, and rechecks the stop state before returning. `stageReservedStep` verifies that persisted claim and its immutable preflight against the original plan before using the existing storage path. For a generator callable inside code mode, `generateAndStage` uses its original single-run reservation. The compact file-adapter path below also supports resuming a verified preparation before the exclusive start. Neither path generates after a failed check; a saved candidate is recovered first.

A fresh compact reservation checks for an existing candidate or claim, then reads its own probe PNG (at most 512 KiB) through the actual file adapter. The helper verifies PNG/JPEG bytes, receipt and protected files at a fixed probe commit. The article’s create-only claim links directly to that commit. Two articles can reserve independently. An old `capture` checkpoint with a source path and shared head but no proof must still read the original file and pass the same checks.

Legacy reservations and the standalone transport diagnostic use `native-image-preflight/transport`. A compact checkpoint already carrying a shared proof continues from its recorded position. If its saved tree has outlived its expected shared head, the helper verifies those original blobs and anchors the probe in that article’s own claim. A transport sample is never article artwork.

A new compact run saves its verified preparation at `<candidate branch>-ready-<identity>`. If the connection fails before the start, run the same compact entry point with the original plan. A fresh workspace may provide its own temporary copy of the diagnostic fixture. The helper reads it through the same adapter and verifies both the PNG and converted JPEG against the immutable proof. A changed article base returns the original commit to its owner.

Immediately before starting, one fresh GitHub lookup verifies the preparation SHA and checks for a finished candidate. The winner must receive a successful create response for `native-image-generation-claim/<plan.identity>`, confirm its exact ref, check again for a candidate completed during the claim, and pass the final live guard. Only a returned `reserved` permits one generation. An existing claim, including a legacy reservation, allows recovery of the original output only. Keep both refs and the original plan. A missing or ambiguous response after the start never grants a replacement.

For a transport-only diagnostic, `checkTransport(plan, options)` takes the same probe and `readBytes` options without a `generate` callback. It performs the actual transfer/save/readback and creates no generation claim or article candidate. Its returned proof cannot replace either reservation entry point's own actual check.

The ordinary `stage/resume/load` methods remain available for recovery without generation. They store the complete source PNG as ordered parts of at most 512 KiB, plus the JPEG and provenance, together on the article's candidate branch; they do not edit published article/image manifests, write an accepted review, create a PR or deploy. Readback reconstructs the original PNG, verifies its complete SHA-256, and checks that the pinned conversion reproduces the saved JPEG. This avoids the GitHub Contents response that can return an empty content string for a binary file larger than 1 MiB even when the request succeeds. A generated result or failed-save checkpoint must be recovered, not replaced by another generation.

Read both helpers from the latest reviewed repository revision and initialize the existing converter. For an ordinary missing-photo article, first commit the final reviewed body and `scene_prompt` to its existing PR, then prepare once with only the slug and that full immutable commit SHA:

```js
const plan = await stager.prepareArticle({slug, baseCommitSha});
```

This read-only wrapper verifies the Git commit/tree, derives the article fingerprint and exact committed prompt, and fixes `logicalRunId` to `news-photo-v1:<article SHA>`. It uses four connector reads, without generating or writing. It rejects caller overrides, illustration/unknown visual strategies, absent prompts, ambiguous articles, and current photo/review records requiring inspection or an explicit reviewed retry. The article need not already be on main: complete image adoption in the same PR before the normal publication handoff.

**If an existing plan, candidate, claim or checkpoint is recorded, continue that original plan; do not use this wrapper to replan it.** In particular, an older manual logical ID must not be replaced by the new default. Keep manual `prepare` for those original plans and the existing explicit review paths; its supplied article fingerprint and base tree must match the immutable working commit.

Bind `readImageControl` below to the actually exposed `read_image_control` MCP tool, pass the nonce unchanged and return the whole actual response to the stager. Do not unconditionally select `.structuredContent`: the helper normalizes direct, `.result` and legacy `.structuredContent` responses and rejects errors at either level. Connector runtimes that reject GitHub workflow API URLs use this existing read-only tool; do not synthesize an active state or reuse an old reply. The endpoint echoes a per-call nonce and timestamp, both verified by the helper. An unavailable reader stops image operations. Use the same logical run ID on retry; a retry's wall-clock time is not a new identity. A recovery owner inherits the originating plan and logical run ID, rather than replacing them with its later review slot. Check the existing publication/recovery handoff and candidate branch before preparing a new generation.

**Unchanged main does not imply no recovery work.** For each unresolved slug in the existing handoff, check its recorded candidate references and enumerate the matching `native-image-candidate/<slug>/` branches before returning idle. A branch ending in `-ready-<plan.identity>` holds preparation only. On the exact candidate branch, read its new or changed `receipt.json` and compare the slug, current article fingerprint, logical run ID, source/JPEG hashes and existing acceptance/rejection history. Recover an unprocessed matching candidate with its originating plan before generating another image; skip identities already adopted or rejected. Record changed branch/commit and receipt SHA in the existing task result or handoff, without creating another ledger. A failed or incomplete branch lookup is an unresolved lookup, not evidence that no candidate exists; do not replace it with an unchanged-main shortcut.

### Native tool outside code mode: one bounded step per cell

Pin the reviewed main SHA once and retain it with the original plan, probe references and version 2 checkpoint. In each fresh cell fetch `scripts/native_image_bytes.js` and `scripts/native_image_stage.js` from that SHA and evaluate them as `bytesAPI` and `stageAPI`. Bind the existing GitHub callbacks and the live read-only image control. The cell needs these shared inputs:

```js
const services = {bytes:bytesAPI, fetchFile:github.fetch_file, github,
  repository:"RollNuts/mihassei-news", readCloudWorkflow:({nonce})=>readImageControl({nonce})};
```

`compactStep` loads the one pinned hash dependency for metadata work and the three pinned dependencies for conversion. Each operation fits 16 calls including both helper imports, original-file reads, live guards and up to two writes. Paste only the compact metadata into the next cell. Version 2 supports PNGs up to 4 MiB in eight 512 KiB parts. Old version 1 checkpoints and raw archives retain their existing recovery path.

#### Resume a reservation

Call **one** step per code-mode cell. Use the original `plan` and the preceding reservation `checkpoint`, or `undefined` on the first call. Retain the four small strings of `probe` **once**, before the first lookup: the actual file ID/path (`probeSource`), verified SHA-256 (`expectedProbeSha256`), source reference (`probeSourceRef`) and transfer route (`route`). The [offline probe](#offline-png-bootstrap) prints this object for the file-adapter route. Within that run, reuse those values and bind a fresh `readBytes` to the same reader in **every reservation cell**. The first lookup can return a `capture` checkpoint before recording any probe inputs.

```js
try {
  if (checkpoint && checkpoint.version !== 2) throw Error("Expected a compact reservation checkpoint");
  const {probeSource, expectedProbeSha256, probeSourceRef, route} = probe;
  const step = await stageAPI.compactStep(services, "reserveExternalGenerationStep", checkpoint);
  const result = await step(plan, {probeSource, expectedProbeSha256, probeSourceRef, route, readBytes});
  if (result.status === "pending") text({status: result.status, phase: result.phase, checkpoint: result.checkpoint});
  else if (result.status === "reserved") text({status: result.status, plan: result.plan,
    transportPreflight: result.transportPreflight, generationClaim: result.generationClaim});
  else text({status: result.status, commitSha: result.commitSha, branch: result.branch, receipt_sha256: result.receipt_sha256});
} catch (error) {
  text({status: "stopped", message: String(error?.message ?? error), phase: error?.stage,
    checkpoint: error?.checkpoint, last_confirmed_phase: checkpoint?.phase});
}
```

`probeSource` is the real file ID/path string, retained unchanged. The capture reads the existing PNG; its upload step reacquires that same file and verifies both PNG and pinned JPEG hashes before writing. Do not loop all steps inside one `functions.exec`, create self-messages/tasks, or retry to evade an account quota. `pending` is progress, not generation permission. Preserve the plan, checkpoint and original references in the existing handoff if the turn/run ends. Stop on error or no progress; retain the error's checkpoint. The API rejects edited/cross-plan/exhausted checkpoints and bounds total progression to 256 steps.

Only the fresh final `status: "reserved"` response permits the immediately following **one** top-level native generation call with the exact original prompt. The preceding step verifies the immutable preparation receipt. The final cell requires the expected preparation ref, checks for a finished candidate before and after claiming, confirms the new claim ref and checks the live guard. A finished candidate resumes readback. An empty, changed or ambiguous preparation lookup stops a fresh compact run. `saved`, an existing claim, a lost/aborted final reply or a replayed old checkpoint permits recovery only. Never change the logical identity to try again. The claim remains `reserved_generation_unconfirmed`, not evidence that generation happened.

#### Resume saving the original

After the native tool returns, record its actual file ID/path and the **first** real observation time once, together with the returned proof/claim. Start a new `stageReservedStep` with `checkpoint` set to `undefined`. Bind the same [PNG reader](#file-adapter-to-code-mode-png-bytes) in every save cell, including when resuming from the preceding **save** checkpoint.

```js
const input = checkpoint ? {readBytes} : {
  compact: true, originalSourceRef, observedAt: firstObservedAt,
  imageGenResult: {output_hint: originalSourceRef},
  transportPreflight, generationClaim, readBytes
};
try {
  if (checkpoint && checkpoint.version !== 2) throw Error("Expected a compact save checkpoint");
  const step = await stageAPI.compactStep(services, "stageReservedStep", checkpoint);
  const result = await step(plan, input);
  text(result.status === "pending" ? result : {
    status: result.status, commitSha: result.commitSha, branch: result.branch,
    receipt_sha256: result.receipt_sha256, jpegPath: result.jpegPath,
    source_sha256: result.receipt.source.sha256, jpeg_sha256: result.receipt.jpeg.sha256
  });
} catch (error) {
  text({status: "stopped", message: String(error?.message ?? error), phase: error?.stage,
    checkpoint: error?.checkpoint, last_confirmed_phase: checkpoint?.phase});
}
```

The helper reacquires only that exact original file for conversion/upload/receipt phases and checks its original PNG and pinned JPEG hashes. A missing original file or changed bytes stops saving; never retimestamp or regenerate. Original PNG chunks/C2PA are preserved in the existing raw archive, not copied into the checkpoint. The readback checks the actual JPEG and protected files at one immutable candidate commit. Its next cell reconstructs every source part, reproduces the JPEG and C2PA metadata, and confirms the candidate branch before returning `saved`. A completed candidate remains readable while stopped. Print and retain only the compact checkpoint or the small final refs/hashes above, not the whole receipt (which can carry C2PA bytes). For visual review, use a separate cell with `const images = await stager.load(plan, savedCommitSha)` against that exact verified immutable commit, then emit `images.sourcePngDataUrl` and `images.jpegDataUrl` as images; never pass that byte-bearing object to `text`/`notify`. Candidate saving, visual acceptance and public delivery remain separate.

The older `reserveExternalGeneration`/`stageReserved` macros remain compatible for an executor with enough callback capacity; do not fall back to them after this runtime reports a call limit.

### Generator actually callable inside code mode

The following initialization/example is only for a generator actually exposed as a callable code-mode function. A top-level native tool is not a `tools.*` callback; use the external protocol above instead.

```js
// @exec: {"yield_time_ms": 120000, "max_output_tokens": 1500}
const repo = "RollNuts/mihassei-news";
const bytesAPI = new Function(byteHelperSource + "\nreturn NativeImageBytes;")();
const dependencies = await bytesAPI.fetchSources(args =>
  tools.mcp__codex_apps__github_fetch_file(args)
);
const runtime = bytesAPI.initialize(dependencies);
const stageAPI = new Function(stageHelperSource + "\nreturn NativeImageStage;")();
const stager = stageAPI.create({
  runtime, repository: repo,
  readCloudWorkflow: ({nonce}) => readImageControl({nonce}),
  github: {
    fetch_file: args => tools.mcp__codex_apps__github_fetch_file(args),
    create_blob: args => tools.mcp__codex_apps__github_create_blob(args),
    create_tree: args => tools.mcp__codex_apps__github_create_tree(args),
    create_commit: args => tools.mcp__codex_apps__github_create_commit(args),
    create_branch: args => tools.mcp__codex_apps__github_create_branch(args),
    fetch: args => tools.mcp__codex_apps__github_fetch(args),
    update_ref: args => tools.mcp__codex_apps__github_update_ref(args)
  }
});
const plan = await stager.prepare({
  slug, articleSha256, prompt, baseCommitSha, baseTreeSha, logicalRunId
});
// This example checks the actual PNG-data-URL route exposed in this runtime.
// It does not establish a Python/local-file or Library-to-connector byte bridge.
const probeRef = "3fc967e9e856c1e80d8336dbf8cebbbe3d11df7d";
const probePath = "assets/editorial-scenes/robot-job-hopping.png";
const probeSource = {kind: "existing-github-png", ref: probeRef, path: probePath};
const readBytes = async source => {
  if (source === probeSource) {
    const actual = await tools.mcp__codex_apps__github_fetch_file({
      repository_full_name: repo, path: source.path, ref: source.ref, encoding: "base64"
    });
    const file = actual?.structuredContent ?? actual?.result;
    if (actual?.isError || actual?.error_code || actual?.error || file?.isError || file?.error_code || file?.error ||
        file?.encoding !== "base64" || typeof file.content !== "string")
      throw Error("Existing probe PNG could not be read");
    return {image_url: "data:image/png;base64," + file.content};
  }
  if (typeof source?.image_url === "string" && source.image_url.startsWith("data:image/png;base64,"))
    return source;
  throw Error("This runtime has not established a byte bridge for the returned file/path");
};
let actualGeneration;
try {
  const saved = await stager.generateAndStage(plan, {
    probeSource,
    probeSourceRef: repo + "@" + probeRef + ":" + probePath,
    expectedProbeSha256: "c3737d66d45f9ab85beff0f6ca264ffa0311886d052e5737b98f65308aa1b4bb",
    route: "PNG data URL in this code runtime to GitHub candidate storage",
    readBytes,
    generate: args => tools.image_gen__imagegen(args)
  });
  actualGeneration = saved.imageGenResult;
  text({status: saved.status, branch: saved.branch, commitSha: saved.commitSha,
        jpegPath: saved.jpegPath, reused: saved.reused,
        preflightCommit: saved.transportPreflight?.commit_sha});
} catch (error) {
  actualGeneration = error.imageGenResult;
  // Transient same-conversation recovery only; never print this payload.
  store("native-image-retry-" + plan.identity, {
    plan, checkpoint: error.checkpoint, imageGenResult: error.imageGenResult,
    transferredImage: error.transferredImage, observedAt: error.observedAt,
    transportPreflight: error.transportPreflight, generationClaim: error.generationClaim
  });
  text({status: "incomplete", branch: plan.branch, phase: error.stage,
        generationClaim: error.generationClaim?.branch, message: error.message});
  // Preserve real file references/checkpoint in the existing durable handoff.
  // Never invoke generation again to retry a save or a byte transfer.
} finally {
  if (actualGeneration?.image_url?.startsWith("data:")) generatedImage(actualGeneration);
}
```

For a local-file or file-ID generator, use an existing PNG in that actual execution environment as `probeSource`, and make `readBytes` return bytes through an available execution/materialization tool. Use the same function to acquire the eventual generated output. Reusing the GitHub/data-URL sample above does not test that different bridge. Inspect the exposed tool schemas before selecting the callback; if the generator or byte-returning callback is unavailable, do not replace the entry point with a direct generation call. A repository helper cannot add tools to a scheduled worker or prevent a caller from bypassing it.

### File adapter to code-mode PNG bytes

The existing receiver also exposes the read-only `read_candidate_png` tool. The PNG reader additionally permits the bounded Azure account form `^oaisdmntpr[a-z0-9]{1,14}\.blob\.core\.windows\.net$`; JPEG probe/storage still permit only their existing exact observed Azure accounts. This is an untrusted read-only relay: the prefix does not establish OpenAI ownership, generation provenance or adoption eligibility. Bind `readCandidatePng` below to that tool's **actually exposed** code-mode function, and use an original PNG path recognized by the current run's file adapter. For a code-callable generator its callback must return that actual path; for a top-level generator retain its actual returned path for the later save step. A Library ID or a path from another executor is not a substitute. For an observed blocked file reference, use the [Library recovery procedure](#blocked-photo-file-reference) and retain the individual source inventory. The same callback must read both an existing PNG `probeSource` during the capture step and the eventual generated PNG before the first save step.

```js
const readBytes = actualPngPath => readCandidatePng({file: actualPngPath});
```

Return the whole tool response. The stager checks the receiver’s reported SHA-256, byte count and dimensions against the original PNG. The receiver puts the bytes in `structuredContent.image_url` and a metadata summary in `content`. Keep both the response and the data URL in code mode. An existing byte adapter can still return a PNG `{image_url}` object. A bare data URL passed as the callback result stops the step. Resume from its checkpoint with the original file reference and the reader above.

The reader preserves the exact PNG bytes, including C2PA chunks. It checks MIME, bounded PNG structure and dimensions. The existing native helper performs CRC/decode/C2PA parsing and retains source parts, actual prompt and current article provenance through the ordinary candidate flow. Receiver limits are 8 MiB, 8192 pixels per side and 32 million pixels total.

### Offline PNG bootstrap

When this worker exposes a top-level Python executor, create this deterministic transport fixture there using only the standard library (outside `functions.exec`). It avoids raw-GitHub DNS, external downloads and manual Mac attachments:

```python
import base64, hashlib, tempfile
from pathlib import Path
png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAABAAAAAJCAYAAAA7KqwyAAAAUUlEQVR4nNXRMQ0AMAwDQUMMiAILnCLpbAbpF4LHTqdfIkuRpCmdaTTmvc7U0jQa49bm4uYiGuMWM4o5jda7mDYzijmNxrjFjGJOozHu/79wAV5JS/CTcXlMAAAAAElFTkSuQmCC", validate=True)
expected = "e7d768652abcb3292c99905f5544c3eeb91933aef573ee912e7babb3450d1744"
assert len(png) == 138 and hashlib.sha256(png).hexdigest() == expected
with tempfile.NamedTemporaryFile(dir="/mnt/data", prefix="mihassei-probe-", suffix=".png", delete=False) as probe:
    probe.write(png)
    path = Path(probe.name)
print({"probeSource": str(path), "expectedProbeSha256": expected, "probeSourceRef": str(path),
       "route": "read_candidate_png through the original file adapter"})
```

Use the actual returned path from that run with `read_candidate_png`; keep its data URL in code mode and use the existing bounded preflight. This 16×9 PNG has valid CRCs and no C2PA. The pinned byte helper produces a 1200×675 JPEG of 92,351 bytes, SHA-256 `974a39a8a8e509df3b2324fab77f42a85738a897e1ee5644244ba5c1ca545b33`. It is only a transport fixture and must never be adopted as article artwork. Offline conversion checks do not prove scheduled-worker tool access, store capacity or end-to-end publication.

After deployment, refresh the installed tool schema and prove one **existing** PNG transfer in the actual scheduled GPT runtime before new generation. A locally passing test or the prepared-JPEG route alone does not prove this path's live availability, response-size limits or free Worker CPU usage. A generator exposed only as a top-level tool uses the verified two-phase protocol above; if no native generator is exposed at all, keep the fallback and record that separate capability gap.

After staging, `const images = await stager.load(plan)` returns verified `sourcePngDataUrl` and `jpegDataUrl`. Pass them directly to `image(images.sourcePngDataUrl)` and `image(images.jpegDataUrl)`; never emit the whole result with `text` or `notify`. Compare both displayed images with the full current article. Only after that review, use the normal publication PR to install the JPEG, its truthful generation record and a current-SHA visual decision. Keep the source candidate retrievable. Existing accepted photos, integer seeds and previous rejections remain unchanged until an actual reviewed replacement is published.

For a compact version 2 checkpoint, follow [the reservation example](#resume-a-reservation) for `native_reservation_steps` or [the save example](#resume-saving-the-original) for `native_stage_steps`. Keep the original plan and the metadata in that example in the existing task handoff. A missing source file before upload completes requires recovering that exact original.

For legacy `stage` checkpoints only, `resume(plan, checkpoint)` works in a fresh runtime once `filesComplete` is true: all original PNG parts, JPEG and receipt have returned blob SHAs. It can then finish tree/commit/branch/readback without original data-URI bytes. Before that boundary, retain the raw native result, real byte-returning callback and first observation time. Pass `error.transferredImage` to the legacy storage method with the original proof/claim when available. If the file must be reread, that legacy method expects an `imageGenResult` object containing the actual PNG data URL. Verify the reread bytes against the receiver’s SHA-256 before passing `{image_url: png.image_url}`. Legacy `store` examples require an executor that actually retains values; current GPT does not retain them across cells. A blob or unreferenced commit is incomplete. Authorization failures, missing files on existing branches, malformed responses and ambiguous lookups never authorize overwriting a branch or generating again.

### Install a reviewed candidate in its existing publication PR

For a new image-manifest slug, load `scripts/generated_manifest_patch.js` from the same reviewed immutable commit as the stager and pass its `GeneratedManifestPatch` API as `manifestPatch` to `NativeImageStage.create`. This path requires a photo-routed article (`cpu-photo`, including the unset default) and an ordinary Native candidate retaining its C2PA and original generation claim; historical recovery and existing-record replacement keep their explicit procedures. After displaying and reviewing the source PNG and final JPEG from the **fixed candidate commit**, prepare the public change:

```js
const prepared = await stager.preparePublication({
  plan,
  candidateCommitSha,
  pullRequestNumber,
  expectedHeadSha,
  articleSha256,
  reviewSource,
});
```

`reviewSource` is the original JSON text of the actual accepted decision for that article fingerprint and JPEG SHA, with the observed review time and notes. The helper does not perform visual review or create an acceptance. It rechecks the fixed candidate's bytes and provenance, the current PR's article and head, and returns `prepared_not_published` with the current PR's `baseTreeSha`, `expectedHeadSha`, and three `files` for GitHub `create_tree`: the public JPEG blob, the manifest append, and the review update. Existing manifest and review text, including large integer seeds, is preserved. An existing manifest slug requires its explicit replacement/history procedure; this append-only path must not overwrite it.

Before any GitHub write, run the existing fresh image guard. Use these three files on the returned **current PR tree**, then follow the existing expected-head non-forced update, readback, CI, and publication-finalize procedure. If the head changes, prepare again against the new head. Do not merge the candidate branch or copy its tree into the publication PR: `source.png.partNNN`, `candidate.jpg`, and `receipt.json` remain retrievable at the original candidate commit. A prepared patch is neither saved nor published, and a successful save is not proof of visible delivery.

If the original article PR has merged, continue its plan and fixed candidate. Check for an existing owner before branching from current `main`. [Photo recovery ownership](VISUAL_OPERATIONS.md#画像の採用条件)

When the reviewer has a new handoff to record in `docs/DAILY_EDITORIAL_BRIEF.md`, that update can open the recovery PR. For the pump article, the reviewer recorded the original PR, article and image hashes, fixed candidate commit, visual decision and next owner. The next commit added the three public files to the same PR for news photo finalization. [Recovery PR #1041](https://github.com/RollNuts/mihassei-news/pull/1041) · [Publication handoff](OPERATIONS.md#ci公開待機の引継ぎ)

## Runtime without Node

Read the helper as UTF-8 from the reviewed repository revision. Load it in a plain JavaScript runtime, including `functions.exec`:

```js
const api = new Function(helperSource + "\nreturn NativeImageBytes;")();
const sources = await api.fetchSources(args =>
  tools.mcp__codex_apps__github_fetch_file(args)
);
const runtime = api.initialize(sources);
const result = runtime.convert(actualImageGenResult.image_url);
```

`SOURCES` fixes the official UPNG, jpeg-js encoder, and js-sha256 repository, path, commit, and UTF-8 source SHA-256. `fetchSources` accepts a caller-supplied connector; initialization checks the pinned digests and a SHA-256 known vector. No dependency source is vendored. Treat the fetched sources as executable code from those fixed upstream revisions. `new Function` is a loader, not an isolation boundary.

The returned fields include `bytes` (JPEG Uint8Array), `base64`, `sha256` (final JPEG), `source_png_sha256` (complete original PNG), dimensions, `quality`, and `receipt`. Keep large base64 strings out of tool text output. Save the original PNG and actual full prompt separately. `functions.exec` functions and stores are transient; reconstruct the runtime from the saved helper and pinned sources when needed.

## Choose the actual return type before generating

Verify a usable byte-transfer route before calling ImageGen. Read the schema of the image tool actually exposed in this run; do not infer its return type from another chat. A `data:` URL captured inside code mode can use this helper without a shell. A file ID or local path additionally requires an available tool that can return the actual bytes to the same runtime as the GitHub connector. Library materialization alone is not that bridge.

For a local-file route, pass a small existing PNG through the real execution tool inside the generating entry point's `readBytes` callback. Do not generate an image merely to test tool availability. If no byte-returning route is exposed, retain the existing article fallback and record the specific missing capability once. Continue text review and inspection of already-accessible candidates. Retry the import when the tool/source availability changes, not every hour under identical conditions. Do not invent tools, use paths owned by another executor, or treat an uncalled materialization operation as a failed download.

The present chat's tools do not establish what a scheduled worker receives. The Automation response fields `environment_selection` and `environments` describe saved selection, not proof of a previous run's capabilities. If environment selection is not an exposed update input, a prompt change does not attach an executor. Keep this limitation distinct from successfully importing an individual photo.

| Actual result available | Import route |
| --- | --- |
| PNG data URL / byte array in the current code-mode call | Pass the actual value to `runtime.convert`, then upload the resulting JPEG bytes in the same execution. |
| PNG or JPEG at an actual local path | Read it in the executor that owns that path. For connector staging, transfer its bytes through an available execution tool, as below; an existing output can also use owner-export recovery. |
| Real conversation/Library file ID and filename | Follow the current Library skill and the exposed materialization/transfer tools to retrieve that exact file, then verify its recorded SHA and dimensions. |
| A path in a previous run that is absent here | Try its recorded file ID through the exact-ID materialization route. A missing local path or an empty Library search does not establish that the source is unavailable. |

A file ID is an input to the supported file service, not a filesystem path or a URL to construct. In environments exposing Library `prepare_materialize`, supply the actual file ID and filename according to its current schema, then use the official transfer helper. Do not save expiring download URLs in the repository. If retrieval fails, record the exact failed operation and the real source reference; do not report a search miss as a failed download. In particular, `library_read` returning "not visible in the requested files surface" is a read-surface failure, not the result of a `prepare_materialize` call. Record which operation was actually attempted before diagnosing the execution environment.

For an unresolved file ID, discover the current Library app's `prepare_materialize` through the available tool registry/search before declaring that route unavailable. A failure from a different `files` materialization tool does not establish that the Library app route failed. Follow its current skill and schema, use the exact file ID, and finish the returned transfer before deciding whether bytes are recoverable.

Keep three recovery states distinct: a saved candidate branch with a receipt, an unsaved output known only by file ID/local path, and a confirmed missing retrieval/transfer capability. Only the first can be loaded with its original plan. A missing historical plan or logical run ID does not prevent exact-file recovery. After recovering bytes, create a stable recovery identity from the real file ID, preserve the source article and recovery evidence, and state that the original generation logical run ID or exact prompt was not retained when that is the case. Do not present the recovery identity as the original generation identity.

### Owner-export recovery for an existing output

Materializing and reading a PNG in the generating chat's executor can succeed even when that executor cannot pass bytes to the GitHub connector. A file ID can also be invisible to another chat. Record these as separate boundaries; do not retry the same inaccessible ID in other chats or regenerate to repair delivery. The owner can use its ordinary file-download facility to export the original bytes, without new credentials, an upload endpoint or base64 in conversation text.

For this handoff, export a ZIP containing only `original.png` and `transport-receipt.json`. The receipt records `transport_only: true`, the actual PNG SHA-256, byte count and dimensions, the real source reference, the materialization/export method and actual export time. Preserve the originating plan, article SHA and prompt separately in the existing handoff when known; do not invent missing article linkage, seed, generation time, job ID or approval. The receiving owner checks the cloud/storage guards, archive SHA and bounded contents before extraction, rejects duplicate/unsafe paths and symlinks, then verifies the PNG against the receipt and retained source evidence. Continue the same article/PR through the ordinary conversion, visual review and publication checks.

This is recovery of an already-generated file, not a new-generation route or proof that a scheduled worker can save directly to GitHub. It does not bypass the generation entry points' preflight/reservation requirements. A transport-only image with no established article provenance remains diagnostic material and must not be installed as an article candidate.

If the exact `file_id` is known but the original filename was not retained, inspect the currently exposed `prepare_materialize` schema. When it defines `file_name` as a fallback local basename, pass the exact ID with a descriptive fallback such as `source.png`; an unknown original filename alone does not block that retrieval route. This chosen basename is not evidence of the original tool filename. Verify the materialized bytes, media format and recorded SHA before adoption, and retain the actual materialization result or error rather than substituting a `library_read` visibility result.

The helper is one conversion route, not a requirement to discard a valid JPEG produced by Pillow or another existing local encoder. A local conversion can be adopted when the actual original PNG, final 1200×675 JPEG, full prompt, source/final SHA, transformation settings and visual review satisfy `VISUAL_OPERATIONS.md`. Record the encoder actually used. A reference preview is not accepted merely because it exists, but it need not be regenerated or re-encoded solely to use this JavaScript helper.

## Transfer a local JPEG without printing its bytes

When `exec_command` and the GitHub blob connector are exposed to the same code-mode runtime, capture the execution result directly. This avoids copying large base64 strings through model text. Use only observed local paths; the execution tool must be able to read both files.

The following assumes `api` and `runtime` from the previous example, and actual absolute `originalPngPath` / `finalJpegPath` values:

```js
const shellQuote = value => "'" + value.replace(/'/g, "'\\''") + "'";
const readCommand = "python3 - " + shellQuote(originalPngPath) + " " +
  shellQuote(finalJpegPath) + " <<'PY'\n" +
  "import base64, hashlib, json, sys\n" +
  "from pathlib import Path\n" +
  "from PIL import Image\n" +
  "p, j = map(Path, sys.argv[1:])\n" +
  "source, jpeg = p.read_bytes(), j.read_bytes()\n" +
  "with Image.open(p) as im:\n" +
  "    assert im.format == 'PNG'; source_size = list(im.size); im.verify()\n" +
  "with Image.open(j) as im:\n" +
  "    assert im.format == 'JPEG' and im.size == (1200, 675); im.verify()\n" +
  "print(json.dumps({'base64': base64.b64encode(jpeg).decode('ascii'),\n" +
  " 'bytes': len(jpeg), 'sha256': hashlib.sha256(jpeg).hexdigest(),\n" +
  " 'source_png_sha256': hashlib.sha256(source).hexdigest(),\n" +
  " 'source_size': source_size}))\nPY";
const read = await tools.exec_command({
  cmd: readCommand, max_output_tokens: 200000
});
if (read.exit_code !== 0 || read.session_id)
  throw Error("Local image read did not complete");
const payload = JSON.parse(read.output); // Truncation or non-JSON is an error.
const jpeg = api.fromBase64(payload.base64);
if (jpeg.length !== payload.bytes || runtime.sha256(jpeg) !== payload.sha256)
  throw Error("Local-to-connector byte transfer mismatch");
// Also compare source_png_sha256 and source_size with any prior receipt.
const blob = await tools.mcp__codex_apps__github_create_blob({
  repository_full_name: "RollNuts/mihassei-news",
  encoding: "base64", content: payload.base64
});
```

Do not emit `read.output`, `payload`, the raw tool result, or base64 with `text` / `notify`. Print only small metadata. Check the connector's returned result and attach its blob to the article's working branch with the existing Git tree/commit/PR procedure. An unattached blob, a transient store, or a local path alone is not a durable handoff and is not publication.

Save the real JPEG and its generation metadata, including the exact prompt sent to the tool, to the working branch before ending the generating run. Visual acceptance remains a separate decision bound to the current article and image SHA; a saved candidate must not be labelled accepted before inspection. If the original PNG is also needed across runs, retain a retrievable original-file reference and its SHA. On retry, recover this same saved candidate first; a new invocation with the same logical run ID does not authorize another generation. When recovering a historical asset whose tool prompt was not retained, preserve the recorded scene instructions with their actual source and state that the exact tool input is unavailable; never relabel those instructions as an observed verbatim prompt.

## ChatGPT fileParams: prepared JPEG candidate only

The separate `/api/image-candidate/mcp` endpoint can receive an already prepared 1200×675 JPEG up to 700 KiB. It does not generate, convert, retain an original PNG, approve a photograph or publish it. Existing native-image provenance and current-article visual-review rules remain required. A successful transfer is not a replacement for the original PNG/C2PA record.

After deployment, refresh the existing plugin's tool definitions: version-2 staging requires `claim.version` and no longer accepts `claim.file_id_sha256`. Old cached claim schemas fail closed rather than being reinterpreted.

Use `probe_candidate_jpeg` with the absolute file path available in the **current** execution. The adapter supplies the documented file object; do not pass a raw file ID, a fabricated download URL or another worker's local path. A verified result includes the actual JPEG SHA/bytes and an `upload_ticket`: version 2, a cryptographically random 32-byte nonce, its SHA-256, a 12-character branch suffix and a 15-minute expiry. The nonce is intentionally returned privately to the caller; never copy the whole ticket into GitHub, public output, logs or a receipt.

After the owner enables storage, an existing GitHub writer creates the following public claim on a new branch from the reviewed working commit. It uses `native-image-candidate/<slug>/<article SHA first 16>-<ticket.branch_suffix>` and writes only `maintenance/image-upload-claims/<slug>/claim.json`:

```json
{
  "version": 2,
  "repository": "RollNuts/mihassei-news",
  "slug": "<actual slug>",
  "article_sha256": "<actual article fingerprint>",
  "branch": "<the exact candidate branch>",
  "expected_head_sha": "<the reviewed working commit>",
  "path": "maintenance/native-image-candidates/<slug>/candidate.jpg",
  "jpeg_sha256": "<probe sha256>",
  "jpeg_bytes": 0,
  "nonce_sha256": "<ticket nonce_sha256>",
  "expires_at": "<ticket expires_at>"
}
```

Replace every placeholder and `jpeg_bytes` with the observed values. The public claim contains the nonce **hash only**. Pass those claim fields except `repository`, plus the create-file result's `claim_commit_sha` and the private ticket `nonce`, to `stage_candidate_jpeg` with the actual JPEG file path. Version-1 claims are rejected; a new version does not relabel an old candidate or its provenance. File IDs may change on upload, so authorization binds the exact JPEG SHA and size. The actual first transport ID hash is retained as receipt metadata.

The server checks the live owner-written branch claim without credentials before any token-bearing GitHub request or file download. It then checks the immutable claim, one-parent working-commit ancestry, the canonical article fingerprint in that exact working commit, the fixed public repository, live image-workflow state and the current main generated-JPEG inventory. New articles and reviewed drafts need not already be on main. Later adoption must still match the **current article SHA** and undergo the ordinary visual review; this endpoint never updates main, generation fingerprints, seeds or acceptance records.

JPEG and `upload-receipt.json` become visible together in one non-forced candidate-branch update. The receipt says `awaiting_visual_review`, records the truthful transport facts and contains no nonce, download URL or raw file ID. A retry uses the same claim and can reuse only the same JPEG plus matching receipt and ancestry, preserving the first receipt even if the transient file ID changed. Stops, expiry, archive limits and concurrent changes are rechecked before the branch update. Missing, conflicting or partial records stop recovery; they are not permission to overwrite or regenerate. After expiry, inspect the recorded GitHub branch/commit with the existing reader rather than issuing another claim to replace it.

Storage is **OFF** unless the owner separately approves a new repository-limited credential, stores it only as Worker Secret `IMAGE_CANDIDATE_GITHUB_WRITE_TOKEN`, and explicitly sets `IMAGE_CANDIDATE_WRITE_ENABLED=true`. The selected repository is only `RollNuts/mihassei-news`, with Contents read/write and mandatory Metadata read, a finite owner-approved expiry (90 days is the proposed maximum), and no Actions write, Workflows write or Administration permission. The Auth None connection uses the per-upload claim capability; it requires no global caller key. No credential is issued or enabled by deploying this code. Mock test results do not prove live storage, deployment CPU usage or later publication; test those separately after authorization without generating another image.

## Provenance output

`receipt` is null when there is no C2PA chunk. When present, extraction follows PNG caBX → C2PA manifest store → final manifest → its direct claim → claim `instanceID`. It never substitutes an ingredient ID, the manifest URI, a guessed filename, or a newly made UUID. Missing or ambiguous current identifiers and malformed structures raise errors.

The actual `xmp:iid:` value is preserved in `generation_receipt`; its existing UUID portion is returned as `generation_id`. This identifies an asset instance, not an exposed backend generation/job ID. `source_c2pa` preserves the manifest URI, generator metadata, and actions with timestamp precision. `caBX_bytes` preserves the original metadata payload. The helper marks signature and content-binding validation as `not_performed`; parsing is not cryptographic validation. The delivered JPEG does not acquire the original PNG's signature.

The calling importer must still record the actual prompt, actual generation/review times, current article fingerprint, final JPEG byte length, and a real visual-review decision, and preserve prior records without rounding their integers. For historical recoveries, follow the explicit missing-prompt procedure above. See the existing publication rules in `VISUAL_OPERATIONS.md`.

For a **new slug**, load `scripts/generated_manifest_patch.js` from the same reviewed immutable commit and append to the latest manifest's original UTF-8 text:

```js
const patch = new Function(helperSource + "\nreturn GeneratedManifestPatch;")();
const updatedManifestSource = patch.appendImage({ manifestSource, slug, imageSource });
```

`imageSource` is the new record's JSON text. The helper retains both inputs' original numeric literals, rejects duplicate members and existing slugs, and changes only the insertion inside top-level `images`. It does not save, validate photo provenance, accept or publish; use the existing guarded branch/PR procedure. Never parse and stringify the entire manifest with JavaScript `Number`: existing 64-bit seeds can be rounded. Python's standard `json` integer handling is also suitable. An existing slug needs its explicit recovery/history procedure, not this append-only helper.

## Regression fixtures

`scripts/native_image_bytes_test.js` runs without Node when provided the same API and pinned source map:

```js
const runTests = new Function(testSource + "\nreturn NativeImageBytesTests;")();
const report = runTests(api, sources);
```

The fixtures test a known PNG-to-JPEG byte digest, original PNG SHA, UTF-8/base64, direct current-claim selection despite old/ingredient decoys, exact timestamp preservation, absent identifiers, duplicate CBOR keys, broken box bounds, PNG CRC corruption, and dependency digest changes. C2PA fixtures are explicitly unsigned synthetic parser data. They do not assert provider provenance.

For an existing Node environment, both files also expose CommonJS exports. Node is optional and the helper itself does not use `Buffer`, `require`, filesystem, networking, `crypto`, `TextEncoder`, `atob`, or `btoa`.

The separate staging fixtures run in the same plain JavaScript runtime:

```js
const runStageTests = new Function(stageTestSource + "\nreturn NativeImageStageTests;")();
const report = await runStageTests(stageAPI, runtime, {sources, byteHelperSource, stageHelperSource});
```

They use an in-memory GitHub fake and an explicitly synthetic PNG, with no real image generation or publication. They cover exact byte readback, a source larger than 1 MiB, reuse without writes, branch/readback interruption, checkpoint recovery, ambiguous lookup failures, article binding and exact integer seeds. The generating entry point also checks zero generator calls after acquisition/save/readback failures, a standalone transport diagnostic without claims, distinct probe/reservation/candidate receipts, repeated real checks on the fixed branch, recovery of candidates saved during the preflight, and at most one generator call across independent workers and fresh-runtime retries. Lost or malformed claim responses cannot authorize generation, and lookup failures retain the reservation reference. A live transport check must use the actual worker's callbacks; successful current-chat transfer alone does not prove a scheduled worker received the same tools.





<a id="weekly-original-transport"></a>
### Test the current producer’s original PNG route

Choose the route for the generator actually available in this execution. A file-returning generator must use its real `readCandidatePng` reader.

- With top-level Python, run the [138-byte offline bootstrap](#offline-png-bootstrap) there. Use the returned path and the reviewed main commit SHA as shown below. Only the path crosses tools.
- With a writer callable from code mode, bind `writeProbeFile(base64)` to decode the helper’s GitHub fixture into a new temporary PNG in the generator’s workspace and return the path. Pass `{route:"file", writeProbeFile}`. If that favicon fixture already has a handle, pass `{route:"file", file}`.
- Use `{route:"inline_png"}` without a file reader only when the generator actually returns the original PNG data URL.

The file route must reopen the exact fixture. Record the failed operation before reserving an image. A `read_candidate_png` failure with `BLOCKED_FILE_REFERENCE` requires a changed, authorized file reference. For an existing original, follow [Library recovery](#blocked-photo-file-reference). After the reader returns bytes, a `verify_png` failure identifies a byte or metadata mismatch. For `cloud_status`, `create_blob` or `fetch_file_readback` failures, keep the same verified fixture and address the indicated stop or GitHub operation.

Load the original handoff, native byte and staging helpers at the reviewed main commit. The verification runtime loads only SHA-256. Keep the byte-bearing tool results inside code mode.

```js
const runtime = await bytesAPI.verificationRuntime(github.fetch_file);
const {cloudStatus} = stageAPI.create({controlOnly:true, github, repository:"RollNuts/mihassei-news", readCloudWorkflow});
// Top-level Python has created the offline fixture and returned its path.
const handoff = H.create({runtime, github, cloudStatus, readCandidatePng});
text(await handoff.checkOriginalTransport({route:"file", file:probeSource, offlineProbeCommit:reviewedMainCommit}));
```

This writes the chosen fixture’s verified public bytes through the chosen route and re-reads their existing immutable GitHub path, checking the acknowledged blob ID and SHA-256. Remove only your temporary probe after completing its checks. It creates no branch, image, or reservation. A successful probe verifies **downstream transport only**. With the matching generator and return format available, continue in this run with the [normal reservation](#weekly-photo-quota). After a fresh grant, generate one original, checkpoint and re-read its full SHA before the next. On failure retain the operation and safe connector error, or name the missing generator/reader and observed result type. Keep a pending original on its own reservation.

<a id="weekly-photo-quota"></a>
### Reserve one original in the producer’s weekly allowance

Use this only when the [stock rule](EDITORIAL_LOOP.md#写真ストックと制作量) calls for a new original, after recovering outstanding files and verifying that the current execution can save and read back an actual original through its available transport. Previously received originals can go straight to the existing handoff. Retain the reviewed main commit for the run alongside the checkpoint. In each fresh cell, use GitHub `fetch_file` at that commit to load just `scripts/photo_generation_quota.js` and `scripts/native_image_stage.js`. Bind `fetch`, `fetch_file`, `create_branch`, `create_file` and `update_file` from the existing GitHub connection and the live `read_image_control` callback. The two imports and one quota step fit within 16 calls, including when main advances.

```js
const Q = new Function(quotaSource + "\nreturn PhotoGenerationQuota;")();
const stage = new Function(stageSource + "\nreturn NativeImageStage;")();
const {cloudStatus} = stage.create({controlOnly:true, github, repository:"RollNuts/mihassei-news",
  readCloudWorkflow: ({nonce}) => readImageControl({nonce})});
const quota = Q.create({github, cloudStatus});
// Reuse the same input and checkpoint across cells. Keep helper source and GitHub responses private.
const next = await quota.reserveStep(input, checkpoint);
```

`input` identifies the registered `model_id`, `source_chat_url`, actual `run_id`, its offset-qualified `run_started_at`, and the stable `attempt_id` for one planned original. For `pending`, pass `next.checkpoint` to the next cell with the unchanged input; repeat only on a returned next phase. After an ambiguous write, look up that same attempt again. Preserve its ID across task retries. Only a fresh `may_generate === true` permits one call producing one original. Record an observed result with `finishStep` on the same week, run and attempt, using `week_start` from the reservation, `status` (`succeeded`, `failed` or `interrupted`) and `result_ref` pointing to the actual receipt or observed outcome. A reserved attempt with no confirmed result remains charged. Preserve existing original and checkpoint references for recovery.

Each producer has one `photo-issue-plan/stock-<model_id>-<JST Sunday YYYY-MM-DD>` reservation branch per week. Keep that branch and its ledger intact for the week. If either is known to be lost, recover its original Git history before granting any further attempts. Keep the receipt and images for each shoot on its own existing candidate branch. Starting with the first eligible full week (2026-11-29 JST), the helper checks that the common policy had already reached main before that Sunday. During transition, initialize only with a reconciled baseline covering the actual earlier calls and parallel candidates. Pass `baseline = {mode:"reconciled", model_id, week_start, coverage_ref, prior_attempts}`. The producer must inspect `coverage_ref` for the full interval and parallel attempts. Each known prior attempt has `run_id`, `attempt_id`, `units` and `evidence_ref`; failed and interrupted attempts count. Even an empty `prior_attempts` needs evidence of complete coverage. A missing reservation file, an empty image inventory or an unknown chat history cannot establish a zero baseline. Pass only the compact outcome to the task log; keep the checkpoint in code mode.

<a id="photo-original-handoff"></a>
### Existing weekly photo originals

The producer can deliver existing PNGs from its own chat to the editor without a ZIP. Use the existing issue slug, producer chat URL, observed delivery turn and each source image's original ordinal and measured SHA-256. Retain the actual generation turn if known. Inspect the existing `photo-issue-plan/<issue>` branch first. Reuse any matching raw files, including older full-PNG, JPEG or binary-map handoffs. Never upload the same originals again to add a receipt in a new format.

Load `scripts/photo_original_handoff.js` from the same pinned main as the native byte and staging helpers above. Use the initialized byte runtime, ordinary GitHub callbacks and existing live cloud guard. Choose the input route that matches the actual original returned in this execution; a file adapter is required only for a local file. Keep image bytes and base64 inside code mode. Check the 12 GiB free-space floor wherever files are materialized. Candidate writes check the public workflow and the main JPEG total each time. This is transport of an existing original, not permission for another generation; stock replenishment and attempt reservations follow [EDITORIAL_LOOP](EDITORIAL_LOOP.md#写真ストックと制作量).

If a complete weekly original already exists on the pinned candidate commit, reuse it. When GitHub Contents returns just the blob ID for a large numbered original or supplement, call the existing receiver's [read_weekly_candidate_png](IMAGE_CANDIDATE_RECEIVER.md#reading-a-saved-weekly-original) with its issue, commit, path and expected SHA-256. Decode its structured PNG data URL inside code mode. Keep the ID from that exact GitHub path for the release tree. The source stays on its candidate commit and the editor receives its bytes without uploading another copy.

When ImageGen actually returns a PNG data URL to code mode, pass that unchanged value directly. The following `imagegenResult` is the real tool result, retained privately in code mode; it is not a made-up URL or a conversation preview. No local file or `readCandidatePng` adapter is needed.

```js
const H = new Function(source + "\nreturn PhotoOriginalHandoff;")();
const original = H.create({runtime, github, cloudStatus:stager.cloudStatus});
const originalDataUrl = imagegenResult.image_url;
if (typeof originalDataUrl !== "string" || !originalDataUrl.startsWith("data:image/png;base64,"))
  throw Error("Original PNG data URL was not returned");
const measuredSha = runtime.sha256(runtime.fromBase64(originalDataUrl));
// Start at 0; retain this exact original and measured SHA for each continuation.
const slice = await original.uploadDataUrl(originalDataUrl, measuredSha, start);
const asset = {...slice, parts:[...earlierParts, ...slice.parts], source_ordinal:originalOrdinal,
  generation_turn:knownGenerationTurn || null};
text({asset, next:slice.next}); // compact metadata only, never the tool result or data URL
```

The direct route and file route share the 8 MiB bound, full SHA comparison, PNG CRC and dimension checks, and guarded 512 KiB part upload. Retain the original data URL privately until all its parts are saved; `text` / `notify` must not receive it. If no actual original data URL was returned, recover the observed original file through the existing route rather than fabricating PNG bytes, a file path or a source reference. The producer chat, delivery turn and known generation turn still describe the real source; direct byte delivery does not permit relabelling another chat's output as the registered producer's work.

When the actual return is a local PNG file, use the existing file adapter with that observed path instead:

```js
const H = new Function(source + "\nreturn PhotoOriginalHandoff;")();
const original = H.create({runtime, github, cloudStatus:stager.cloudStatus, readCandidatePng});
// Per file, across cells. Start at 0. Each call writes one 512 KiB blob.
// Pass the measured SHA and the same real file path on each continuation.
const slice = await original.upload(file, measuredSha, start);
const asset = {...slice, parts:[...earlierParts, ...slice.parts], source_ordinal:originalOrdinal,
  generation_turn:knownGenerationTurn || null};
text({asset, next:slice.next}); // retain only this compact metadata
```

<a id="blocked-photo-file-reference"></a>
#### Recover a blocked original-file reference

A `BLOCKED_FILE_REFERENCE` response describes the attempted transport reference. It does not prove that the original is missing. A Library ID, a prior executor's path and a file materialized in the current execution are different inputs. Do not retry an unchanged rejected ID/path, manufacture a download URL, change the receiver's allowlist or regenerate the photograph to work around it.

**Record the individual sources before the first transfer.** Reuse this producer's existing original-inventory JSON, preserving earlier records and any completed receipt. For each actually observed original retain its filename, observed local path, real Library ID and version/file ID when available, measured SHA-256, byte count and dimensions, actual observation chat/run/time, model ID and the evidence linking that file to the producer, and any known generation turn. Unknown values remain `null` / `unknown`; the observation chat is not evidence of the generation chat. Names, folder membership, close timestamps or resemblance alone cannot establish model/source identity. Do not substitute an inventory row number for an unknown original ordinal.

Persist and read back the inventory through its existing authorized Library location before attempting the transfer; retain that inventory's real Library ID and measured SHA in the ordinary task result. If no inventory exists, save one for this unresolved handoff without replacing a completed receipt. A candidate-backed inventory may instead remain in the existing handoff at a sibling path outside `originals/` and `originals-pending/`, using the same expected-head and fresh-guard write procedure. Do not create a separate cross-project ledger. The helper drops arbitrary source-reference fields from its normalized packet, and `originals-pending/` permits only its checkpoint and recorded parts, so neither adding an arbitrary packet field nor putting another JSON inside that directory preserves a retrievable inventory.

Recover the exact original through the supported Library path:

1. Follow the available Library skill and materialize the actual authorized Library item or retained native file reference into the **current** workspace. To pin a known Library version, use the `file_id` and `file_name` returned together by the same Library read/list/search, with its `library_file_id` when present. A version number is evidence to retain, not a materialize selector. If only the stable Library ID is available, resolve it normally and require the resulting bytes to match the already observed source SHA. An actual native `observed_file_id` and filename recorded together in the producer's fixed source inventory are also a resolved reference: pass those exact values as `file_id` and `file_name` to `prepare_materialize`, following the Library skill's resolved-reference route with the inventory's expected SHA. The provider's normal file-access check must succeed; an inventory entry does not grant access. Include a real Library ID when known or required by its storage provider; otherwise retain it as unknown rather than inventing one or uploading a duplicate solely for transport. If the producer's reference is unavailable to the editor, stop using that reference. Look once in the editor’s own visible Library for the inventory filename or a separately recorded export filename. Use a match only through its own authorized Library reference and only if its bytes, SHA-256 and dimensions match the producer inventory in step 3. If no verified match is accessible, leave the item pending. Never guess IDs, paths or URLs. Pass the resulting current local file to the PNG reader.
2. Use the returned `workspace_path`, or complete the returned authorized signed transfer and apply its extended attributes as required by the Library tool. Preserve the 12 GiB free-space floor. Do not expose signed URLs, image bytes or base64 in conversation or handoff records.
3. Read the actual current local file and compare its full SHA-256, byte count and dimensions with the source inventory. A changed or unreadable original stops this item; it is not a new source under the old identity.
4. Pass **that current local path** to `read_candidate_png({file: actualPath})`. Compare the returned metadata and full source SHA again, keep its actual PNG data URL private in code mode, and continue the existing `uploadDataUrl` or file `upload`, checkpoint and immutable readback steps. Preserve the original Library/source references and candidate; the transfer path does not change provenance.
5. If the original exists locally in the producer's current execution but has no retained Library reference, save that exact existing file once through the authorized Library tool and append its returned real ID to the inventory. Do not replace another item, recreate missing bytes or treat a local path as a durable reference.

Keep a failure's error class, exact item identity, stage and next unperformed supported operation in that same inventory. If inventory persistence itself fails, retain the compact per-item references and observed hashes in the existing task result, together with that save error; a count such as “8 PNGs found” is insufficient for another execution to recover them. Retry only a changed/new actual reference or a previously unperformed supported operation. A successful transfer in one execution does not establish another scheduled worker's capabilities.

An inventory is `sources_observed_not_received`, not an original-byte receipt, visual acceptance or usable stock. Library materialization and PNG reading also do not establish GitHub receipt. Continue the unchanged full original save, remote SHA readback, producer matching, duplicate and visual checks before increasing stock or permitting the next generation.

#### Durable progress before the next original

An upload reply contains acknowledged Git blob references, not a branch-backed original. Save transfer progress on the same `photo-issue-plan/<issue>` branch before moving on to another generated original. `saveTransferStep` with `kind:"checkpoint"` keeps the actual part blobs reachable through `maintenance/photo-issue-plans/<issue>/originals-pending/`, together with `checkpoint.json`. The completed `originals/receipt.json` remains protected. Existing `generation-attempts.json` and other sibling files are preserved; these methods do not reserve generation or determine the remaining generation budget.

Keep one packet's issue, producer, source chat and delivery turn fixed. Include the earlier assets in their original order, append acknowledged parts as a contiguous prefix, and append new assets without changing prior metadata or parts. For an upload interrupted after one of two requested parts, `error.partial` contains only the acknowledged parts from that call, the original SHA/dimensions/byte count and the next offset. Merge it with the earlier parts of that same original; it contains no image bytes. A lost blob acknowledgement is not an acknowledged part. Retain the real source file or private data URL while any original part remains missing, and recover that same original instead of generating a replacement.

Use the `weekly-original-transport` verification runtime. In a fresh scheduled cell, load its three helpers and one pinned SHA dependency; restore any missing packet in a separate read-only cell. Keep the same packet, operation and **starting** candidate head on every continuation. Use `null` only for a genuinely new branch.

```js
// Store the packet, kind and starting lease before the first step, or pass their same values across cells.
const options = {kind:"checkpoint", expectedHead:startingCandidateHeadOrNull};
const key = "photo-transfer:" + packet.issue + ":" + options.kind;
const saved = await original.saveTransferStep(packet, options, load(key) ?? null);
store(key, saved.checkpoint ?? null);
text(saved); // only compact Git references, phase and record hash
```

Run one Step per cell until `checkpoint_saved_pending_readback` (checkpoint) or `staged_pending_readback` (receipt). `transfer_pending` requires the next cell with the returned checkpoint. Each change gets a fresh stop/capacity check. The commit and ref steps independently verify the entire tree and registered producer; an existing candidate uses its exact starting head. When a call throws, retain the current step and record its phase and safe error message or connector code explicitly. When a ref acknowledgement is lost, replay the same publish step. Recovery at a newer branch head is a separate read that verifies the exact record and part IDs. A conflicting change stops this operation. If the continuation was lost after a successful save, start with the current candidate head and the identical packet to recover the saved record without writing it again.

After interruption, reconstruct the packet from the exact saved commit and checkpoint hash. `restoreCheckpoint` checks metadata and the part-to-tree blob bindings, and reports whether the expected number of part references is present. Its `checkpoint_metadata_only` status and `image_bytes_verified: false` do **not** verify original PNG bytes, visual quality, acceptance or stock availability. A partial original still requires its exact remaining bytes.

```js
const restored = await original.restoreCheckpoint({issue, ...checkpoint});
text({status:restored.status, commit:restored.commit,
  checkpoint_sha256:restored.checkpoint_sha256, assets:restored.assets});
store("photo-original-packet:" + issue, restored.packet); // private metadata, never print the full packet
// Use checkpoint.commit as the expected head. If private state is lost, restore it in its own cell.
```

Read back each complete original from its pending part paths before any next generation. Prefer `readCheckpointOriginalStep`, one call per cell, to read at most eight parts at a time. Each call rechecks the fixed checkpoint and tree bindings. The private continuation is bound to the method kind, immutable commit, record hash, source SHA and exact next offset; resumed byte chunks are checked against that same receipt again. Only completion returns `original_verified` with the actual PNG bytes after all part hashes/counts, full SHA, PNG CRC and dimensions pass. An incomplete original cannot enter this readback. Visual review remains separate. Read-only restoration and byte recovery remain available while writes are stopped. A successful readback satisfies the storage prerequisite only; the central stock policy, actual generation reservation and all other generation limits still apply.

```js
const ref = {issue, ...checkpoint, source_sha256:asset.source_sha256};
const stateKey = "pending-photo-readback:" + ref.commit + ":" + ref.source_sha256;
const result = await original.readCheckpointOriginalStep(ref, load(stateKey) ?? null);
if (result.status === "readback_pending") {
  store(stateKey, result.state); // private serializable byte state, never text/notify
  text({status:result.status, next:result.next, image_bytes_verified:false});
} else {
  store(stateKey, null);
  // result.bytes contains the verified actual PNG; keep it private for review/conversion.
  text({status:result.status, commit:result.commit,
    verified_original:result.source_sha256, bytes:result.bytes.length});
}
```

The continuation contains base64 bytes: never print the result or `result.state`, put it in a task result, or pass it to `text` / `notify`. Use the actual `functions` private `store` / `load` when this executor provides them; do not assume a different runtime retains values between cells. If private state is lost, restart the read-only Step at the same immutable reference. This rereads the saved original and does not generate another image. `readback_pending` is not verified bytes or usable stock. The pending Step uses at most 11 Git callbacks, and the completed-receipt Step below uses at most 9, excluding helper initialization; four verification-runtime source loads fit in the same cell, while remote packet restoration belongs in a separate cell. Pass the normalized packet between transfer cells through private store/load so restoring it does not add Git callbacks to a write phase. The legacy `readCheckpointOriginal` / `readOriginal` macros remain compatible, but a full 8 MiB source can need 19 / 17 callbacks in one call, so use the Step route for larger originals or tighter callback limits.

After all originals are complete, use `saveTransferStep` as above with `{kind:"receipt", expectedHead:checkpoint.commit}` and a fresh continuation. The full normalized packet must match the latest checkpoint. The completed receipt reuses the same part blob IDs. Store its returned compact result as `staged`, then read every original through `readOriginalStep`. Original-byte verification remains separate from visual acceptance and usable-stock counting.

Existing handoffs without a pending checkpoint can finalize the same way using their confirmed candidate head or `null` for a genuinely new branch. Recover an already completed receipt before preparing new work. The legacy `prepareCheckpoint` / `publishCheckpoint` and `prepare` / `publish` methods remain available to executors with a higher callback limit.

Then use one readback Step per cell for each original:

```js
// staged is the saved receipt result from the preceding transfer.
const ref = {issue, ...staged, source_sha256:asset.source_sha256};
const stateKey = "completed-photo-readback:" + ref.commit + ":" + ref.source_sha256;
const result = await original.readOriginalStep(ref, load(stateKey) ?? null);
if (result.status === "readback_pending") {
  store(stateKey, result.state); // private bytes, never print the state/result
  text({status:result.status, next:result.next, image_bytes_verified:false});
} else {
  store(stateKey, null);
  text({status:result.status, commit:staged.commit, receipt_sha256:staged.receipt_sha256,
    verified_original:result.source_sha256, bytes:result.bytes.length});
}
```

A returned `staged_pending_readback` is an unreviewed candidate. Use the full conversion runtime to encode a review JPEG from the first available source.

- If the receipt’s numbered original is already saved, run `await original.uploadReviewJpeg(ref, {numberedCommit})` with its immutable commit and the weekly PNG reader. This re-reads and verifies the whole original at the receipt ordinal, up to 8 MiB, without carrying bytes between cells.
- For a split-only original of up to four parts (2 MiB), `await original.uploadReviewJpeg(ref)` fits in one fresh cell, including six helper/dependency reads and the live guard.
- For a longer split-only original, use `readOriginalStep(ref, state, 3)` and retain its partial bytes privately. In the cell that finishes reconstruction, pass the verified result directly to the uploader. The three-part limit leaves room for the receipt read, conversion and guarded write, including cold imports.

```js
if (result.status === "original_verified") {
  const prepared = await original.uploadReviewJpeg(ref, result);
  text(prepared);
}
```

Reuse a JPEG already listed for this source in the existing candidate handoff, including earlier paths and encoders. For a newly prepared blob, load the verification runtime (three helpers plus SHA-256) for each metadata step. Run one step per cell using the same small `ref`, `prepared` and returned plan. `expectedHead` is the observed head of the existing `photo-issue-plan/<issue>` branch.

```js
const plan = await original.prepareReviewJpeg(ref, prepared, expectedHead); // retain base and commit
```
```js
const staged = await original.publishReviewJpeg(ref, prepared, plan);     // retain commit and path
```
```js
const verified = await original.readReviewJpeg(ref, prepared, staged.commit);
image("data:image/jpeg;base64," + runtime.toBase64(verified.bytes));
```

On an interrupted ref update, recover with the same plan. The immutable JPEG path, SHA and source receipt are the editor's handoff. Only count the cut after its committed bytes have been read and inspected alongside the source. A blob or staging receipt alone is not acceptance. Use [the weekly editorial process](EDITORIAL_LOOP.md#成人の軽い未来企画既存枠) for the column, formal selection and publication.

#### Prepare a saved ten-cut selection

For a numbered PNG/JPEG handoff with `editorial_review.status: "selected_pending_release_receipts"`, continue the existing editor's selection. Use the issue's normal `photo-issue/issue-NNN` release branch (create it from the latest main if absent), its current head and the frozen draft. Bind `readWeeklyPng` to the weekly PNG reader. For an already prepared PNG/JPEG pair, load the same three helpers and use `const runtime = await bytesAPI.verificationRuntime(fetchFile)` with GitHub’s `fetch_file` callback. This loads only the pinned SHA-256 dependency. Pass `controlOnly: true` to `stageAPI.create({runtime, github, repository, readCloudWorkflow, controlOnly:true})` and bind its `cloudStatus` to the original handoff helper. The producer uses the full conversion runtime when encoding a JPEG. Keep one measured import time. **Run each numbered operation in its own cell.**

1. `text((await original.openReviewedSelection({issue, selectionCommit, releaseCommit, importedAt})).plan)` returns a small plan. It checks the saved notes, ten selections, original Git IDs, registered producer and both catalogs. Preserve that plan and the two branch identities. Set `ref = {slug: issue.slug, edition: plan.edition, selectionCommit}`.
2. `text(await original.startReviewedPacket(plan))` creates `maintenance/photo-issue-plans/<slug>/release-packet.json` on the release branch. After a lost create response, repeat this call to recover the commit and first missing cut. For an existing packet it verifies the stored receipt hashes against the saved selection and returns `next: null` when all cuts are prepared. If the helper requests a fresh preflight, repeat step 1 at the current heads using the original import time. A conflicting packet needs reconciliation.
3. `text(await original.continueReviewedPacket(ref))` checks one selected PNG/JPEG pair against the immutable handoff, prepares its formal receipt and saves its SHA-256 to that packet with the prior file SHA. Repeat in fresh cells until `next` is null. After a timeout, use this same call: it reads the committed packet and finds the first missing cut. With the verification runtime the cold cell uses four setup reads, four material reads, five live guard callbacks and one GitHub update. An overlapping editor cannot overwrite another packet revision.
4. At the current release head, repeat step 1 for a fresh plan. Set `ref.commit` to this release head. In the next cell, `text(await original.prepareReviewedTree(ref, freshPlan))` builds the release tree with the original and JPEG blob IDs and formal receipts. It removes only this feature's temporary packet from the proposed tree. Retain the returned base commit and tree SHA. The branch still contains its recoverable packet.
5. For the catalog and actual visual QA, `const {images} = await original.readReviewedRelease(ref)` reconstructs the prepared image records from the pinned selection and saved receipt hashes in two callbacks. Use the saved selection's actual notes and column, add the reviewed editorial files to the returned tree, then commit on the returned base. Check the release head before the usual non-force update, PR and finalizer below. If the release head moved, repeat step 4. Only a final, checked release commit advances the editorial PR for publication.

Intermediate packet commits use `[skip ci]`. The final release commit requests the ordinary checks. A different handoff format can use the per-cut or unassigned-stock helper below.

For a selected Library original, a member of an existing Library ZIP, or a producer-chat photo, use `original.prepareSuppliedCut` once per cut. Load `producer` from that model's existing `cast-source-drafts:drafts/cast-voices/<model_id>.json`. Use the frozen `issue`, the original candidate commit and the measured import time. Keep the source ordinal separate from the position on the page. In [issue 002, cut 09](https://github.com/RollNuts/mihassei-news/blob/778e50922ae5740e4f6d328ad4e3128b2aa434a5/maintenance/photo-issues/november-coast-city-rio-015/09/receipt-supplied-914dc376be35-ce649a006a9b.json), original 03 became published cut 09.

```js
// On a fixed handoff with ready-to-use candidates, select the reviewed JPEG by its exact path.
// Two source archives can use the same original number.
const matches = handoff.candidates.filter(row => row.jpeg.path === reviewedJpegPath);
if (matches.length !== 1) throw Error("Expected one reviewed candidate at the pinned commit");
const candidate = matches[0];
const cut = original.prepareSuppliedCut({
  issue, cutId, producer, importedAt,
  candidate: {...candidate, commit},
  readback: {commit, source_ordinal: candidate.source_ordinal, image_bytes_verified: true,
    source: {...measuredOriginal, data: originalBytes}, jpeg: {...measuredJpeg, data: jpegBytes}}
});
```

For an older handoff for the **same** shoot, take the selected file, original ordinal and actual conversion settings from its fixed receipt.

Read both files inside code mode. Each measured record carries its pinned path, Git blob ID (`blob`), SHA-256, length and dimensions. Keep the observed Library link and file ID or the registered chat URL, turn and image number. If GitHub Contents omits a large numbered PNG, [read its bytes at the candidate commit](IMAGE_CANDIDATE_RECEIVER.md#reading-a-saved-weekly-original). For a split original, verify the reassembled file and store its complete blob before using its ID. `cut.files` contains two existing blob references and the formal receipt text for GitHub `create_tree`. `cut.image` carries the receipt SHA for the catalog. Add the actual visual review separately after inspecting the release files.

#### Stock received before a feature was assigned

For a feature selected from different shoots, use the frozen target `issue`, its `issue-NNN` edition, and the current head of `photo-issue/issue-NNN` as `releaseCommit`. Take each source's `stockIssue` and `commit` from the [weekly stock handoff](DAILY_EDITORIAL_BRIEF.md#weekly-stock-handoff). Inspect the actual pictures before choosing each target `cutId`. Use the verification runtime above. Run each numbered step in a fresh cell and carry forward only the returned metadata.

1. Build a plan for one cut. For an unassigned recovery receipt, use `const batch = await original.openUnassignedStock({stockIssue, commit, issue, releaseCommit}); text(batch.planCut({sourceOrdinal, cutId, importedAt}))`. For a numbered handoff use `text(await original.planNumberedStockCut({stockIssue, commit, issue, releaseCommit, candidateNumber, cutId, importedAt}))`. Keep the actual import time across retries. File number, original ordinal and page position can all differ.
2. If the feature already contains staged cuts, first run `text(await original.checkStockSiblings(plan))` in a separate cell. Pass the returned fingerprint as `siblings` (omit it for the first cut). `text(await original.prepareStockCutTree(edition, plan, siblings))` reads the original PNG and JPEG at the pinned source commit and prepares their release files and receipt. If it returns `status: "staged"`, continue at step 5.
3. `text(await original.commitStockCutTree(plan, tree))` creates the cut commit using the tree returned by step 2.
4. `text(await original.publishStockCut(edition, plan, prepared))` verifies the commit from step 3 and advances the release branch. If the response is lost, retry with the same plan and commit.
5. `text(await original.readStagedStockCut({issue, cutId, edition, commit:prepared.commit}))` confirms the submitted commit on the release branch and recovers the image catalog entry from the files stored on the release branch. For a previously staged cut, use its returned commit; if the editor lost that reference, omit `commit` to read the current release head and compare the returned image with the selected source. Repeat for the next selected cut at the current release head. The final editorial commit adds the catalog, column and visual review, then goes through the normal release PR and production check below.

For a [producer handoff with `source_candidate_only: true` and a null publication number](https://github.com/RollNuts/mihassei-news/blob/fe276d1b4e698f91d6ecc5f870fab4e7a16e9b9c/maintenance/photo-issue-plans/november-indoor-pool-rena-014/handoff.json), open the stock once for the editor's draft feature. `stockIssue` identifies the original storage location. `releaseCommit` is the fixed head used to prepare the release. The draft feature can be new to its catalog.

```js
const batch = await original.openUnassignedStock({stockIssue, commit, releaseCommit, issue});
text(batch.nearSimilarGroups); // Choose at most one source ordinal from each group for this feature.
const cut = batch.prepareCut({sourceOrdinal, cutId, importedAt,
  readback: {source: pngResult.structuredContent, jpeg: jpegResult.structuredContent}});
```

Read the numbered PNG with `read_weekly_candidate_png` and the recorded JPEG with GitHub `fetch_file` (`encoding: "base64"`), both at `commit`. If that PNG exists only in parts, bind the same connector as `readWeeklyPng` when creating the original handoff helper. Use the original receipt's commit, digest and source SHA for `ref`. Run each line in a separate tool cell, keeping verified bytes in code mode.

```js
const verified = await original.readOriginalStep(ref); // Continue a pending read with its state.
const uploaded = await original.uploadNumberedOriginal(ref, verified);
const plan = await original.prepareNumberedOriginal(ref, uploaded, uploaded.base);
const submitted = await original.submitNumberedOriginal(ref, uploaded, plan);
// In the next cell if submission is pending or its reply was lost:
const saved = await original.confirmNumberedOriginal(ref, uploaded, plan);
```

Use the commit returned with `numbered_original_staged` as the stock revision for both reads and for `openUnassignedStock`. Keep the same plan for confirmation. A pending response still needs a readback of the exact candidate head and PNG. If the branch moved, reopen its current head before preparing another update. The editor still chooses the release cuts after visual inspection. The release receipt retains the stock paths, original ordinal and split receipt.

Use the weekly editor's normal release PR. For a new release branch, use the latest `main` commit as parent and its tree as `base_tree_sha`. For an existing release PR, use its current reviewed head and tree to preserve its other edits. Keep staging files and unused stock on the pinned candidate commit. If a release PR already includes staging-only additions, exclude those additions from its proposed release tree, preserving its other edits and the original candidate commit. Reuse that PR. Add the selected images, receipts, QA and catalog changes at their release paths. Check the full PR diff against `allowed_for("photo_issue", path)`. Within `maintenance/photo-issue-plans/<issue>/`, the finalizer accepts only matching JSON files directly in that directory. Verify that the destination blob IDs match. Before advancing a release ref, reread its head, use the reviewed head as the parent and set `force: false`. If the head moved, reconcile the changes before handing off the final PR head. See [finalizer path scope](../scripts/publication_finalize.py) and [publication handoff](OPERATIONS.md).

For earlier source packets with a verified full original PNG, use `runtime.convertPhoto(pngBytes)` and check its `source_png_sha256` against the retained source record. For multiple cuts, work sequentially on the existing candidate branch. Commit each PNG/JPEG pair or small batch together with its inventory entries, actual hashes and transform. Read back both files at that commit and verify their hashes before starting the next batch. After a timeout, check the existing inventory against the committed files and SHA-256 values and continue with the missing cuts. The split-receipt staging methods require their original receipt. The article converter produces 16:9 news photos.

For an individual Library original, record its observed `https://chatgpt.com/space/file/libfile_...` or `https://chatgpt.com/library?file_id=libfile_...&account_id=personal` URL in the per-cut `user_supplied_photo` receipt and the matching ID in `source.library_file_id`.

For an existing ZIP, the receiver hashes the complete downloaded archive and the original bytes of the named member. The [fixed archive receipt for issue 012](https://github.com/RollNuts/mihassei-news/blob/ddb1b08236bebac209c0afea3e3401b0e54b5685/maintenance/photo-issue-plans/november-city-evening-mio-012/stock-recovery/receipt.json) shows the Library query URL and matching file ID, `source.archive` (`format: "zip"`, filename, complete archive SHA-256 and byte count, member basename), and the extracted original's own SHA-256, byte count and dimensions. The archive and member names are single safe filenames. The member extension matches its PNG or JPEG bytes. Preserve the observed generation fields, including `"not_retained"` and `null` for unknown turns or prompts.

The editor can reuse that evidence. At the pinned candidate commit, read the receipt row for the selected archive ID, member and original SHA, then read the original and prepared JPEG bytes at their recorded paths. For a numbered candidate PNG, the weekly reader above can supply its bytes. For another recorded path, use the immutable GitHub raw file at the same commit. Keep the Git blob IDs from those exact paths. Pass the receipt row's `url`, `library_file_id` and complete `archive` object with the measured original in `readback.source`; the helper compares them with `candidate.source` and verifies both images against their hashes and dimensions. A verified archive receipt and those retained files are sufficient for release preparation.

Local supplied-photo conversion may use Pillow with `transform: {encoder: "Pillow", max_edge: 1200, jpeg_quality: 84, geometry: "preserve-aspect"}`. Record the actually used Pillow version in `encoding_details.pillow_version`, with `resample: "LANCZOS"`, `mode: "RGB"`, `optimize: true`, `progressive: false`, and `subsampling: 0`. Keep the original aspect ratio and the existing byte, dimension, quality and visual-review gates. Older `macOS sips` receipts and observed Library share/conversation references stay unchanged; do not relabel a new Pillow conversion as sips or rewrite a prior receipt to fit the new route.
