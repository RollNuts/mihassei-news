# Native image byte helper

`scripts/native_image_bytes.js` converts an actual ImageGen PNG byte array or base64 data URL to a 1200×675 JPEG at quality 84. It uses a centered, aspect-preserving cover crop, bilinear interpolation, and a white background for transparency. It performs no network requests, file writes, generation, publishing, or approval.

## Preferred path: capture generation and save the candidate in one execution

When the run exposes `tools.image_gen__imagegen` with a PNG `image_url` data URI, call it **inside the same code-mode execution that saves its result**. Do not first call a separate image/Python environment and then try to recover an unrelated local path. This route needs the native image tool and the GitHub connection, but no shell, Node, Python, local checkout or Library transfer.

`scripts/native_image_stage.js` uses the existing byte helper to save an unapproved candidate. It does not generate images, edit published article/image manifests, write an accepted review, create a PR, or deploy. It stores the complete source PNG as ordered parts of at most 512 KiB, plus the JPEG and provenance, together on a candidate branch. It reads the parts back, reconstructs the original PNG, verifies its complete SHA-256, and verifies that the pinned conversion reproduces the saved JPEG. This avoids the GitHub Contents response that can return an empty content string for a binary file larger than 1 MiB even when the request succeeds. The branch is determined before generation from the article and logical run, so a retry checks and reuses the prior candidate first.

Read both helpers from the latest reviewed repository revision and initialize the existing converter. The following assumes the reviewed draft's `slug`, `articleSha256`, `prompt`, `baseCommitSha`, `baseTreeSha` and stable `logicalRunId` are already known. For a new article, first save the reviewed draft to its working commit: `prepare` checks that the target article and its fingerprint exist in `baseCommitSha`. The base tree must belong to that commit. Use the same logical run ID on retry; a retry's wall-clock time is not a new identity. A recovery owner inherits the originating plan and logical run ID, rather than replacing them with its later review slot. Check the existing publication/recovery handoff and candidate branch before preparing a new generation.

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
    create_branch: args => tools.mcp__codex_apps__github_create_branch(args)
  }
});
const plan = await stager.prepare({
  slug, articleSha256, prompt, baseCommitSha, baseTreeSha, logicalRunId
});
const existing = await stager.probe(plan);
if (existing.status === "saved") {
  text({status: existing.status, branch: existing.branch,
        jpegPath: existing.jpegPath, reused: true});
} else {
  const generated = await tools.image_gen__imagegen({
    prompt: plan.prompt, transparent_background: false
  });
  const observedAt = new Date().toISOString();
  try {
    const saved = await stager.stage(plan, {
      imageGenResult: generated, observedAt
    });
    text({status: saved.status, branch: saved.branch,
          commitSha: saved.commitSha, jpegPath: saved.jpegPath});
  } catch (error) {
    // Transient same-conversation recovery; never print the original data URI.
    store("native-image-retry-" + plan.identity, {
      plan, checkpoint: error.checkpoint, imageGenResult: generated, observedAt
    });
    text({status: "incomplete", branch: plan.branch, phase: error.stage,
          message: error.message});
    // Preserve the compact checkpoint in the existing durable task handoff.
    // Never call ImageGen again to retry a save.
  } finally {
    generatedImage(generated);
  }
}
```

After staging, `const images = await stager.load(plan)` returns verified `sourcePngDataUrl` and `jpegDataUrl`. Pass them directly to `image(images.sourcePngDataUrl)` and `image(images.jpegDataUrl)`; never emit the whole result with `text` or `notify`. Compare both displayed images with the full current article. Only after that review, use the normal publication PR to install the JPEG, its truthful generation record and a current-SHA visual decision. Keep the source candidate retrievable. Existing accepted photos, integer seeds and previous rejections remain unchanged until an actual reviewed replacement is published.

If a save fails, retain its compact checkpoint (including the plan) and actual returned references in the existing task handoff. `resume(plan, checkpoint)` works in a fresh runtime once `filesComplete` is true: all original PNG parts, JPEG and receipt have returned blob SHAs. It can then finish the tree, commit, branch and readback without original data-URI bytes. `probe(plan)` reuses an already completed candidate. Before that boundary, retain the actual generation result and retry with `stage(plan, {imageGenResult, observedAt, checkpoint, ...})`, preserving any recovery metadata. The example `store` is transient and helps only in the same conversation; it is not cross-run persistence. Conversion failure or a total interruption before all blobs are saved still requires the original image bytes or a real retrievable file reference. Do not claim this helper recovers bytes that were never durably saved. A blob alone or a commit without the intended saved branch is incomplete. An authorization failure, missing file on an existing branch, malformed response or ambiguous lookup error is not permission to overwrite a branch or generate again.

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

For a local-file route, first read and hash a small existing file through the actual execution tool inside code mode. Do not generate an image merely to test tool availability. If no byte-returning route is exposed, do not start a new generation: retain the existing article fallback and record the specific missing capability once. Continue text review and inspection of already-accessible candidates. Retry the import when the tool/source availability changes, not every hour under identical conditions. Do not invent tools, use paths owned by another executor, or treat an uncalled materialization operation as a failed download.

The present chat's tools do not establish what a scheduled worker receives. The Automation response fields `environment_selection` and `environments` describe saved selection, not proof of a previous run's capabilities. If environment selection is not an exposed update input, a prompt change does not attach an executor. Keep this limitation distinct from successfully importing an individual photo.

| Actual result available | Import route |
| --- | --- |
| PNG data URL / byte array in the current code-mode call | Pass the actual value to `runtime.convert`, then upload the resulting JPEG bytes in the same execution. |
| PNG or JPEG at an actual local path | Read it in the executor that owns that path. Transfer its bytes to code mode through an available execution tool, as below. |
| Real conversation/Library file ID and filename | Follow the current Library skill and the exposed materialization/transfer tools to retrieve that exact file, then verify its recorded SHA and dimensions. |
| A path in a previous run that is absent here | Try its recorded file ID through the exact-ID materialization route. A missing local path or an empty Library search does not establish that the source is unavailable. |

A file ID is an input to the supported file service, not a filesystem path or a URL to construct. In environments exposing Library `prepare_materialize`, supply the actual file ID and filename according to its current schema, then use the official transfer helper. Do not save expiring download URLs in the repository. If retrieval fails, record the exact failed operation and the real source reference; do not report a search miss as a failed download. In particular, `library_read` returning "not visible in the requested files surface" is a read-surface failure, not the result of a `prepare_materialize` call. Record which operation was actually attempted before diagnosing the execution environment.

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

They use an in-memory GitHub fake and an explicitly synthetic PNG, with no image generation or publication. They cover exact byte readback, a source larger than 1 MiB, reuse without writes, branch/readback interruption, checkpoint recovery in a fresh stager, ambiguous missing-reference errors, fingerprint mismatch, malformed input, and preservation of published files and their exact integer seeds. A live transfer test must use an existing image with truthful recovery provenance; successful current-chat transfer alone does not prove a scheduled worker received the same tools.



