# Spec: Manual Box Detection & Line Delete

**Date:** 2026-09-09

## Problem

The automatic OCR pipeline sometimes misses text entirely — a line the
detector never boxes at all, so there's no `Line` row to correct, only a
gap in the review UI with nothing to click on. The reviewer's only
recourse today is to note it and move on; the missed text never enters the
review/export pipeline.

> "As a user, some text may not be detected, i need an ability to draw a
> box around this text and run detect on this text only."

A second, direct consequence surfaced during design: there is currently no
way to delete a `Line` row at all (only edit its text or approve it). A
manually-drawn box that produces a bad or duplicate recognition would
otherwise leave a permanent junk line with no recourse — so this spec
includes line deletion as a required companion, not an optional extra.

## Scope

**In scope:** drawing a box on the review canvas, running recognition on
just that region, inserting the result as a new reviewable line in correct
reading order, and deleting a line.

**Out of scope:** multi-line boxes (one box always produces at most one
line — for a missed paragraph, the reviewer draws one box per line),
redrawing/resizing an existing box, undo (delete-and-redraw covers it),
and any change to the automatic pipeline itself.

## Decisions (from brainstorming)

1. **Trigger**: a "Draw box" toggle button in the toolbar. While active,
   dragging the canvas draws a rectangle instead of panning.
2. **Recognition mode**: one line per box, recognition-only (no detection
   stage) — reuses the existing rescue-pass primitives
   (`app/ocr/crops.py`, the dual Arabic/Latin recognizer pattern already
   in `PaddleOcrEngine`), not the full detect+recognize pipeline.
3. **Reading order**: inserting a line re-sorts the whole list through the
   same band algorithm already used at initial OCR
   (`app/ocr/reading_order.py::sort_reading_order`). Existing lines keep
   their text/status/corrections; only their displayed position number can
   change.
4. **Delete**: real deletion, then the same re-sort. No confirmation
   dialog — a single line is low-stakes and trivially replaced by
   redrawing, unlike batch delete (which destroys real review work and
   does confirm).

## Architecture

No schema changes. `Line` already has every column this needs
(`polygon`, `rec_text`, `score`, `status`, `reading_order`). Two new
endpoints, one new frontend interaction mode, and one extraction of
existing OCR logic into a form both the automatic pipeline and this
feature can call.

### Backend: recognizing one manually-drawn region

`PaddleOcrEngine._rescue` already does almost exactly this — perspective-
correct a quad, upscale/contrast-stretch it, run both recognizers, keep
the best score — just triggered by a *low-confidence pipeline line*
instead of *a manually-drawn box with no prior OCR line at all*. Rather
than duplicate that logic, extract the "recognize one quad" half of it
into a standalone function both call:

```python
# app/ocr/paddle_engine.py
def recognize_quad(self, image: np.ndarray, polygon: Polygon) -> Candidate | None:
    """Run both recognizers on one perspective-corrected, upscaled quad.
    Returns None if neither recognizer found readable content."""
```

`_rescue` becomes a thin wrapper: call `recognize_quad`, compare against
the pipeline's own line, apply `ocr_rescue_min_gain`. The new endpoint
calls `recognize_quad` directly — there's no existing pipeline score to
beat, any readable result is worth keeping.

### Backend: `POST /api/images/{image_id}/lines/detect-box`

Request:
```json
{"polygon": [[120.0, 340.0], [480.0, 340.0], [480.0, 400.0], [120.0, 400.0]]}
```
Four `[x, y]` points in full-image pixel space (same convention as the
existing `Line.polygon`), any order — `order_quad` (already in
`app/ocr/crops.py`) normalizes it, so a quad drawn for slightly slanted
text works too, not just axis-aligned rectangles.

`recognize_quad` is specific to `PaddleOcrEngine`'s two-stage design (a
separate crop-and-recognize step over an already-detected region) — it
has no natural equivalent under `PaddleOcrVLEngine`, which does detection
and recognition jointly in one pass over the whole image. Rather than
force a fake implementation onto an engine it doesn't fit, this feature
is simply unavailable under `OCR_ENGINE=paddle_vl`, which is already
documented as experimental/not-for-production — the handler checks for
the method's presence before calling it.

Handler:
1. 404 if the image doesn't exist.
2. 400 if the polygon's bounding box is smaller than a minimum
   (`8` image pixels either dimension) — a stray click, not a drag.
3. 501 if the current engine has no `recognize_quad` (i.e.
   `OCR_ENGINE=paddle_vl`), `{"detail": "manual box detection isn't
   supported with the paddle_vl engine"}`.
4. Load the image via `get_storage().as_local_path(image.path)` (same
   call `run_ocr_for_image` already uses).
5. Call `engine.recognize_quad(image_array, polygon)`.
6. If it returns `None` (neither recognizer found readable content):
   **422**, `{"detail": "no text found in that region"}`. This is a
   client-correctable input — draw a tighter or different box — not a
   missing resource, so 422 fits better than 404 here, and no `Line` row
   is created.
