# Native image byte helper

`scripts/native_image_bytes.js` converts an actual ImageGen PNG byte array or base64 data URL to a 1200×675 JPEG at quality 84. It uses a centered, aspect-preserving cover crop, bilinear interpolation, and a white background for transparency. It performs no network requests, file writes, generation, publishing, or approval.

## Preferred path: capture generation and save the candidate in one execution

Use `generateAndStage` for new generation. It calls the supplied generator only after the current execution reads an existing PNG through the configured byte-transfer callback, converts it, saves a GitHub tree/commit/branch, reads back matching PNG/JPEG hashes, and acquires the plan's durable generation reservation. It then uses that same callback for the generated result and saves the real candidate in the same execution. If the byte bridge, save/readback or reservation fails, generation is not called. Existing saved candidates are recovered first, without generation or another preflight.

`scripts/native_image_stage.js` uses one isolated `native-image-preflight/transport` branch for the transport sample. Each generating invocation repeats the actual transfer and advances that branch with an expected-head, non-forced update; it never uses an earlier pass to authorize a different worker. Concurrent changes stop the check. The sample receipt has kind `native_image_transport_preflight`, never an article candidate, generation claim or visual approval. Its PNG is at most 512 KiB. This checks the tested route and size, not unlimited future transfers. The actual generated image still receives all ordinary integrity and readback checks.

After the preflight, the entry point checks the candidate again, then atomically creates `native-image-generation-claim/<plan.identity>` before calling the generator. Ownership requires a successful create response naming that branch, the exact commit SHA from the subsequent Git ref lookup, and matching file readback. An existing reservation blocks another worker or a fresh runtime from generating the same plan; a candidate completed during the check is recovered instead. A lost or malformed creation response does not establish ownership. The receipt's `reserved_generation_unconfirmed` status does not prove that the generator was called or completed. Reservations have no automatic expiry, replacement or deletion; use the recorded source/checkpoint for recovery, without changing the originating identity to bypass the reservation. Candidate and reservation branches remain separate, so recovery with `stage/resume/load` does not require creating a new claim.

For a transport-only diagnostic, `checkTransport(plan, options)` takes the same probe and `readBytes` options without a `generate` callback. It performs the actual transfer/save/readback and creates no generation claim or article candidate. Its returned proof cannot be supplied to bypass `generateAndStage`, which repeats the check in its own execution.

The ordinary `stage/resume/load` methods remain available for recovery without generation. They store the complete source PNG as ordered parts of at most 512 KiB, plus the JPEG and provenance, together on the article's candidate branch; they do not edit published article/image manifests, write an accepted review, create a PR or deploy. Readback reconstructs the original PNG, verifies its complete SHA-256, and checks that the pinned conversion reproduces the saved JPEG. This avoids the GitHub Contents response that can return an empty content string for a binary file larger than 1 MiB even when the request succeeds. A generated result or failed-save checkpoint must be recovered, not replaced by another generation.

Read both helpers from the latest reviewed repository revision and initialize the existing converter. The following assumes the reviewed draft's `slug`, `articleSha256`, `prompt`, `baseCommitSha`, `baseTreeSha` and stable `logicalRunId` are already known. For a new article, first save the reviewed draft to its working commit: `prepare` checks that the target article and its fingerprint exist in `baseCommitSha`. The base tree must belong to that commit. Use the same logical run ID on retry; a retry's wall-clock time is not a new identity. A recovery owner inherits the originating plan and logical run ID, rather than replacing them with its later review slot. Check the existing publication/recovery handoff and candidate branch before preparing a new generation.

