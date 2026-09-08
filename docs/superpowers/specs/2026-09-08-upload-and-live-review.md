# Spec: Object-Storage Upload & Live Review Feedback

**Date:** 2026-09-08
**Supersedes parts of:** `2026-09-04-arabic-ocr-review.md` (ingestion section)

## Problem

Four items, in the user's words:

1. > "Upload file is now using native macos but it should like traditional apps.
   > Upload images and server process these image. PLease remember that i will
   > deploy this project into on premise with docker"
2. > "no sign for user know which section they selected"
3. > "No progress and no auto update if i click on an section. I upload image,
   > the process complete and i need to F5 or deselect and select"
4. > "because of paddle_vl can not suitable for large scale app so let's focus on
   > OCR_REC_MODEL=arabic_PP-OCRv5_mobile_rec OCR_DET_MODEL=PP-OCRv5_server_det"

Followed by a storage decision: images go to S3-compatible object storage
([RustFS](https://github.com/rustfs/rustfs)) rather than a local directory.

## Root causes (verified in code, not guessed)

| Complaint | Cause |
|---|---|
| Native picker | `app/routers/picker.py` shells out to `osascript`; returns 501 when `sys.platform != "darwin"`. `POST /api/batches` takes a `source_dir` **host path**, so the browser and the API must share a filesystem. Both break under Docker. |
| No selection indicator | `BatchList.tsx:89` renders `<button className="batch-item">` with no active state, and `App.tsx` never passes the current `batchId` down. `ImageStrip` already solves this with `strip-active`. |
| Needs F5 | `ReviewPage.tsx:22-29` calls `getImage(imageId)` once in a `useEffect` keyed `[imageId, select]`. Select an image that is still `queued`/`running` and nothing ever re-fetches. |
| No upload progress | There is no upload at all today, so there is nothing to report progress on. |

## Storage decision: S3-compatible, RustFS by default

Images live in an S3-compatible object store, accessed with `boto3`.

**RustFS over Silo.** Both are S3-compatible and the application cannot tell
them apart, so this is a deployment default rather than an architectural
commitment. RustFS is **Apache-2.0**; `pgsty/silo` is a MinIO fork and is
**AGPL-3.0**, whose network-service clause is a question corporate legal
frequently refuses for on-premise commercial deployment. Silo's counter-argument
is inherited MinIO hardening, but RustFS has been developed since 2023-11 with
broad adoption, and its preview-status gaps (MinIO on-disk compatibility, S3
Tables/Iceberg) are features this project never touches. Its published benchmark
(2.3× on 4 KB objects, 2-core/4 GB host) is not evidence for this workload —
scene photos are 3–8 MB — and was not a factor.

Swapping to Silo, MinIO, Ceph or AWS S3 is one `S3_ENDPOINT` change plus a
compose service. No application code differs.

## Scope

**In scope:** the `Storage` abstraction and its two backends; server-side upload
with validation; routing OCR reads and image serving through storage; the
selected-batch indicator; batch and upload progress; auto-refresh while OCR
runs; removal of the native picker; adding RustFS to `docker-compose.yml`;
`.env`/README updates marking `paddle_vl` experimental.

**Out of scope — separate plan:** Dockerfiles for api/worker/frontend and the
full application compose stack. Adding the RustFS *service* to the existing
compose file is in scope, matching how Redis is already handled there — the app
cannot use `s3` without a bucket existing somewhere.

**Out of scope — deliberately not built:** resumable/chunk-offset uploads,
presigned URLs, per-user auth, virus scanning, bucket lifecycle policies,
server-side folder recursion.

## Requirements

### R1 — Upload replaces host paths

- The client sends image **bytes**. The server stores them. No request field
  may contain a host filesystem path.
- Two endpoints, because the batch must exist before files can be keyed under it:
  - `POST /api/batches` — JSON `{name}` — creates an **empty** batch.
  - `POST /api/batches/{batch_id}/images` — `multipart/form-data`, repeated
    `files` field — stores, validates, dedups, enqueues.
- Multi-file select **and** whole-folder select must both work from the browser.
  Folder recursion is the browser's job (`webkitdirectory`), not the server's.

### R2 — Storage is an abstraction with two backends

- A `Storage` protocol, mirroring the existing `OcrEngine` protocol + `get_engine()`
  pattern: `put`, `open`, `as_local_path`, `delete`, `exists`.
- `STORAGE_BACKEND=local|s3`, **default `local`**. Rationale, all three
  independent:
  - `uv run pytest` must not require a running object store. The suite touches
    no external service today (`JOB_BACKEND=inline`, `FakeOcrEngine`, no Redis)
    and that property is worth keeping.
  - `LocalStorage.as_local_path` yields the real path with no copy, so `local`
    is the current behaviour rather than a stub.
  - `./scripts/dev.sh` should not need another container.
- `s3` is the on-premise/Docker configuration.
- `as_local_path(key)` is the seam that keeps `OcrEngine.run(Path)`,
  `app/ocr/crops.py` and `load_bgr` **completely untouched**. Those files are
  where measurement has already reversed assumptions three times; this plan does
  not reopen them.

### R3 — Upload is safe at the trust boundary

Client-supplied filenames and bytes are untrusted.

- The storage key is derived from the content hash, never from the client
  string: `batch-{id}/{sha256}{ext}`. The client's basename is kept only as the
  `Image.filename` display column.
- `LocalStorage` must reject any key that resolves outside its root.
- Bytes are streamed to a temporary file in 1 MiB chunks — never
  `upload.read()` in full — and the size cap is enforced **while** streaming.
- Extension must be in `IMAGE_SUFFIXES`, **and** the file must decode as a real
  image (`PIL.verify()`). A renamed payload with a `.png` suffix is rejected.
- Size cap configurable: `MAX_UPLOAD_MB`, default 25.
- `S3_ACCESS_KEY`/`S3_SECRET_KEY` live in `.env` (gitignored). **The GitHub repo
  is public**: `.env.example` and any compose default carry placeholders only,
  never real credentials.

### R4 — One bad file does not fail the batch

A 200-file upload must not 4xx because file 137 was a PDF:

```json
{"imported": 197, "skipped": 2, "failed": [{"filename": "x.pdf", "reason": "not an image"}]}
```

`skipped` means "sha256 already imported". Because `Image.sha256` is globally
unique (and there is no migration tooling to change that), a duplicate may
belong to *another* batch — the UI must say so rather than silently dropping it.

### R5 — Serving and OCR both go through storage

- `GET /api/images/{id}/file` streams from storage. **Proxy, not presigned:**
  on-premise the API reaches the store by service name but the browser
  generally cannot, and a presigned URL to an unreachable host is a broken
  image with no error message.
- A missing object must keep the endpoint's current 410 contract, not become a
  500 — it is the only signal a reviewer gets that an image vanished.
- `run_ocr_for_image` obtains a local path via `as_local_path`.

### R6 — Selected batch is visibly selected

The active batch row is visually distinct and exposes `aria-current="true"`.
Reuse the existing `strip-active` outline treatment for consistency.

### R7 — Progress is visible

- **During upload:** a determinate bar driven by real bytes-sent
  (`XMLHttpRequest.upload.onprogress` — `fetch()` cannot report upload
  progress), plus an `n/total files` count across chunks.
- **During OCR:** each batch row carries a bar for `(done+approved)/image_count`.

### R8 — No F5, ever

When the selected image is `pending`, `queued` or `running`, its detail is
re-fetched on an interval until it reaches `done`, `failed` or `approved`.

Polling **must stop** at a terminal status. This is a correctness requirement,
not an optimisation: once lines exist the reviewer is editing them, and a poll
response replacing `image.lines` would discard in-flight local edits.

### R9 — Model config documented, VL parked

`arabic_PP-OCRv5_mobile_rec` + `PP-OCRv5_server_det` are already the defaults in
`app/config.py`; no code change needed. `.env.example` and the README must mark
`OCR_ENGINE=paddle_vl` as experimental and not for production, pointing at the
documented CPU hang. The VL engine code stays — it is isolated behind the
`OcrEngine` protocol and costs nothing to keep.

## Consequences to accept

- **`Image.path` becomes a storage key**, not a filesystem path. Verified: its
  only readers are `service.run_ocr_for_image`, `routers/images.py` and
  `exporters.py`. The JSONL export keeps the field name `path` (consumers may
  parse it) with a key as its value.
- **`import_folder`, `_candidate_files` and `sha256_of` become dead code.**
  Verified by grep: `import_folder`'s only caller is the `create_batch`
  endpoint being replaced, and `scripts/bench_ocr.py` never used it. They are
  deleted rather than kept "just in case" — a second ingestion path with
  different `Image.path` semantics is exactly how the two-meanings bug gets in.
- **Existing local rows become unreadable** once `Image.path` holds keys. There
  is no migration tooling and `data/` is gitignored dev state, so the
  documented step is to delete `data/app.db` and re-upload.

## Acceptance

- No `osascript`, no `sys.platform` branch, and no `source_dir` request field
  remain in the codebase.
- With `STORAGE_BACKEND=s3` and RustFS running, upload 2 images from a browser
  that shares no filesystem with the API → both reach `done` with no page
  reload and no manual re-selection.
- `uv run pytest` passes with no object store running.
- The same storage contract tests pass against both backends when
  `S3_TEST_ENDPOINT` is set.
- Uploading a `.pdf` renamed to `.png` leaves no `Image` row and no stored object.
- A filename of `../../evil.png` stores a key inside the batch prefix only.
- The selected batch is identifiable from a screenshot alone.
