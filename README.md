# Arabic OCR Review

OCR a folder of Arabic images with PaddleOCR, then verify the output side by side.

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or any
  Docker Engine) — running, for Redis. `docker info` should succeed.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — Python
  package/version manager.
- Node.js 20+ and npm, for the frontend.

## Setup (macOS)

```bash
git clone <this-repo-url> arabic-ocr-review
cd arabic-ocr-review

uv sync                              # creates .venv, installs Python 3.12 + deps
cd frontend && npm install && cd ..  # frontend deps

cp .env.example .env

docker compose up -d redis           # starts Redis in a container
docker exec arabic-ocr-redis redis-cli ping   # expect: PONG

uv run python scripts/smoke_ocr.py   # downloads OCR models on first run (~100MB)
```

Python is pinned to 3.12: paddlepaddle 3.3.1 has no cp314 macOS wheel. `uv sync`
reads `.python-version` and fetches 3.12 automatically if it's not already
installed — no manual pyenv/python setup needed.

Redis runs in Docker (`docker-compose.yml`), not as a local install. It listens
on `localhost:6379`, matching `REDIS_URL` in `.env.example`, so nothing else
needs to change. To stop it: `docker compose down` (add `-v` to also drop its
persisted data volume).

### Storage

Uploaded images go to object storage, selected by `STORAGE_BACKEND`:

- **`local`** (default) — a directory under `DATA_DIR`. Needs nothing running,
  which is why it is the default for `./scripts/dev.sh` and the test suite.
