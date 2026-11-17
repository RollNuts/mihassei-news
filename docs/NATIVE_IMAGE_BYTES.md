# Native image byte helper

`scripts/native_image_bytes.js` converts an actual ImageGen PNG byte array or base64 data URL to a 1200×675 JPEG at quality 84. It uses a centered, aspect-preserving cover crop, bilinear interpolation, and a white background for transparency. It performs no network requests, file writes, generation, publishing, or approval.

## Preferred path: reserve generation and save its original candidate

Cloud image operations use the live GitHub image-workflow stop state defined in [VISUAL_OPERATIONS](VISUAL_OPERATIONS.md), without a Mac CLI requirement. The helper fails closed for a private repository, disabled/unknown workflow, failed control lookup, or an excessive public JPEG total. It rechecks before generation and remote writes; stopped candidates remain readable. This changes stop control, not byte-bridge availability or visual approval.

When the native generator is available only as a top-level tool outside code mode, use `reserveExternalGenerationStep` → one actual native tool call → `stageReservedStep`. The reservation reads an existing PNG through the actual byte callback, converts it, saves and reads back PNG/JPEG hashes, acquires a durable create-only claim, and rechecks the stop state before returning. `stageReservedStep` verifies that persisted claim and its immutable preflight against the original plan before using the existing storage path. If the generator is actually callable inside code mode, `generateAndStage` remains a convenience wrapper over the same reservation checks. Neither path generates after a failed check; a saved candidate is recovered first.

`scripts/native_image_stage.js` uses one isolated `native-image-preflight/transport` branch for the transport sample. Each reservation attempt repeats the actual transfer and advances that branch with an expected-head, non-forced update; it never uses an earlier pass to authorize a different worker. Concurrent changes stop the check. The sample receipt has kind `native_image_transport_preflight`, never an article candidate, generation claim or visual approval. Its PNG is at most 512 KiB. This checks the tested route and size, not unlimited future transfers. The actual generated image still receives all ordinary integrity and readback checks.

After the preflight, the entry point checks the candidate again, then atomically creates `native-image-generation-claim/<plan.identity>` before permitting the one generator call. Ownership requires a successful create response naming that branch, the exact commit SHA from the subsequent Git ref lookup, and matching file readback. An existing reservation blocks another worker or a fresh runtime from generating the same plan; a candidate completed during the check is recovered instead. A lost or malformed creation response does not establish ownership. The receipt's `reserved_generation_unconfirmed` status does not prove that the generator was called or completed. Reservations have no automatic expiry, replacement or deletion; use the recorded source/checkpoint for recovery, without changing the originating identity to bypass the reservation. Candidate and reservation branches remain separate, so recovery with `stage/resume/load` does not require creating a new claim.

For a transport-only diagnostic, `checkTransport(plan, options)` takes the same probe and `readBytes` options without a `generate` callback. It performs the actual transfer/save/readback and creates no generation claim or article candidate. Its returned proof cannot replace either reservation entry point's own actual check.

The ordinary `stage/resume/load` methods remain available for recovery without generation. They store the complete source PNG as ordered parts of at most 512 KiB, plus the JPEG and provenance, together on the article's candidate branch; they do not edit published article/image manifests, write an accepted review, create a PR or deploy. Readback reconstructs the original PNG, verifies its complete SHA-256, and checks that the pinned conversion reproduces the saved JPEG. This avoids the GitHub Contents response that can return an empty content string for a binary file larger than 1 MiB even when the request succeeds. A generated result or failed-save checkpoint must be recovered, not replaced by another generation.