7. Otherwise: create a new `Line` (`rec_text` = the candidate's text,
   `score` = its score, `polygon` = the *original drawn polygon* in
   full-image coordinates — not the warped crop's coordinates, `status =
   unreviewed`, `corrected_text = None`).
8. Re-sort: load all `Line` rows for the image (including the new one),
   run them through `sort_reading_order` (needs a thin adapter — see
   below), write back each row's `reading_order`, commit.
9. Return `list[LineOut]` for the whole image, ordered — **not** the full
   `ImageDetailOut`; the image's own fields (status, dimensions) don't
   change, only its lines do, so the response matches exactly what the
   frontend needs to replace.

**Reading-order adapter**: `sort_reading_order` takes `Sequence[OcrLine]`
(the OCR-engine dataclass) and returns the same type reordered — it has
no idea about database rows. A small conversion function, `app/ocr/
reading_order.py::reorder_lines(lines: list[Line]) -> list[Line]`,
wraps each `Line` into a transient `OcrLine`-shaped view (text/score
don't matter for ordering, only `polygon`), calls `sort_reading_order`,
and returns the `Line` objects in the resulting order so the caller can
assign `reading_order = index`. This keeps `sort_reading_order` itself
untouched (it's already tested, already used at initial-OCR time) and
confines the DB-row-shaped glue to one new, separately-tested function.

### Backend: `DELETE /api/lines/{line_id}`

1. 404 if the line doesn't exist.
2. Delete it, then re-sort the *remaining* siblings the same way (closes
   the gap — 1,2,3,5 becomes 1,2,3,4 rather than leaving a hole).
3. Return `list[LineOut]` for the remaining lines of that image — same
   response shape as detect-box, same reason.

### Frontend: drawing

`ImageCanvas.tsx` gains a `drawMode` prop (lifted to `ReviewPage`, toggled
by a new toolbar button) and pointer handlers on the `<svg>`:

- `pointerdown` (only when `drawMode`): record the start point, converted
  from client to SVG-viewBox (= image-pixel) coordinates via
  `svg.getScreenCTM()!.inverse()` — the standard matrix-based conversion,
  correct through any current zoom/pan state automatically, no manual
  scale arithmetic (matching this project's existing approach: the
  polygon overlay already relies on shared container+viewBox for exactly
  this reason).
- `pointermove`: update a live rectangle (rendered as a dashed `<rect>` in
  the same SVG, so it scales/pans identically to everything else).
- `pointerup`: convert the end point the same way, clamp both corners to
  `[0, width] × [0, height]`, reject client-side if the resulting box is
  smaller than the same 8px minimum the backend enforces (fail fast,
  save a request), otherwise POST the four corners to `detect-box` and
  replace `image.lines` with the response on success. A brief pending
  state on the drawn rectangle (e.g. dashed → solid, or a small spinner)
  covers the request's latency — this is a synchronous recognition call,
  no polling needed, unlike full-image OCR.

While `drawMode` is active, `TransformWrapper`'s `panning.disabled` is set
`true` so a drag draws instead of pans; toggling `drawMode` off restores
normal pan/zoom/click-to-select behavior exactly as it is today.

### Frontend: delete

A small delete icon in `LineRow.tsx`'s `.line-actions`, alongside the
existing copy/approve/revert buttons. `onClick` calls `DELETE
/api/lines/{id}`, then replaces `image.lines` with the response — no
confirmation dialog, per the decision above.

### Error handling

- No text found in the drawn box → 422, surfaced as an inline notice near
  the canvas (not a full-page error — the review session continues
  normally), and the drawn rectangle is removed.
- A degenerate box (near-zero drag) never reaches the network — rejected
  client-side.
- Network/server failure on either endpoint → the existing per-action
  error-surfacing pattern already used elsewhere in this app (e.g.
  `LineRow`'s save-failure indicator): show the error, leave state
  unchanged, let the reviewer retry.

## Testing

- `tests/test_reading_order.py` (existing file): `reorder_lines` against
  a small set of `Line`-shaped rows, confirming
  correct band/RTL ordering and that `reading_order` values come back
  dense (`0..n-1`).
- `tests/test_api_line_detect_box.py`: POST a box against the
  `FakeOcrEngine`-backed test client, assert a new line appears with
  correct `reading_order` relative to existing lines; a
  `recognize_quad`-returns-`None` fake asserts the 422 path and that no
  `Line` row was created; a too-small polygon asserts 400 with no engine
  call at all.
- `tests/test_api_lines.py` (existing file): add delete-line tests —
  delete removes the row, remaining lines' `reading_order` closes the
  gap, deleting an unknown id is 404.
- Frontend: the coordinate-conversion logic is DOM-dependent (needs a
  live SVG element's CTM), so per this project's established convention
  it's verified manually in the browser rather than unit-tested; `oxlint`
  and `tsc` cover the rest.

## Consequences accepted

- `FakeOcrEngine` (used by the test suite) has no `recognize_quad` method
  today — it will need one, returning a deterministic candidate, so the
  new endpoint's tests don't depend on real PaddleOCR.
- A drawn box that extends outside the image's own bounds is not
  rejected — `cv2.warpPerspective` samples out-of-range source
  coordinates as black pixels rather than erroring, degrading that
  crop's recognition quality but never crashing. Client-side clamping to
  image bounds keeps this the rare case, not something to special-case
  further.
