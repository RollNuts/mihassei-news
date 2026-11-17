# ChatGPT JPEG candidate file receiver (owner approval pending)

A distinct, stateless MCP endpoint: `POST https://veripsa.com/api/image-candidate/mcp`.
The existing `/api/image-control/mcp` and its `read_image_control` tool remain independently read-only.

## File input and current status

Following [OpenAI's Define file inputs](https://developers.openai.com/plugins/reference#define-file-inputs), both tools declare `_meta["openai/fileParams"]=["file"]`. A file has **required** `download_url` and `file_id`, with optional `mime_type` and `file_name`. `probe_candidate_jpeg` retrieves a single temporary file URL and checks actual JPEG dimensions, byte count and SHA-256. It makes no GitHub requests, image conversions, model calls or storage writes.

A file URL must be HTTPS with the exact established OpenAI file host `files.oaiusercontent.com`. Custom ports, credentials, redirects and other hosts fail closed. There is a strict 700 KiB streaming cap, 5-second download deadline, JPEG magic/structure and 1200×675 size check, and no URL, file ID or signed query in the response. It returns only the SHA-256 of the file ID, not the ID itself. Exact URL-host compatibility requires a **real ChatGPT-hosted fileParam call** after connecting this separate MCP endpoint. A mock fetch of an already-verified repository JPEG is not proof that a particular ChatGPT client supplies the same host.

`stage_candidate_jpeg` exists in the schema for review but is disabled unless **all** of these are separately configured by the owner:
- Worker secret `IMAGE_CANDIDATE_GITHUB_WRITE_TOKEN`, a new, dedicated, short-lived fine-grained GitHub token. Repository selection must be **RollNuts/mihassei-news only**, with **Contents: read/write**, Metadata: read (implicit). No Actions-write, Administration, workflows or organization-wide access. Do not reuse or expand `IMAGE_CONTROL_GITHUB_READ_TOKEN`.
- Worker secret `IMAGE_CANDIDATE_CALLER_KEY`, a distinct high-entropy owner-only bearer secret. The ChatGPT plugin connection must send it in the `Authorization` header. Never put it in tool arguments, GitHub, browser code or logs.
- Worker runtime variable `IMAGE_CANDIDATE_WRITE_ENABLED="true"`, only after explicit approval and a production probe; absent/false remains disabled. This is **not** configured by this PR.

Before any credentialed GitHub request, the write handler validates the caller bearer against the configured server key and the exact file/claim shape. It then validates the uploaded JPEG and current claim hash. An untrusted or unauthenticated request cannot use the GitHub token to fan out to arbitrary API calls. No long-lived session, R2/KV/D1, new Workflow, API, external converter or GitHub App is added.

## Owner-created repository claim

A trusted repository writer, using existing GitHub access (not the Worker), creates a claim **before** candidate upload. First select a known pre-claim branch head `expected_head_sha`. Generate a private random nonce (32–128 URL-safe characters), hash the nonce and ChatGPT file ID, and commit `maintenance/image-upload-claims/<slug>/claim.json` on a `native-image-candidate/<slug>/<article_SHA_first16>-<12hex>` branch. The public claim stores `version:1`, fixed `repository`, and every submitted `claim` field **except** `nonce` and `claim_commit_sha`.

The claim contains exact JPEG SHA-256 and bytes, `file_id_sha256`, `nonce_sha256`, candidate branch/path, current article SHA, and a short UTC `expires_at` (within 15 minutes). It records `expected_head_sha` (the parent of the claim commit), and the repo writer separately supplies the resulting `claim_commit_sha` in the tool request. This avoids putting a commit's own hash inside that same commit. The Worker verifies both branch HEAD == `claim_commit_sha` and the claim commit's sole parent == `expected_head_sha`. Only the **hash** of the nonce appears in the repository; the raw nonce is supplied once in the protected tool call and must not be copied to a PR/log.

The handler compares the public claim to the tool's provided fields, binds the raw nonce only by its hash, checks that the repository is public and the existing `free-cpu-scenes.yml` workflow is active (workflow state is fetched anonymously from the fixed public repository, so the **Contents-write** token does not need Actions-read), checks the 1.5 GiB generated-photo archive cap, and then only creates `maintenance/native-image-candidates/<slug>/candidate.jpg` on that candidate branch. It never changes `main`, `content/articles.json`, `assets/generated`, visual approvals, generation IDs or legacy seeds. Updating an existing image is forbidden; exact replay returns `reused`, different bytes/head return a conflict. A Git commit/tree is written and the branch ref is advanced with `force:false`, with exact JPEG bytes read back.

The current real `assets/generated/manifest.json` is about **763 KiB**. GitHub's Contents API base64 response is over 1 MiB; the receiver therefore separates the bounded 2 MiB **HTTP Contents response** and 1 MiB decoded **inventory JSON** from the 700 KiB individual JPEG decoder. A response exceeding those limits fails closed. GitHub uses `GET /git/ref/heads/...` (singular) for the current ref, but `PATCH /git/refs/heads/...` (plural) for the non-force ref update. For `reused`, the server rechecks the original immutable claim, nonce hash, expiry, claim commit parent, current branch commit parent and exact JPEG bytes before acknowledging a replay. Matching JPEG alone is never enough.


## Test vs. deployment

Offline tests use a genuine, already-approved 1200×675 JPEG from the repository and a mocked ChatGPT signed URL. They check actual bytes, SHA, MIME, size, rejection, disabled state, replay, owner authentication and optimistic branch conflicts. These are **mock delivery tests**, not ChatGPT-to-Worker live attachment delivery or production Worker CPU timing.

The existing free Cloudflare Worker budget is **10 ms CPU per request / 50 subrequests**. No JPEG conversion or model inference runs in the Worker. A real proof of Worker CPU/subrequests and the signed file URL can be collected only after the separate MCP route has deployed and is connected in ChatGPT. Until then, real transfer/write and platform metering are *unverified*, not assumed free.

On every published revision, the **existing** `verify-public-photos.yml` now also calls `scripts/check_candidate_receiver.py`, which sends one JSON-RPC `tools/list` request to the separate public endpoint and verifies the `openai/fileParams` declaration. This is a state-free, read-only schema probe (HTTP POST, no file passed), not a successful real ChatGPT file transfer, and not a new workflow.

## No automatic credentials

This PR does not provision credentials, grant repository write access, switch on writes, run a new workflow or save/upload any generated image. Explicit owner approval is required for the credential and final write enablement. No candidate JPEG is visually accepted by this transport; existing editorial image review remains mandatory.