Read both helpers from the latest reviewed repository revision and initialize the existing converter. The following assumes the reviewed draft's `slug`, `articleSha256`, `prompt`, `baseCommitSha`, `baseTreeSha` and stable `logicalRunId` are already known. For a new article, first save the reviewed draft to its working commit: `prepare` checks that the target article and its fingerprint exist in `baseCommitSha`. The base tree must belong to that commit. Bind `readImageControl` below to the actually exposed `read_image_control` MCP tool, pass the nonce unchanged and return the whole actual response to the stager. Do not unconditionally select `.structuredContent`: the helper normalizes direct, `.result` and legacy `.structuredContent` responses and rejects errors at either level. Connector runtimes that reject GitHub workflow API URLs use this existing read-only tool; do not synthesize an active state or reuse an old reply. The endpoint echoes a per-call nonce and timestamp, both verified by the helper. An unavailable reader stops image operations. Use the same logical run ID on retry; a retry's wall-clock time is not a new identity. A recovery owner inherits the originating plan and logical run ID, rather than replacing them with its later review slot. Check the existing publication/recovery handoff and candidate branch before preparing a new generation.

**Unchanged main does not imply no recovery work.** For each unresolved slug in the existing handoff, check its recorded candidate references and enumerate the matching `native-image-candidate/<slug>/` branches before returning idle. Read new or changed `receipt.json` files and compare the slug, current article fingerprint, logical run ID, source/JPEG hashes and existing acceptance/rejection history. Recover an unprocessed matching candidate with its originating plan before generating another image; skip identities already adopted or rejected. Record changed branch/commit and receipt SHA in the existing task result or handoff, without creating another ledger. A failed or incomplete branch lookup is an unresolved lookup, not evidence that no candidate exists; do not replace it with an unchanged-main shortcut.

### Native tool outside code mode: one bounded step per cell

Initialize from the actual exposed tools and reviewed helper sources. **Complete separate initialization cells before advancing:** first fetch and verify the two helper sources and pinned dependency sources, then `store` those UTF-8 strings/objects; next restore them, initialize the runtime/stager, `prepare` the original plan and `store` the plan. End each cell with a small summary. Later cells restore sources and plan from `load`, without fetching vendor/helper files again. Do not keep all initialization and the full reservation macro in one volatile execution.

The old whole-macro reservation needs at least 75 connector callbacks plus the PNG read, excluding initialization; the step API limits each invocation to 15 callbacks and two mutations, including cold guard reads. The actual platform limit and whether it applies per cell or per turn are not assumed. Each mutating call still checks public visibility, current-main JPEG capacity and the live stop authority. Only the capture phase fetches the existing PNG; subsequent checkpoints contain serializable base64 strings and the original metadata.

Call **one** step per code-mode cell, then store the returned checkpoint before another tool-heavy operation:

```js
const key = "native-reservation-steps-" + plan.identity;
try {
  const result = await stager.reserveExternalGenerationStep(plan, {
    probeSource, expectedProbeSha256, probeSourceRef, route, readBytes
  }, load(key));
  if (result.status === "pending") store(key, result.checkpoint);
  else store(key + "-result", result);
  text({status: result.status, phase: result.phase, calls: result.tool_calls});
} catch (error) {
  if (error.checkpoint) store(key, error.checkpoint);
  text({status: "stopped", phase: error.stage, message: error.message});
}
```

Do not loop all steps inside one `functions.exec`, create self-messages/tasks, or retry to evade an account quota. `pending` is progress, not generation permission. Preserve the checkpoint and original plan/prompt/source references in the existing durable handoff if the turn/run ends; `store` alone is not cross-run persistence. Stop on an error or no progress and retain that error's checkpoint. The API rejects edited/cross-plan/exhausted checkpoints and bounds total progression to 256 steps.

Only the fresh final `status: "reserved"` response permits the immediately following **one** top-level native generation call with the exact original prompt. The final create-only claim, positive ownership/ref/receipt readback, candidate check and live guard occur together within one bounded cell. `saved`, an existing claim, a lost/aborted final reply or a replayed old checkpoint permits recovery only. Never change the logical identity to try again. The claim remains `reserved_generation_unconfirmed`, not evidence that generation happened.

