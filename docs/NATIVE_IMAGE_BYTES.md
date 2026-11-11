# Native image byte helper

`scripts/native_image_bytes.js` converts an actual ImageGen PNG byte array or base64 data URL to a 1200×675 JPEG at quality 84. It uses a centered, aspect-preserving cover crop, bilinear interpolation, and a white background for transparency. It performs no network requests, file writes, generation, publishing, or approval.

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

A file ID is an input to the supported file service, not a filesystem path or a URL to construct. In environments exposing Library `prepare_materialize`, supply the actual file ID and filename according to its current schema, then use the official transfer helper. Do not save expiring download URLs in the repository. If retrieval fails, record the exact failed operation and the real source reference; do not report a search miss as a failed download.

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

The calling importer must still record the actual prompt, actual generation/review times, current article fingerprint, final JPEG byte length, and a real visual-review decision, and preserve prior records without rounding their integers. See the existing publication rules in `VISUAL_OPERATIONS.md`.

## Regression fixtures

`scripts/native_image_bytes_test.js` runs without Node when provided the same API and pinned source map:

```js
const runTests = new Function(testSource + "\nreturn NativeImageBytesTests;")();
const report = runTests(api, sources);
```

The fixtures test a known PNG-to-JPEG byte digest, original PNG SHA, UTF-8/base64, direct current-claim selection despite old/ingredient decoys, exact timestamp preservation, absent identifiers, duplicate CBOR keys, broken box bounds, PNG CRC corruption, and dependency digest changes. C2PA fixtures are explicitly unsigned synthetic parser data. They do not assert provider provenance.

For an existing Node environment, both files also expose CommonJS exports. Node is optional and the helper itself does not use `Buffer`, `require`, filesystem, networking, `crypto`, `TextEncoder`, `atob`, or `btoa`.


