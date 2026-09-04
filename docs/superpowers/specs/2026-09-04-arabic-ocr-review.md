# Arabic OCR + Review Tool — Spec

**Date:** 2026-09-04
**Status:** Agreed (4 decisions locked with the user on 2026-09-04)

## Problem

A customer has a large number of scanned/photographed images containing Arabic
text. We must OCR all of them and give a human reviewer a fast way to verify and
correct the machine output — image on the left, extracted text on the right, with
each text line linked to its location on the image so the reviewer can find it
without hunting.

## Users

One reviewer, working on a local machine, running the app at `localhost`.

## Solution shape

A local three-part app:

1. **OCR worker** — PaddleOCR (PP-OCRv5 Arabic recognition) running under Celery
   with a Redis broker. Model loaded once per worker process.
2. **Backend** — FastAPI + SQLite (WAL). Stores batches, images, and per-line OCR
   results plus reviewer corrections. Serves image bytes and JSON to the UI.
3. **Frontend** — React + TypeScript + Vite. Side-by-side reviewer view:
   left = original image with polygon overlay, right = one editable row per
   detected line, bidirectionally linked to the overlay.

## Locked decisions

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| 1 | Job queue | **Redis + Celery** | User's choice. Celery over RQ specifically because RQ forks a fresh process per job, which would reload the ~1–2 GB PaddleOCR model on every image. Celery prefork workers load the model once in `worker_process_init` and keep it warm. |
| 2 | Review unit | **Line-level, box-linked** | PaddleOCR's native output granularity is the text line. Click a row → its polygon highlights; click a polygon → the row scrolls into view and focuses. Word-level would require splitting lines, which is unsafe for Arabic ligatures. |
| 3 | Auth | **None; single local user** | Runs on localhost. Reviewer name is a config string used only to stamp audit fields. |
| 4 | Export | **JSONL (master) + per-image `.txt`** | JSONL keeps original text, corrected text, polygons, scores and review status. `.txt` is corrected text only, reading order, UTF-8 no BOM, for downstream NLP. |

## OCR model choice

Pinned explicitly rather than relying on `lang=` defaults, because in
`paddleocr` 3.7.0 the default recognition model is `PP-OCRv6_medium_rec`
(a 50-language single model) and it is not documented whether Arabic is among
those 50. The Arabic-specific model is documented and dedicated:

- Recognition: `arabic_PP-OCRv5_mobile_rec` (7.6 MB; Arabic, Persian, Uyghur,
  Urdu, Pashto, Kurdish, Sindhi, Balochi, English)
- Detection: `PP-OCRv5_mobile_det` by default on macOS CPU;
  `PP-OCRv5_server_det` selectable via config for accuracy at the cost of speed.

Task 1 of the plan is a smoke test that prints the *resolved* model names and
asserts non-empty output on a committed Arabic fixture, so this assumption is
verified before anything is built on it.

## Right-to-left handling

Two separate concerns, both easy to get wrong:

- **Reading order** — `dt_polys` come back in detection order. Lines are grouped
  into horizontal bands by vertical overlap, bands ordered top→bottom, and lines
  within a band ordered by **descending x** (rightmost first). The resulting
  index is persisted as `reading_order` so the line list and the `.txt` export
  always agree.
- **Rendering** — `rec_texts` are already logical-order Unicode. Strings are
  **never** reversed in code. The UI sets `dir="rtl"` and lets the browser's
  bidi algorithm render them. Manual reversal + bidi = double reversal, which
  looks almost right and is therefore the worst kind of bug.

## Non-goals (explicitly out of scope)

- CUDA / Ubuntu deployment. Only accommodation: device is a single config value
  (`OCR_DEVICE=cpu`), no abstraction layer.
- Authentication, user accounts, multi-reviewer assignment or locking.
- PDF ingestion (images only).
- Layout analysis, tables, key-value extraction (PP-StructureV3).
- Fine-tuning / training-data export. The schema keeps original and corrected
  text separately so this stays possible later, but nothing is built for it now.
- Translation.

## Success criteria

1. `uv run python scripts/smoke_ocr.py` prints Arabic text from the fixture image.
2. Importing a folder of N images enqueues N jobs; all reach `ocr_done` or
   `ocr_failed` with a recorded error; re-importing the same folder adds nothing
   (sha256 idempotency).
3. In the UI, clicking any line row highlights the matching polygon and vice
   versa, at any zoom level.
4. A reviewer can correct a line's text, mark it, mark the image approved, and
   move to the next image without touching the mouse.
5. Export produces JSONL + `.txt` files whose Arabic renders correctly (no
   `\uXXXX` escapes, no BOM, no reversed text).