After the native tool returns, record its actual file ID/path and the **first** real observation time once. Read that exact original PNG with `readBytes`, store `{imageGenResult, observedAt: firstObservedAt, originalSourceRef, transportPreflight, generationClaim}` as `initialInput`, and end that cell before starting the save. Optional `imageGenResult.output_hint` must be the actual tool file ID/path, never a signed URL. In each later cell:

```js
const key = "native-stage-steps-" + plan.identity;
const checkpoint = load(key);
try {
  const result = await stager.stageReservedStep(plan,
    checkpoint ? undefined : load("native-original-" + plan.identity), checkpoint);
  if (result.status === "pending") store(key, result.checkpoint);
  else store(key + "-result", result);
  text({status: result.status, phase: result.phase, calls: result.tool_calls});
} catch (error) {
  if (error.checkpoint) store(key, error.checkpoint);
  text({status: "stopped", phase: error.stage, message: error.message});
}
```

`native-original-<identity>` is the stored `initialInput`. Checkpoints preserve its exact PNG bytes, source metadata, first `observedAt`, proof/claim and returned blob references; do not reread the PNG or retimestamp each step. Source/JPEG/protected-file readbacks are bounded slices, and `saved` is returned only after full PNG SHA, conversion/C2PA correspondence, protected files and final receipt/ref checks. A saved candidate can be read while stopped. Restore the same checkpoint/source on a later existing run; do not regenerate to repair delivery. Candidate saving, visual acceptance and public delivery remain separate.

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

The existing receiver also exposes the read-only `read_candidate_png` tool. The PNG reader additionally permits the bounded Azure account form `^oaisdmntpr[a-z0-9]{1,14}\.blob\.core\.windows\.net$`; JPEG probe/storage still permit only their existing exact observed Azure accounts. This is an untrusted read-only relay: the prefix does not establish OpenAI ownership, generation provenance or adoption eligibility. Bind `readCandidatePng` below to that tool's **actually exposed** code-mode function, and use an original PNG path recognized by the current run's file adapter. For a code-callable generator its callback must return that actual path; for a top-level generator retain its actual returned path for the later save step. A Library ID or a path from another executor is not a substitute. The same callback must read both an existing PNG `probeSource` during the capture step and the eventual generated PNG before the first save step.

```js
const readBytes = async actualPngPath => {
  const received = await readCandidatePng({file: actualPngPath});
  const png = received?.structuredContent ?? received?.result ?? received;
  if (received?.isError || received?.error_code || received?.error || png?.isError || png?.error_code || png?.error ||
      png?.status !== "read" || png.mime_type !== "image/png" ||
      typeof png.image_url !== "string" || !png.image_url.startsWith("data:image/png;base64,"))
    throw Error("Original PNG byte transfer failed");
  if (runtime.sha256(runtime.fromBase64(png.image_url)) !== png.sha256)
    throw Error("Original PNG transfer SHA mismatch");
  return {image_url: png.image_url};
};
```

Capture the PNG payload only inside code mode (direct or inside a supported wrapper): never `text(received)`, `notify(received)`, or print the data URL/base64 into the conversation. Tool text contains a small metadata summary. This reader preserves the exact PNG bytes, including C2PA chunks; it checks MIME, bounded PNG structure and dimensions, without decoding, converting, verifying CRC/signatures, storing or approving. The existing native helper performs CRC/decode/C2PA parsing and `native_image_stage` retains source parts, actual prompt and current article provenance through the ordinary candidate flow. Limits are 8 MiB, 8192 pixels per side and 32 million pixels total.

### Offline PNG bootstrap

When this worker exposes a top-level Python executor, create this deterministic transport fixture there using only the standard library (outside `functions.exec`). It avoids raw-GitHub DNS, external downloads and manual Mac attachments:

```python
import base64, hashlib
from pathlib import Path
png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAABAAAAAJCAYAAAA7KqwyAAAAUUlEQVR4nNXRMQ0AMAwDQUMMiAILnCLpbAbpF4LHTqdfIkuRpCmdaTTmvc7U0jQa49bm4uYiGuMWM4o5jda7mDYzijmNxrjFjGJOozHu/79wAV5JS/CTcXlMAAAAAElFTkSuQmCC", validate=True)
expected = "e7d768652abcb3292c99905f5544c3eeb91933aef573ee912e7babb3450d1744"
assert len(png) == 138 and hashlib.sha256(png).hexdigest() == expected
path = Path("/mnt/data/mihassei-offline-probe.png")
path.write_bytes(png)
print({"path": str(path), "bytes": len(png), "sha256": expected})
```

Use the actual returned path from that run with `read_candidate_png`; keep its data URL in code mode and use the existing bounded preflight. This 16×9 PNG has valid CRCs and no C2PA. The pinned byte helper produces a 1200×675 JPEG of 92,351 bytes, SHA-256 `974a39a8a8e509df3b2324fab77f42a85738a897e1ee5644244ba5c1ca545b33`. It is only a transport fixture and must never be adopted as article artwork. Offline conversion checks do not prove scheduled-worker tool access, store capacity or end-to-end publication.

After deployment, refresh the installed tool schema and prove one **existing** PNG transfer in the actual scheduled GPT runtime before new generation. A locally passing test or the prepared-JPEG route alone does not prove this path's live availability, response-size limits or free Worker CPU usage. A generator exposed only as a top-level tool uses the verified two-phase protocol above; if no native generator is exposed at all, keep the fallback and record that separate capability gap.

After staging, `const images = await stager.load(plan)` returns verified `sourcePngDataUrl` and `jpegDataUrl`. Pass them directly to `image(images.sourcePngDataUrl)` and `image(images.jpegDataUrl)`; never emit the whole result with `text` or `notify`. Compare both displayed images with the full current article. Only after that review, use the normal publication PR to install the JPEG, its truthful generation record and a current-SHA visual decision. Keep the source candidate retrievable. Existing accepted photos, integer seeds and previous rejections remain unchanged until an actual reviewed replacement is published.

If a save fails, retain its compact checkpoint (including the plan), reservation reference and actual returned references in the existing task handoff. `resume(plan, checkpoint)` works in a fresh runtime once `filesComplete` is true: all original PNG parts, JPEG and receipt have returned blob SHAs. It can then finish the tree, commit, branch and readback without original data-URI bytes. `probe(plan)` reuses an already completed candidate. Before that boundary, the error's `imageGenResult` is the raw tool result, which may contain only a file ID or local path. Pass `error.transferredImage ?? await readBytes(error.imageGenResult)` as the original input to `stageReservedStep` for an external reservation (or use `stage` for historical recovery), with the retained `observedAt`, `checkpoint`, `transportPreflight`, `generationClaim` and any recovery metadata. This retries byte acquisition or saving, without calling the generator. The example `store` is transient and helps only in the same conversation; it is not cross-run persistence. Conversion failure or a total interruption before all blobs are saved still requires the original image bytes or a real retrievable file reference. Do not claim this helper recovers bytes that were never durably saved. A blob alone or a commit without the intended saved branch is incomplete. An authorization failure, missing file on an existing branch, malformed response or ambiguous lookup error is not permission to overwrite a branch or generate again.

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
const report = await runStageTests(stageAPI, runtime);
```

They use an in-memory GitHub fake and an explicitly synthetic PNG, with no real image generation or publication. They cover exact byte readback, a source larger than 1 MiB, reuse without writes, branch/readback interruption, checkpoint recovery, ambiguous lookup failures, article binding and exact integer seeds. The generating entry point also checks zero generator calls after acquisition/save/readback failures, a standalone transport diagnostic without claims, distinct probe/reservation/candidate receipts, repeated real checks on the fixed branch, recovery of candidates saved during the preflight, and at most one generator call across independent workers and fresh-runtime retries. Lost or malformed claim responses cannot authorize generation, and lookup failures retain the reservation reference. A live transport check must use the actual worker's callbacks; successful current-chat transfer alone does not prove a scheduled worker received the same tools.