**Unchanged main does not imply no recovery work.** For each unresolved slug in the existing handoff, check its recorded candidate references and enumerate the matching `native-image-candidate/<slug>/` branches before returning idle. Read new or changed `receipt.json` files and compare the slug, current article fingerprint, logical run ID, source/JPEG hashes and existing acceptance/rejection history. Recover an unprocessed matching candidate with its originating plan before generating another image; skip identities already adopted or rejected. Record changed branch/commit and receipt SHA in the existing task result or handoff, without creating another ledger. A failed or incomplete branch lookup is an unresolved lookup, not evidence that no candidate exists; do not replace it with an unchanged-main shortcut.

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
    const file = actual.structuredContent;
    if (actual.isError || file?.encoding !== "base64" || typeof file.content !== "string")
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

After staging, `const images = await stager.load(plan)` returns verified `sourcePngDataUrl` and `jpegDataUrl`. Pass them directly to `image(images.sourcePngDataUrl)` and `image(images.jpegDataUrl)`; never emit the whole result with `text` or `notify`. Compare both displayed images with the full current article. Only after that review, use the normal publication PR to install the JPEG, its truthful generation record and a current-SHA visual decision. Keep the source candidate retrievable. Existing accepted photos, integer seeds and previous rejections remain unchanged until an actual reviewed replacement is published.

If a save fails, retain its compact checkpoint (including the plan), reservation reference and actual returned references in the existing task handoff. `resume(plan, checkpoint)` works in a fresh runtime once `filesComplete` is true: all original PNG parts, JPEG and receipt have returned blob SHAs. It can then finish the tree, commit, branch and readback without original data-URI bytes. `probe(plan)` reuses an already completed candidate. Before that boundary, the error's `imageGenResult` is the raw tool result, which may contain only a file ID or local path. Pass `error.transferredImage ?? await readBytes(error.imageGenResult)` as `stage`'s `imageGenResult`, with the retained `observedAt`, `checkpoint`, `transportPreflight`, `generationClaim` and any recovery metadata. This retries byte acquisition or saving, without calling the generator. The example `store` is transient and helps only in the same conversation; it is not cross-run persistence. Conversion failure or a total interruption before all blobs are saved still requires the original image bytes or a real retrievable file reference. Do not claim this helper recovers bytes that were never durably saved. A blob alone or a commit without the intended saved branch is incomplete. An authorization failure, missing file on an existing branch, malformed response or ambiguous lookup error is not permission to overwrite a branch or generate again.

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
| PNG or JPEG at an actual local path | Read it in the executor that owns that path. Transfer its bytes to code mode through an available execution tool, as below. |
| Real conversation/Library file ID and filename | Follow the current Library skill and the exposed materialization/transfer tools to retrieve that exact file, then verify its recorded SHA and dimensions. |
| A path in a previous run that is absent here | Try its recorded file ID through the exact-ID materialization route. A missing local path or an empty Library search does not establish that the source is unavailable. |

A file ID is an input to the supported file service, not a filesystem path or a URL to construct. In environments exposing Library `prepare_materialize`, supply the actual file ID and filename according to its current schema, then use the official transfer helper. Do not save expiring download URLs in the repository. If retrieval fails, record the exact failed operation and the real source reference; do not report a search miss as a failed download. In particular, `library_read` returning "not visible in the requested files surface" is a read-surface failure, not the result of a `prepare_materialize` call. Record which operation was actually attempted before diagnosing the execution environment.

For an unresolved file ID, discover the current Library app's `prepare_materialize` through the available tool registry/search before declaring that route unavailable. A failure from a different `files` materialization tool does not establish that the Library app route failed. Follow its current skill and schema, use the exact file ID, and finish the returned transfer before deciding whether bytes are recoverable.

Keep three recovery states distinct: a saved candidate branch with a receipt, an unsaved output known only by file ID/local path, and a confirmed missing retrieval/transfer capability. Only the first can be loaded with its original plan. A missing historical plan or logical run ID does not prevent exact-file recovery. After recovering bytes, create a stable recovery identity from the real file ID, preserve the source article and recovery evidence, and state that the original generation logical run ID or exact prompt was not retained when that is the case. Do not present the recovery identity as the original generation identity.

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