- **`s3`** — any S3-compatible store. On-premise that is
  [RustFS](https://github.com/rustfs/rustfs), started with the rest of the
  stack via `docker compose up -d rustfs`. The bucket is created automatically
  on first use — nothing to run by hand.

RustFS over `pgsty/silo`: both are S3-compatible and the application cannot
tell them apart, so this is only a default. RustFS is Apache-2.0; Silo is a
MinIO fork under AGPL-3.0, whose network-service clause is a question corporate
legal often refuses for on-premise commercial use. Switching is one
`S3_ENDPOINT` change and a compose service — no application code differs, since
`boto3` speaks plain S3.

`Image.path` holds a **storage key** (`batch-<id>/<sha256>.<ext>`), not a
filesystem path. Objects are named by content hash, so the store is free of
filename collisions and of client-supplied names; the original filename is kept
in the database for display. `DATA_DIR` still holds `app.db` and `exports/`
under either backend — and the image directory too when `STORAGE_BACKEND=local`.

Switching an existing install from `local` to `s3` (or the reverse) does not
migrate objects, and there is no migration tooling: delete `data/app.db` and
re-upload.

The OCR models are pinned explicitly in `.env` rather than left to `lang=`
defaults — in paddleocr 3.7.0 the default recogniser is `PP-OCRv6_medium_rec`,
and PP-OCRv6's 50 languages are Chinese, Traditional Chinese, English,
Japanese and 46 Latin-script languages: **no Arabic**. We use
`arabic_PP-OCRv5_mobile_rec` with `PP-OCRv5_server_det`.

## Accuracy: where the ceiling is

The **recogniser is the bottleneck, not the detector.** PaddleOCR ships Arabic
in exactly one size — `arabic_PP-OCRv5_mobile_rec`, 7.6 MB — plus an older v3
mobile model. There is no `arabic_..._server_rec`. `PP-OCRv5_server_rec`
covers ["Simplified Chinese, Traditional Chinese, English, Japanese, as well
as complex text scenarios such as handwriting, vertical text, pinyin, and rare
characters"][v5rec] — no Arabic. So the detector can be upgraded to the server
tier (and is, by default) but Arabic recognition cannot.

[v5rec]: http://www.paddleocr.ai/latest/en/version3.x/module_usage/text_recognition.html

Measured on `tests/data/pack1` (Abu Dhabi shopfront photos):

- Main signs read well: `كوكب الجمال` 0.96, `ADNOC OASIS` 0.997,
  `واحة أدنوك` 0.82, `شارع الفلاح` 0.93.
- Small, low-contrast, decorative and heavily-slanted text is where it fails.
- Scene text is a different problem from documents. 60–80% line accuracy is
  near state of the art for Arabic *scene* text; scanned documents should do
  much better.

Two real upgrade paths, in order of effort:

1. **Fine-tune the Arabic recogniser on this corpus.** The schema keeps
   `rec_text` (what the model said) apart from `corrected_text` (what the
   reviewer fixed), so every correction is already a labelled training pair.
   Export gives `image → label`. A few thousand reviewed lines is the normal
   amount needed, and this is the only path that fixes the domain gap.
2. **PaddleOCR-VL** — wired in (`app/ocr/paddle_vl_engine.py`,
   `OCR_ENGINE=paddle_vl`) but **experimental and not used in production**: at
   ~1B params it is far too slow for a large corpus on CPU, and it **does not
   currently work on this Mac at all**.
   Tried on 2026-09-04: `pip install "paddlex[ocr]"` is required first (a
   `DependencyError` otherwise); after that the model downloads (~1.9GB) and
   loads its weights fine, but the forward pass hangs - the process sits in
   uninterruptible sleep (`U+` in `ps`), burning almost no CPU over several
   minutes, alongside a `Bucketed engine_config has no entry for resolved
   engine 'paddle_dynamic'; using an empty config for that engine` warning
   at exactly the point it stops responding. This is not a GPU problem -
   PaddlePaddle has no Apple Silicon GPU backend at all, so it was always
   running on CPU (`device="cpu"` is set explicitly) - it looks like a
   genuine engine/backend bug in this release rather than "slow but working".
   Likely fine on the CUDA box, where `native` backend + an actual GPU is
   the combination it's built for. Try `scripts/smoke_ocr_vl.py` there;
   don't rely on it here.

### Tuning the knobs

`.env` exposes the detection thresholds, all defaulting to PaddleOCR's own
values. There is no universally correct setting — measured here, raising
`OCR_DET_LIMIT_SIDE_LEN` to 1280+ split the sign `كوكب الجمال` into two
boxes, while *lowering* it to 736 split `مرحبا بالعالم` in the synthetic
fixture. What matters is text height in pixels. Measure before changing:

```bash
PYTHONPATH=. uv run python -u scripts/bench_ocr.py tests/data/pack1/*.png
```

That prints PaddleOCR's defaults against your current `.env` side by side,
tagging which recogniser won each line. Note that **confidence is not
accuracy** — a confidently wrong read scores 0.98 — so judge the text, not
the mean.

### Detector tradeoff, measured

`server_det` and `mobile_det` don't just differ in speed — they detect
*different boxes*. On `tests/data/pack1`: `server_det` recovers `ADNOC` and a
near-complete street name (`شار الشبخ راشد بن سعيد`) that `mobile_det` missed
outright, but `mobile_det` finds `KALYAN` (a correct brand name) that
`server_det` never boxes at all, regardless of orientation settings. Neither
dominates. If a specific brand/word matters and goes missing, try
`OCR_DET_MODEL=PP-OCRv5_mobile_det` before assuming it's a recognition
problem — it may never have been detected.

### The second recognition pass

Each line scoring at or below `OCR_SECOND_PASS_MAX_SCORE` is re-read from a
perspective-corrected, upscaled, contrast-stretched crop using *both* the
Arabic and the Latin recogniser, and the better score wins. This is what
recovers slanted text (a sheared Arabic line smears its ligatures together
far faster than Latin does) and bilingual signs. A rescue must beat the
original by `OCR_RESCUE_MIN_GAIN` to replace it, because scores are not
calibrated between the two models — without the margin, a confident Latin
misread turned `510` into `OLS` at 0.98.

## Run

```bash
./scripts/dev.sh
```

This starts the Redis container (if not already up), the Celery worker, the
FastAPI backend, and the Vite dev server, then waits — `Ctrl-C` stops the
worker/API/UI. Redis itself keeps running in Docker afterward; `docker compose
down` stops it separately.

UI at <http://localhost:5173>, API docs at <http://localhost:8000/docs>.

Running the three processes by hand instead of via `dev.sh` (useful for
watching one process's logs on its own):

```bash
docker compose up -d redis
uv run celery -A app.worker.celery_app.celery worker --loglevel=info --pool=prefork --concurrency=2
uv run uvicorn app.main:app --port 8000 --reload
cd frontend && npm run dev
```

## Review workflow

1. Left sidebar → **Choose images** (multi-select) or **Choose a folder** (the
   browser walks it for you) → give the batch a name → **Upload**. The files
   are uploaded to the server, which stores each one in object storage and
   queues it for OCR. Nothing depends on the browser and the API sharing a
   filesystem, so this works unchanged when the API runs in a container.

   Duplicate images are skipped by sha256 and reported — `2 skipped (already
   imported)` means those exact bytes are already in the database, possibly
   under a different batch. Files that are not readable images are listed
   individually as rejected; one bad file never fails the rest of the upload.
   Per-file size cap is `MAX_UPLOAD_MB` (default 25).
2. The selected batch is outlined in the sidebar and its bar fills as images
   finish. Failed images stay in the strip in red with the error in their
   tooltip. The review pane refreshes itself while OCR is running — no reload.
3. Pick an image. Left = original with polygon overlay (scroll to zoom, drag to
   pan, double-click to reset); right = a magnified crop of the selected line
   above one row per detected line, RTL.
4. `j`/`k` move between lines, `Enter` edits, `a` approves the line, `A` approves
   the image and advances, `n` skips to the next image. Clicking a polygon
   selects its row and vice versa.
5. Export from the sidebar. Files land in `data/exports/`.

If a save fails, the row shows `⚠ unsaved` with the error in its tooltip — a
correction is never silently dropped.

## Data model note

`rec_text` (what the model said) is never overwritten. Corrections live in
`corrected_text`, and `final_text` is the correction when present. This keeps
diffs, QA sampling and a future fine-tuning export possible.

Reading order is computed, not taken from the detector: lines are grouped into
bands by vertical overlap, bands run top to bottom, and within a band lines run
rightmost-first. The strings themselves are never reversed — they are already
logical-order Unicode, and the UI sets `dir="rtl"` so the browser's bidi
algorithm renders them.

## Tests

```bash
uv run pytest              # fast; uses a fake OCR engine, no model download
uv run pytest -m slow      # real PaddleOCR against tests/fixtures/arabic_sample.png
cd frontend && npm test
```

`tests/fixtures/arabic_sample.png` is generated by `scripts/make_fixture.py`
(Pillow + Geeza Pro, pre-shaped with arabic-reshaper) and committed, so tests do
not depend on system fonts.

## Later: Ubuntu + CUDA

Out of scope for now. The only device-aware line in the codebase is `OCR_DEVICE`
in `.env` — set it to `gpu:0` and install the CUDA paddle build from
PaddlePaddle's own index on that machine. Nothing else changes.
