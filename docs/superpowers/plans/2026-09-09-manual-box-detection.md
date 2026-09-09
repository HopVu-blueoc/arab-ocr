# Manual Box Detection & Line Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a reviewer draw a box around text the automatic pipeline missed, run recognition on just that region, and insert the result as a new reviewable line in correct reading order; also let them delete a bad line outright.

**Architecture:** Extract the "recognize one perspective-corrected quad" half of `PaddleOcrEngine._rescue` into a reusable `recognize_quad` method, expose it through a new `POST /api/images/{id}/lines/detect-box` endpoint, and add a `DELETE /api/lines/{id}` endpoint — both re-sorting the image's lines through a new `reorder_lines` adapter over the existing `sort_reading_order` algorithm. On the frontend, `ImageCanvas` gains a `drawMode` that swaps panning for a click-drag-drawn rectangle, converted to image-pixel coordinates via `getScreenCTM()`, and POSTs it on release; `LineRow` gains a delete button. No schema changes.

**Tech Stack:** FastAPI + SQLModel (backend), React + `react-zoom-pan-pinch` + `zustand` (frontend), pytest / vitest.

**Spec:** [docs/superpowers/specs/2026-09-09-manual-box-detection-design.md](../specs/2026-09-09-manual-box-detection-design.md)

## Global Constraints

- Minimum manual-box size: **8 image pixels** in each dimension, enforced both client-side (fail fast) and server-side (authoritative) — from the spec's Handler step 2.
- `POST /api/images/{image_id}/lines/detect-box` and `DELETE /api/lines/{line_id}` both return `list[LineOut]` for the whole image's remaining/updated lines, ordered — never the full `ImageDetailOut`.
- No confirmation dialog on line delete (spec Decision 4).
- Manual box detection is unavailable under `OCR_ENGINE=paddle_vl` (no `recognize_quad`); the handler feature-detects with `hasattr` and returns 501, never raises `AttributeError`.
- Backend commands: `uv run pytest` (tests), `uv run ruff check .` (lint) — run from the repo root.
- Frontend commands: `npm run test`, `npm run lint`, `npm run build` (`tsc -b`) — run from `frontend/`.

---

## Task 1: Extract `recognize_quad` from `PaddleOcrEngine._rescue`

**Files:**
- Modify: `app/ocr/paddle_engine.py`

**Interfaces:**
- Produces: `PaddleOcrEngine.recognize_quad(self, image: np.ndarray, polygon: Polygon) -> Candidate | None` — used by Task 4's endpoint.

This task is a pure refactor with no new tests of its own: the existing `tests/test_paddle_engine.py::test_paddle_engine_reads_the_fixture` (marked `slow`, needs real PaddleOCR models) already exercises `_rescue` end-to-end and must keep passing unchanged. Correctness argument for why the refactor preserves behavior: `_rescue` today builds three candidates (`pipeline`, `arabic-rescue`, `latin-rescue`), and only replaces the pipeline's line when the winning candidate (a) isn't `"pipeline"` and (b) beats the pipeline's score by at least `ocr_rescue_min_gain` (`0.15` by default, always `> 0`). If the pipeline candidate would have won `pick_best` (i.e. its score is `>= max(rescue scores)`), then the rescue-only `best.score` computed without the pipeline candidate is `<= line.score < line.score + ocr_rescue_min_gain`, so the gain check alone already discards it — the explicit `best.source == "pipeline"` check becomes redundant once the pipeline candidate is removed from the list passed to `pick_best`, not a behavior change.

- [ ] **Step 1: Read the current file to get exact line numbers for the edit**

Run: `sed -n '1,20p;105,146p' app/ocr/paddle_engine.py`

- [ ] **Step 2: Add the `Polygon` import**

In `app/ocr/paddle_engine.py`, change:
```python
from app.ocr.engine import OcrLine, OcrResult
```
to:
```python
from app.ocr.engine import OcrLine, OcrResult, Polygon
```

- [ ] **Step 3: Add a shared recognizer-builder helper**

Directly above `_rescue` (currently lines 122-145), insert:
```python
    def _build_recognizer(self, model_name: str):
        from paddleocr import TextRecognition

        return TextRecognition(model_name=model_name, device=self.settings.ocr_device, enable_mkldnn=False)
```

- [ ] **Step 4: Use the helper from `warmup`**

In `warmup`, replace:
```python
        if s.ocr_second_pass:
            from paddleocr import TextRecognition

            if self._arabic_rec is None:
                self._arabic_rec = TextRecognition(
                    model_name=s.ocr_rec_model, device=s.ocr_device, enable_mkldnn=False
                )
            if self._latin_rec is None:
                self._latin_rec = TextRecognition(
                    model_name=s.ocr_latin_rec_model, device=s.ocr_device, enable_mkldnn=False
                )
```
with:
```python
        if s.ocr_second_pass:
            if self._arabic_rec is None:
                self._arabic_rec = self._build_recognizer(s.ocr_rec_model)
            if self._latin_rec is None:
                self._latin_rec = self._build_recognizer(s.ocr_latin_rec_model)
```

- [ ] **Step 5: Replace `_rescue` with `recognize_quad` + a thin `_rescue` wrapper**

Replace the whole existing `_rescue` method:
```python
    def _rescue(self, image: np.ndarray, line: OcrLine) -> OcrLine:
        crop = warp_quad(image, line.polygon)
        if crop.size == 0:
            return line

        prepared = preprocess_crop(
            crop,
            target_height=self.settings.ocr_crop_target_height,
            max_upscale=self.settings.ocr_crop_max_upscale,
            clahe=self.settings.ocr_crop_clahe,
        )
        variants = crop_variants(prepared)

        candidates = [
            Candidate(line.text, line.score, "pipeline"),
            self._best_read(self._arabic_rec, variants, "arabic-rescue"),
            self._best_read(self._latin_rec, variants, "latin-rescue"),
        ]
        best = pick_best(candidates)
        if best is None or best.source == "pipeline":
            return line
        if best.score < line.score + self.settings.ocr_rescue_min_gain:
            return line
        return OcrLine(text=best.text, score=best.score, polygon=line.polygon, source=best.source)
```
with:
```python
    def recognize_quad(self, image: np.ndarray, polygon: Polygon) -> Candidate | None:
        """Run both recognisers on one perspective-corrected, upscaled quad.

        Shared by the automatic rescue pass (`_rescue`, below) and the manual
        box-detection endpoint. A manual box has no prior pipeline read to
        compare against, so this returns the raw best candidate and leaves
        "is it good enough to use" to the caller. Builds the recognisers on
        first use regardless of `ocr_second_pass` - that setting only gates
        the automatic pipeline's second pass, not a reviewer's explicit
        request to recognize a region.
        """
        if self._arabic_rec is None:
            self._arabic_rec = self._build_recognizer(self.settings.ocr_rec_model)
        if self._latin_rec is None:
            self._latin_rec = self._build_recognizer(self.settings.ocr_latin_rec_model)

        crop = warp_quad(image, polygon)
        if crop.size == 0:
            return None

        prepared = preprocess_crop(
            crop,
            target_height=self.settings.ocr_crop_target_height,
            max_upscale=self.settings.ocr_crop_max_upscale,
            clahe=self.settings.ocr_crop_clahe,
        )
        variants = crop_variants(prepared)

        candidates = [
            self._best_read(self._arabic_rec, variants, "arabic-rescue"),
            self._best_read(self._latin_rec, variants, "latin-rescue"),
        ]
        return pick_best(candidates)

    def _rescue(self, image: np.ndarray, line: OcrLine) -> OcrLine:
        best = self.recognize_quad(image, line.polygon)
        if best is None:
            return line
        if best.score < line.score + self.settings.ocr_rescue_min_gain:
            return line
        return OcrLine(text=best.text, score=best.score, polygon=line.polygon, source=best.source)
```

- [ ] **Step 6: Run the fast test suite to confirm nothing else broke**

Run: `uv run pytest -q`
Expected: all pass (the slow, real-model test is skipped by default `addopts = "-m 'not slow'"`).

- [ ] **Step 7: Run the slow paddle-engine test manually as a smoke check**

Run: `uv run pytest tests/test_paddle_engine.py -m slow -v`
Expected: PASS, identical assertions to before the refactor (same 4 lines, same order, same text). This confirms the equivalence argument above holds against the real model, not just in theory. (Downloads ~100MB of models on first run — expect it to be slow.)

- [ ] **Step 8: Run lint**

Run: `uv run ruff check app/ocr/paddle_engine.py`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add app/ocr/paddle_engine.py
git commit -m "refactor: extract recognize_quad from PaddleOcrEngine._rescue

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: Add `recognize_quad` to `FakeOcrEngine`

**Files:**
- Modify: `app/ocr/fake_engine.py`

**Interfaces:**
- Consumes: `Candidate` from `app.ocr.crops` (fields `text: str, score: float, source: str`).
- Produces: `FakeOcrEngine(recognize_quad_result: Candidate | None = <default>).recognize_quad(image, polygon) -> Candidate | None`, and `FakeOcrEngine.recognize_quad_calls: list[Polygon]` for tests to assert whether the engine was reached at all. Used by Task 4 and Task 5's tests.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_reading_order.py`... skip — this is a new small test, put it directly in a new file so it's easy to run in isolation. Create `tests/test_fake_engine.py`:
```python
from app.ocr.crops import Candidate
from app.ocr.fake_engine import FakeOcrEngine


def test_recognize_quad_returns_configured_result_and_records_the_call():
    result = Candidate("يدوي", 0.93, "manual")
    engine = FakeOcrEngine(recognize_quad_result=result)

    got = engine.recognize_quad(image=None, polygon=[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])

    assert got is result
    assert engine.recognize_quad_calls == [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]]


def test_recognize_quad_can_be_configured_to_find_nothing():
    engine = FakeOcrEngine(recognize_quad_result=None)
    assert engine.recognize_quad(image=None, polygon=[(0.0, 0.0), (1.0, 1.0)]) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fake_engine.py -v`
Expected: FAIL with `TypeError: FakeOcrEngine.__init__() got an unexpected keyword argument 'recognize_quad_result'`.

- [ ] **Step 3: Implement `recognize_quad` on `FakeOcrEngine`**

Replace the full contents of `app/ocr/fake_engine.py`:
```python
from pathlib import Path

from PIL import Image

from app.ocr.crops import Candidate
from app.ocr.engine import OcrLine, OcrResult, Polygon
from app.ocr.reading_order import sort_reading_order

DEFAULT_LINES = [
    OcrLine("مرحبا بالعالم", 0.98, [(60, 40), (600, 40), (600, 100), (60, 100)]),
    OcrLine("السطر", 0.95, [(440, 160), (820, 160), (820, 220), (440, 220)]),
    OcrLine("الثاني", 0.91, [(60, 160), (420, 160), (420, 220), (60, 220)]),
    OcrLine("اختبار التعرف الضوئي", 0.88, [(60, 280), (700, 280), (700, 340), (60, 340)]),
]

DEFAULT_RECOGNIZE_QUAD_RESULT = Candidate("يدوي", 0.93, "manual")


class FakeOcrEngine:
    """Deterministic engine for tests. Never imports paddle."""

    def __init__(
        self,
        lines: list[OcrLine] | None = None,
        recognize_quad_result: Candidate | None = DEFAULT_RECOGNIZE_QUAD_RESULT,
    ) -> None:
        self.lines = DEFAULT_LINES if lines is None else lines
        self.calls: list[Path] = []
        self.recognize_quad_result = recognize_quad_result
        self.recognize_quad_calls: list[Polygon] = []

    def warmup(self) -> None:
        """No model to build."""

    def run(self, image_path: Path) -> OcrResult:
        self.calls.append(image_path)
        with Image.open(image_path) as img:
            width, height = img.size
        return OcrResult(width=width, height=height, lines=sort_reading_order(self.lines))

    def recognize_quad(self, image, polygon: Polygon) -> Candidate | None:
        self.recognize_quad_calls.append(polygon)
        return self.recognize_quad_result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_fake_engine.py -v`
Expected: PASS

- [ ] **Step 5: Run the full fast suite to confirm the existing engine tests still pass**

Run: `uv run pytest -q`
Expected: all pass — nothing else constructs `FakeOcrEngine(...)` with positional args that would break from the added keyword-only-by-default parameter.

- [ ] **Step 6: Commit**

```bash
git add app/ocr/fake_engine.py tests/test_fake_engine.py
git commit -m "test: add recognize_quad to FakeOcrEngine for manual-box tests

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: Add `reorder_lines` adapter over `sort_reading_order`

**Files:**
- Modify: `app/ocr/reading_order.py`
- Test: `tests/test_reading_order.py`

**Interfaces:**
- Consumes: `Line` from `app.models` (fields used: `polygon: list[list[float]]`, plus whatever other fields the caller already set — untouched); `sort_reading_order` (unchanged, already in this file).
- Produces: `reorder_lines(lines: list[Line]) -> list[Line]` — same `Line` objects, reordered; the caller assigns `reading_order = index` afterward. Used by Task 4 and Task 5's endpoints.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_reading_order.py` (append to the existing file, keep the existing tests and imports):
```python
from app.models import Line
from app.ocr.reading_order import reorder_lines, sort_reading_order  # noqa: F811 (re-import for the new tests' clarity)


def make_line(line_id: int, x0: float, y0: float, w: float = 300, h: float = 60) -> Line:
    return Line(
        id=line_id,
        image_id=1,
        reading_order=0,
        rec_text="x",
        score=0.9,
        polygon=[[x0, y0], [x0 + w, y0], [x0 + w, y0 + h], [x0, y0 + h]],
    )


def test_reorder_lines_applies_the_same_band_rtl_ordering():
    # Same layout as test_rtl_within_band_is_rightmost_first, expressed as Line rows.
    lines = [
        make_line(1, 60, 160),  # left-block
        make_line(2, 60, 280),  # bottom
        make_line(3, 440, 165),  # right-block, same band as left-block
        make_line(4, 60, 40),  # top
    ]
    ordered = reorder_lines(lines)
    assert [ln.id for ln in ordered] == [4, 3, 1, 2]


def test_reorder_lines_returns_the_same_objects():
    original = make_line(1, 60, 40)
    (result,) = reorder_lines([original])
    assert result is original


def test_reorder_lines_empty_input():
    assert reorder_lines([]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_reading_order.py -v`
Expected: FAIL with `ImportError: cannot import name 'reorder_lines' from 'app.ocr.reading_order'`.

- [ ] **Step 3: Implement `reorder_lines`**

Replace the full contents of `app/ocr/reading_order.py`:
```python
from collections.abc import Sequence

from app.models import Line
from app.ocr.engine import OcrLine, bbox


def sort_reading_order(
    lines: Sequence[OcrLine],
    *,
    rtl: bool = True,
    overlap_ratio: float = 0.5,
) -> list[OcrLine]:
    """Order detected lines the way a human reads them.

    Lines are grouped into horizontal bands (vertical overlap >= overlap_ratio of
    the shorter line's height); bands run top to bottom; inside a band lines run
    right to left when rtl is True. Text is never modified.
    """
    bands: list[dict] = []
    for ln in sorted(lines, key=lambda line: bbox(line.polygon)[1]):
        _, y0, _, y1 = bbox(ln.polygon)
        for band in bands:
            overlap = min(y1, band["y1"]) - max(y0, band["y0"])
            shorter = min(y1 - y0, band["y1"] - band["y0"])
            if shorter > 0 and overlap / shorter >= overlap_ratio:
                band["lines"].append(ln)
                band["y0"] = min(band["y0"], y0)
                band["y1"] = max(band["y1"], y1)
                break
        else:
            bands.append({"y0": y0, "y1": y1, "lines": [ln]})

    ordered: list[OcrLine] = []
    for band in bands:
        band["lines"].sort(key=lambda line: bbox(line.polygon)[0], reverse=rtl)
        ordered.extend(band["lines"])
    return ordered


def reorder_lines(lines: list[Line]) -> list[Line]:
    """Adapt DB `Line` rows to `sort_reading_order`'s `OcrLine` view and back.

    Only `polygon` matters for ordering, so `text`/`score` are filled with
    placeholders. Order is recovered by object identity of the wrapper
    objects this function creates itself: `sort_reading_order` reorders its
    input list without ever cloning an element, so each wrapper's identity
    survives the call and maps unambiguously back to the `Line` it came from
    - even if two lines share an identical polygon.
    """
    wrapped = [OcrLine(text="", score=0.0, polygon=[tuple(p) for p in ln.polygon]) for ln in lines]
    by_identity = {id(w): ln for w, ln in zip(wrapped, lines, strict=True)}
    return [by_identity[id(w)] for w in sort_reading_order(wrapped)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_reading_order.py -v`
Expected: PASS (all 7 tests: the original 4 plus the 3 new ones).

- [ ] **Step 5: Run the full fast suite and lint**

Run: `uv run pytest -q && uv run ruff check app/ocr/reading_order.py tests/test_reading_order.py`
Expected: all pass, no lint errors.

- [ ] **Step 6: Commit**

```bash
git add app/ocr/reading_order.py tests/test_reading_order.py
git commit -m "feat: add reorder_lines adapter for re-sorting DB Line rows

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: `POST /api/images/{image_id}/lines/detect-box`

**Files:**
- Modify: `app/schemas.py`
- Modify: `app/routers/images.py`
- Test: `tests/test_api_line_detect_box.py` (new)

**Interfaces:**
- Consumes: `PaddleOcrEngine.recognize_quad` / `FakeOcrEngine.recognize_quad` (Task 1, Task 2) — called only when `hasattr(engine, "recognize_quad")`; `reorder_lines` (Task 3); `app.dispatch.get_engine()` (existing); `app.ocr.crops.load_bgr` (existing); `line_out` (existing, `app/routers/images.py`).
- Produces: `DetectBoxRequest` schema (`app/schemas.py`) and the endpoint itself — the frontend's `detectBox` (Task 6) calls this.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_line_detect_box.py`:
```python
import pytest
from PIL import Image as PILImage

from app import dispatch
from app.ocr.crops import Candidate
from app.ocr.fake_engine import FakeOcrEngine

BOX = [[100.0, 100.0], [300.0, 100.0], [300.0, 150.0], [100.0, 150.0]]


@pytest.fixture
def image_id(client, upload, tmp_path):
    path = tmp_path / "a.png"
    PILImage.new("RGB", (1000, 400), "white").save(path)
    batch, _ = upload("b", [path])
    return client.get(f"/api/batches/{batch['id']}/images").json()[0]["id"]


def test_detect_box_adds_a_new_line_with_the_engines_text(client, image_id, monkeypatch):
    monkeypatch.setattr(
        dispatch, "_engine", FakeOcrEngine(recognize_quad_result=Candidate("يدوي", 0.93, "manual"))
    )

    resp = client.post(f"/api/images/{image_id}/lines/detect-box", json={"polygon": BOX})

    assert resp.status_code == 200
    lines = resp.json()
    added = next(ln for ln in lines if ln["rec_text"] == "يدوي")
    assert added["score"] == 0.93
    assert added["status"] == "unreviewed"
    assert added["corrected_text"] is None
    assert added["polygon"] == BOX
    orders = [ln["reading_order"] for ln in lines]
    assert sorted(orders) == list(range(len(lines)))  # dense, no gaps or duplicates


def test_detect_box_returns_422_and_creates_no_line_when_nothing_found(client, image_id, monkeypatch):
    monkeypatch.setattr(dispatch, "_engine", FakeOcrEngine(recognize_quad_result=None))
    before = client.get(f"/api/images/{image_id}").json()["lines"]

    resp = client.post(f"/api/images/{image_id}/lines/detect-box", json={"polygon": BOX})

    assert resp.status_code == 422
    after = client.get(f"/api/images/{image_id}").json()["lines"]
    assert after == before


def test_detect_box_rejects_a_too_small_box_without_calling_the_engine(client, image_id, monkeypatch):
    engine = FakeOcrEngine(recognize_quad_result=Candidate("x", 0.9, "manual"))
    monkeypatch.setattr(dispatch, "_engine", engine)
    tiny = [[100.0, 100.0], [104.0, 100.0], [104.0, 104.0], [100.0, 104.0]]

    resp = client.post(f"/api/images/{image_id}/lines/detect-box", json={"polygon": tiny})

    assert resp.status_code == 400
    assert engine.recognize_quad_calls == []


class _NoManualBoxEngine:
    def warmup(self) -> None:
        """No model to build."""

    def run(self, image_path):
        raise NotImplementedError


def test_detect_box_returns_501_when_the_engine_has_no_recognize_quad(client, image_id, monkeypatch):
    monkeypatch.setattr(dispatch, "_engine", _NoManualBoxEngine())

    resp = client.post(f"/api/images/{image_id}/lines/detect-box", json={"polygon": BOX})

    assert resp.status_code == 501


def test_detect_box_404_for_an_unknown_image(client):
    resp = client.post("/api/images/9999/lines/detect-box", json={"polygon": BOX})
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api_line_detect_box.py -v`
Expected: FAIL with `404 Not Found` (route doesn't exist yet) on every test.

- [ ] **Step 3: Add the `DetectBoxRequest` schema**

In `app/schemas.py`, add after `LineOut`:
```python
class DetectBoxRequest(BaseModel):
    polygon: list[list[float]]
```

- [ ] **Step 4: Implement the endpoint**

In `app/routers/images.py`:

Change the imports at the top from:
```python
from app.db import SessionDep
from app.dispatch import enqueue_image
from app.models import Image, ImageStatus, Line, utcnow
from app.schemas import ImageDetailOut, ImageOut, ImageUpdate, LineOut
from app.storage import ObjectNotFound, get_storage
```
to:
```python
from app.db import SessionDep
from app.dispatch import enqueue_image, get_engine
from app.models import Image, ImageStatus, Line, utcnow
from app.ocr.crops import load_bgr
from app.ocr.reading_order import reorder_lines
from app.schemas import DetectBoxRequest, ImageDetailOut, ImageOut, ImageUpdate, LineOut
from app.storage import ObjectNotFound, get_storage
```
(add `select` too if not already imported — it already is, per the existing `get_image` handler).

Add, after `retry_image` (end of file):
```python
MIN_MANUAL_BOX_SIDE = 8  # image pixels; a stray click, not a drag


@router.post("/{image_id}/lines/detect-box", response_model=list[LineOut])
def detect_box(image_id: int, payload: DetectBoxRequest, session: SessionDep) -> list[LineOut]:
    image = session.get(Image, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="image not found")

    xs = [p[0] for p in payload.polygon]
    ys = [p[1] for p in payload.polygon]
    if max(xs) - min(xs) < MIN_MANUAL_BOX_SIDE or max(ys) - min(ys) < MIN_MANUAL_BOX_SIDE:
        raise HTTPException(status_code=400, detail="box is too small")

    engine = get_engine()
    if not hasattr(engine, "recognize_quad"):
        raise HTTPException(
            status_code=501,
            detail="manual box detection isn't supported with the paddle_vl engine",
        )

    with get_storage().as_local_path(image.path) as local_path:
        array = load_bgr(local_path)

    polygon = [(float(x), float(y)) for x, y in payload.polygon]
    candidate = engine.recognize_quad(array, polygon)
    if candidate is None:
        raise HTTPException(status_code=422, detail="no text found in that region")

    session.add(
        Line(
            image_id=image.id,
            reading_order=0,  # placeholder; overwritten by the re-sort below
            rec_text=candidate.text,
            score=candidate.score,
            polygon=[[x, y] for x, y in polygon],
        )
    )
    session.commit()

    all_lines = session.exec(select(Line).where(Line.image_id == image_id)).all()
    for index, line in enumerate(reorder_lines(all_lines)):
        line.reading_order = index
        session.add(line)
    session.commit()

    ordered = session.exec(
        select(Line).where(Line.image_id == image_id).order_by(Line.reading_order)
    ).all()
    return [line_out(ln) for ln in ordered]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_api_line_detect_box.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Run the full fast suite and lint**

Run: `uv run pytest -q && uv run ruff check app/schemas.py app/routers/images.py tests/test_api_line_detect_box.py`
Expected: all pass, no lint errors.

- [ ] **Step 7: Commit**

```bash
git add app/schemas.py app/routers/images.py tests/test_api_line_detect_box.py
git commit -m "feat: add POST /api/images/{id}/lines/detect-box endpoint

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: `DELETE /api/lines/{line_id}`

**Files:**
- Modify: `app/routers/lines.py`
- Test: `tests/test_api_lines.py`

**Interfaces:**
- Consumes: `reorder_lines` (Task 3); `line_out` (existing).
- Produces: the endpoint — the frontend's `deleteLine` (Task 6) calls this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_lines.py`:
```python
def test_delete_line_removes_it_and_closes_the_reading_order_gap(client_with_lines):
    client, image = client_with_lines
    lines = image["lines"]
    assert len(lines) >= 2
    victim = lines[0]

    resp = client.delete(f"/api/lines/{victim['id']}")

    assert resp.status_code == 200
    remaining = resp.json()
    assert victim["id"] not in [ln["id"] for ln in remaining]
    assert len(remaining) == len(lines) - 1
    assert [ln["reading_order"] for ln in remaining] == list(range(len(remaining)))


def test_delete_unknown_line_is_404(client_with_lines):
    client, _ = client_with_lines
    assert client.delete("/api/lines/9999").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api_lines.py -v`
Expected: FAIL with `405 Method Not Allowed` on `test_delete_line_removes_it_and_closes_the_reading_order_gap` (no DELETE route yet), and the 404 test also fails since there's no route to 404 through in the expected way (it would currently 405 too).

- [ ] **Step 3: Implement the endpoint**

Replace the full contents of `app/routers/lines.py`:
```python
from fastapi import APIRouter, HTTPException
from sqlmodel import select

from app.db import SessionDep
from app.models import Line, LineStatus, utcnow
from app.ocr.reading_order import reorder_lines
from app.routers.images import line_out
from app.schemas import LineOut, LineUpdate

router = APIRouter(prefix="/api/lines", tags=["lines"])


@router.patch("/{line_id}", response_model=LineOut)
def update_line(line_id: int, payload: LineUpdate, session: SessionDep) -> LineOut:
    line = session.get(Line, line_id)
    if line is None:
        raise HTTPException(status_code=404, detail="line not found")

    fields = payload.model_dump(exclude_unset=True)
    if "corrected_text" in fields:
        text = fields["corrected_text"]
        line.corrected_text = text
        # An explicit status in the same request still wins.
        line.status = LineStatus.unreviewed if text is None else LineStatus.edited
    if fields.get("status") is not None:
        line.status = fields["status"]

    line.updated_at = utcnow()
    session.add(line)
    session.commit()
    session.refresh(line)
    return line_out(line)


@router.delete("/{line_id}", response_model=list[LineOut])
def delete_line(line_id: int, session: SessionDep) -> list[LineOut]:
    line = session.get(Line, line_id)
    if line is None:
        raise HTTPException(status_code=404, detail="line not found")

    image_id = line.image_id
    session.delete(line)
    session.commit()

    remaining = session.exec(select(Line).where(Line.image_id == image_id)).all()
    for index, ln in enumerate(reorder_lines(remaining)):
        ln.reading_order = index
        session.add(ln)
    session.commit()

    ordered = session.exec(
        select(Line).where(Line.image_id == image_id).order_by(Line.reading_order)
    ).all()
    return [line_out(ln) for ln in ordered]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_api_lines.py -v`
Expected: PASS (6 tests: the original 4 plus the 2 new ones).

- [ ] **Step 5: Run the full fast suite and lint**

Run: `uv run pytest -q && uv run ruff check app/routers/lines.py tests/test_api_lines.py`
Expected: all pass, no lint errors.

- [ ] **Step 6: Commit**

```bash
git add app/routers/lines.py tests/test_api_lines.py
git commit -m "feat: add DELETE /api/lines/{id} endpoint

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

Backend work ends here. Tasks 6-11 are frontend; none of them have a Vitest unit test of their own beyond what's specified, because the new logic (SVG pointer math, async wiring) is DOM/interaction-dependent — consistent with this project's existing convention (see `frontend/src/api/lines.test.ts` vs. `LineRow.tsx`'s untested clipboard-triggering code). Task 12 is the manual browser verification that covers this gap, per the spec's Testing section.

---

## Task 6: Frontend API client — `detectBox` and `deleteLine`

**Files:**
- Modify: `frontend/src/api/client.ts`

**Interfaces:**
- Consumes: `json<T>` (existing helper in this file), `LineDto` (existing, `frontend/src/api/types.ts`).
- Produces: `detectBox(imageId: number, polygon: number[][]): Promise<LineDto[]>`, `deleteLine(lineId: number): Promise<LineDto[]>` — used by Task 7 and Task 9.

- [ ] **Step 1: Add the two functions**

In `frontend/src/api/client.ts`, add after `updateLine`:
```typescript
export const detectBox = (imageId: number, polygon: number[][]) =>
  json<LineDto[]>(`/api/images/${imageId}/lines/detect-box`, {
    method: "POST",
    body: JSON.stringify({ polygon }),
  });

export const deleteLine = (lineId: number) =>
  json<LineDto[]>(`/api/lines/${lineId}`, { method: "DELETE" });
```

- [ ] **Step 2: Typecheck and lint**

Run (from `frontend/`): `npm run build && npm run lint`
Expected: no errors. (`detectBox`/`deleteLine` are unused until Tasks 7 and 9 wire them in — `tsc` doesn't flag unused exports, only unused locals, so this passes cleanly on its own.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "feat(frontend): add detectBox and deleteLine API client functions

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 7: `ImageCanvas.tsx` — draw-box interaction

**Files:**
- Modify: `frontend/src/components/ImageCanvas.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `detectBox` (Task 6).
- Produces: `ImageCanvas({ image, drawMode, onLinesChanged })` — new required props `drawMode: boolean` and `onLinesChanged: (lines: LineDto[]) => void`, consumed by Task 10's `ReviewPage`.

This component's core logic (pointer-to-image-pixel conversion via `getScreenCTM()`) needs a live, laid-out SVG element to test meaningfully — per this project's established convention, it's verified manually in Task 12, not unit-tested.

- [ ] **Step 1: Replace `ImageCanvas.tsx`**

Replace the full contents of `frontend/src/components/ImageCanvas.tsx`:
```tsx
import { useRef, useState } from "react";
import { TransformComponent, TransformWrapper } from "react-zoom-pan-pinch";
import { detectBox, imageFileUrl } from "../api/client";
import type { ImageDetailDto, LineDto } from "../api/types";
import { useSelection } from "../store/selection";
import { polygonToPoints } from "./polygon";

const MIN_BOX_SIDE = 8; // image pixels; matches the backend's own minimum

interface DraftBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export function ImageCanvas({
  image,
  drawMode,
  onLinesChanged,
}: {
  image: ImageDetailDto;
  drawMode: boolean;
  onLinesChanged: (lines: LineDto[]) => void;
}) {
  const { selectedId, hoveredId, select, hover } = useSelection();
  const svgRef = useRef<SVGSVGElement>(null);
  const [draft, setDraft] = useState<DraftBox | null>(null);
  const [pending, setPending] = useState(false);
  const [drawError, setDrawError] = useState<string | null>(null);

  // The shared container+viewBox pattern (see the polygon overlay below)
  // means this conversion stays correct through any zoom/pan state without
  // any manual scale arithmetic.
  function toImagePoint(clientX: number, clientY: number): { x: number; y: number } {
    const svg = svgRef.current!;
    const point = svg.createSVGPoint();
    point.x = clientX;
    point.y = clientY;
    const { x, y } = point.matrixTransform(svg.getScreenCTM()!.inverse());
    return {
      x: Math.min(Math.max(x, 0), image.width),
      y: Math.min(Math.max(y, 0), image.height),
    };
  }

  function onPointerDown(e: React.PointerEvent<SVGSVGElement>) {
    if (!drawMode || pending) return;
    const p = toImagePoint(e.clientX, e.clientY);
    setDrawError(null);
    setDraft({ x0: p.x, y0: p.y, x1: p.x, y1: p.y });
  }

  function onPointerMove(e: React.PointerEvent<SVGSVGElement>) {
    if (!draft || pending) return;
    const p = toImagePoint(e.clientX, e.clientY);
    setDraft({ ...draft, x1: p.x, y1: p.y });
  }

  async function onPointerUp() {
    if (!draft) return;
    const x0 = Math.min(draft.x0, draft.x1);
    const y0 = Math.min(draft.y0, draft.y1);
    const x1 = Math.max(draft.x0, draft.x1);
    const y1 = Math.max(draft.y0, draft.y1);

    if (x1 - x0 < MIN_BOX_SIDE || y1 - y0 < MIN_BOX_SIDE) {
      setDraft(null); // a stray click, not a drag - fail fast, save a request
      return;
    }

    setPending(true);
    try {
      const lines = await detectBox(image.id, [
        [x0, y0],
        [x1, y0],
        [x1, y1],
        [x0, y1],
      ]);
      onLinesChanged(lines);
      setDraft(null);
    } catch (err) {
      setDrawError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="canvas-frame">
      <TransformWrapper
        minScale={0.5}
        maxScale={8}
        doubleClick={{ mode: "reset" }}
        wheel={{ step: 0.15 }}
        panning={{ disabled: drawMode }}
      >
        <TransformComponent wrapperClass="canvas-wrapper" contentClass="canvas-content">
          {/* img and svg share this box, so the transform moves them together
              and there is no scale arithmetic anywhere. */}
          <div
            className="canvas-stack"
            style={{ aspectRatio: `${image.width} / ${image.height}` }}
          >
            <img src={imageFileUrl(image.id)} alt={image.filename} className="canvas-img" />
            <svg
              ref={svgRef}
              className={["canvas-svg", drawMode ? "canvas-svg-draw" : ""].join(" ")}
              viewBox={`0 0 ${image.width} ${image.height}`}
              preserveAspectRatio="xMidYMid meet"
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
            >
              {image.lines.map((line) => (
                <polygon
                  key={line.id}
                  points={polygonToPoints(line.polygon)}
                  className={[
                    "box",
                    line.id === selectedId ? "box-selected" : "",
                    line.id === hoveredId ? "box-hovered" : "",
                  ].join(" ")}
                  onClick={() => select(line.id)}
                  onMouseEnter={() => hover(line.id)}
                  onMouseLeave={() => hover(null)}
                />
              ))}
              {draft && (
                <rect
                  x={Math.min(draft.x0, draft.x1)}
                  y={Math.min(draft.y0, draft.y1)}
                  width={Math.abs(draft.x1 - draft.x0)}
                  height={Math.abs(draft.y1 - draft.y0)}
                  className={pending ? "draw-box draw-box-pending" : "draw-box"}
                />
              )}
            </svg>
          </div>
        </TransformComponent>
      </TransformWrapper>
      {drawError && <p className="canvas-error">{drawError}</p>}
    </div>
  );
}
```

- [ ] **Step 2: Add CSS for the draw-box states**

In `frontend/src/styles.css`, add after the `.box-selected` rule:
```css
.canvas-svg-draw { cursor: crosshair; }
.draw-box { fill: rgba(255, 193, 7, 0.12); stroke: #ffc107; stroke-width: 2;
  stroke-dasharray: 6 4; vector-effect: non-scaling-stroke; pointer-events: none; }
.draw-box-pending { stroke-dasharray: none; }
.canvas-error { margin: 6px 0 0; padding: 4px 8px; font-size: 12px; color: #ff6b6b; }
```

- [ ] **Step 3: Typecheck and lint**

Run (from `frontend/`): `npm run build && npm run lint`
Expected: `tsc -b` will still fail here because `ReviewPage.tsx` (Task 10) doesn't yet pass `drawMode`/`onLinesChanged` — that's expected at this point in the plan. If your workflow requires each task's build to pass standalone, do Task 10's `ReviewPage.tsx` wiring in the same commit as this task instead of splitting them; otherwise proceed to Task 8-10 first and typecheck once after Task 10.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ImageCanvas.tsx frontend/src/styles.css
git commit -m "feat(frontend): draw-box interaction on the review canvas

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 8: `Toolbar.tsx` — draw-box toggle button

**Files:**
- Modify: `frontend/src/components/Toolbar.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Produces: `Toolbar` gains required props `drawMode: boolean` and `onToggleDrawMode: () => void`, consumed by Task 10's `ReviewPage`.

- [ ] **Step 1: Replace `Toolbar.tsx`**

Replace the full contents of `frontend/src/components/Toolbar.tsx`:
```tsx
import { useState } from "react";
import { copyToClipboard, joinFinalText } from "../api/lines";
import type { ImageDetailDto } from "../api/types";

export function Toolbar({
  image,
  onApprove,
  onNext,
  onRetry,
  drawMode,
  onToggleDrawMode,
}: {
  image: ImageDetailDto;
  onApprove: () => void;
  onNext: () => void;
  onRetry: () => void;
  drawMode: boolean;
  onToggleDrawMode: () => void;
}) {
  const reviewed = image.lines.filter((l) => l.status !== "unreviewed").length;
  const failed = image.status === "failed";
  const [copiedAll, setCopiedAll] = useState(false);
  const [copyAllError, setCopyAllError] = useState<string | null>(null);

  async function copyAll() {
    try {
      await copyToClipboard(joinFinalText(image.lines));
      setCopyAllError(null);
      setCopiedAll(true);
      setTimeout(() => setCopiedAll(false), 1200);
    } catch (err) {
      setCopyAllError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <header className="toolbar">
      <strong className="toolbar-filename" title={image.filename}>
        {image.filename}
      </strong>
      {failed ? (
        <span className="toolbar-error">OCR failed: {image.error ?? "unknown error"}</span>
      ) : (
        <span className="muted">
          {reviewed}/{image.lines.length} lines touched · {image.status}
        </span>
      )}
      <div className="toolbar-actions">
        {failed && (
          <button className="toolbar-retry" onClick={onRetry}>
            Retry OCR
          </button>
        )}
        <button
          onClick={onToggleDrawMode}
          className={drawMode ? "toolbar-drawing" : undefined}
          title="Draw a box around text the pipeline missed"
        >
          {drawMode ? "Drawing… (click to stop)" : "Draw box"}
        </button>
        <button
          onClick={copyAll}
          disabled={image.lines.length === 0}
          title={copyAllError ?? undefined}
        >
          {copiedAll ? "Copied ✓" : copyAllError ? "Copy failed ⚠" : "Copy all text"}
        </button>
        <button onClick={onApprove} disabled={image.status === "approved"}>
          Approve image (A)
        </button>
        <button onClick={onNext}>Next (n)</button>
      </div>
      <span className="muted">j/k move · a approve line · Enter edit</span>
    </header>
  );
}
```

- [ ] **Step 2: Add CSS for the active drawing state**

In `frontend/src/styles.css`, add after `.toolbar-retry`:
```css
.toolbar-drawing { border-color: #ffc107; color: #ffc107; }
```

- [ ] **Step 3: Typecheck and lint**

Run (from `frontend/`): `npm run lint`
Expected: no errors (`tsc -b` still fails until Task 10 supplies the new props from `ReviewPage`, same note as Task 7 Step 3).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Toolbar.tsx frontend/src/styles.css
git commit -m "feat(frontend): add draw-box toggle button to the toolbar

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 9: `LineRow.tsx` / `LineList.tsx` — delete a line

**Files:**
- Modify: `frontend/src/components/LineRow.tsx`
- Modify: `frontend/src/components/LineList.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `deleteLine` (Task 6).
- Produces: `LineList` and `LineRow` gain a required `onDelete: (lines: LineDto[]) => void` prop, consumed by Task 10's `ReviewPage`.

- [ ] **Step 1: Replace `LineRow.tsx`**

Replace the full contents of `frontend/src/components/LineRow.tsx`:
```tsx
import { useEffect, useRef, useState } from "react";
import { deleteLine, updateLine } from "../api/client";
import { copyToClipboard } from "../api/lines";
import type { LineDto } from "../api/types";
import { useSelection } from "../store/selection";

export function LineRow({
  line,
  onChange,
  onDelete,
}: {
  line: LineDto;
  onChange: (line: LineDto) => void;
  onDelete: (lines: LineDto[]) => void;
}) {
  const { selectedId, hoveredId, select, hover } = useSelection();
  const ref = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState(line.final_text);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const isSelected = line.id === selectedId;

  async function copyText() {
    try {
      await copyToClipboard(line.final_text);
      setCopyError(null);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch (err) {
      setCopyError(err instanceof Error ? err.message : String(err));
    }
  }

  // A silently dropped save loses the reviewer's correction, so every write
  // reports failure in the row itself.
  async function save(patch: { corrected_text?: string | null; status?: LineDto["status"] }) {
    setSaving(true);
    setSaveError(null);
    try {
      onChange(await updateLine(line.id, patch));
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    setDeleting(true);
    setDeleteError(null);
    try {
      onDelete(await deleteLine(line.id));
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : String(err));
      setDeleting(false);
    }
    // No `finally` reset of `deleting` on success: the row unmounts as soon
    // as `onDelete` swaps `image.lines`, so there is nothing left to update.
  }

  useEffect(() => setDraft(line.final_text), [line.id, line.final_text]);
  useEffect(() => {
    if (isSelected) ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [isSelected]);

  async function commit() {
    if (draft === line.final_text) return;
    await save({ corrected_text: draft === line.rec_text ? null : draft });
  }

  async function approve() {
    await save({ status: "approved" });
  }

  async function revert() {
    setDraft(line.rec_text);
    await save({ corrected_text: null });
  }

  return (
    <div
      ref={ref}
      className={[
        "line-row",
        `line-status-${line.status}`,
        saveError ? "line-save-failed" : "",
        isSelected ? "line-selected" : "",
        line.id === hoveredId ? "line-hovered" : "",
      ].join(" ")}
      onMouseEnter={() => hover(line.id)}
      onMouseLeave={() => hover(null)}
      onClick={() => select(line.id)}
    >
      <span className="line-index">{line.reading_order + 1}</span>
      <input
        className="line-text"
        dir="rtl"
        lang="ar"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onFocus={() => select(line.id)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            (e.target as HTMLInputElement).blur();
          }
          if (e.key === "Escape") setDraft(line.final_text);
        }}
      />
      <span className="line-score" title={saveError ?? `confidence ${line.score}`}>
        {saving ? "…" : saveError ? "⚠ unsaved" : line.score.toFixed(2)}
      </span>
      <div className="line-actions">
        <button onClick={copyText} title={copyError ?? "Copy this line's text"}>
          {copied ? "✓" : copyError ? "⚠" : "📋"}
        </button>
        <button onClick={approve} title="Mark this line correct">
          ✓
        </button>
        <button onClick={revert} disabled={line.corrected_text === null} title="Restore OCR text">
          ⟲
        </button>
        <button onClick={remove} disabled={deleting} title={deleteError ?? "Delete this line"}>
          {deleteError ? "⚠" : "🗑"}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Thread `onDelete` through `LineList.tsx`**

Replace the full contents of `frontend/src/components/LineList.tsx`:
```tsx
import type { LineDto } from "../api/types";
import { LineRow } from "./LineRow";

export function LineList({
  lines,
  onChange,
  onDelete,
}: {
  lines: LineDto[];
  onChange: (line: LineDto) => void;
  onDelete: (lines: LineDto[]) => void;
}) {
  if (lines.length === 0) return <p className="empty">No text detected.</p>;
  return (
    <div className="line-list">
      {lines.map((line) => (
        <LineRow key={line.id} line={line} onChange={onChange} onDelete={onDelete} />
      ))}
    </div>
  );
}
```

- [ ] **Step 3: Add CSS for the delete button's error state**

In `frontend/src/styles.css`, add after `.line-save-failed .line-score`:
```css
.line-actions button:disabled { opacity: 0.5; cursor: default; }
```

- [ ] **Step 4: Typecheck and lint**

Run (from `frontend/`): `npm run lint`
Expected: no errors (`tsc -b` still fails until Task 10, same note as Task 7/8).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/LineRow.tsx frontend/src/components/LineList.tsx frontend/src/styles.css
git commit -m "feat(frontend): add delete button to each line row

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 10: `ReviewPage.tsx` — wire it all together

**Files:**
- Modify: `frontend/src/pages/ReviewPage.tsx`

**Interfaces:**
- Consumes: `ImageCanvas` (Task 7: `drawMode`, `onLinesChanged` props), `Toolbar` (Task 8: `drawMode`, `onToggleDrawMode` props), `LineList` (Task 9: `onDelete` prop).

- [ ] **Step 1: Replace `ReviewPage.tsx`**

Replace the full contents of `frontend/src/pages/ReviewPage.tsx`:
```tsx
import { useEffect, useState } from "react";
import { getImage, retryImage, updateImageStatus, updateLine } from "../api/client";
import { REVIEW_POLL_MS, isProcessing } from "../api/status";
import type { ImageDetailDto, LineDto } from "../api/types";
import { ImageCanvas } from "../components/ImageCanvas";
import { LineCrop } from "../components/LineCrop";
import { LineList } from "../components/LineList";
import { Toolbar } from "../components/Toolbar";
import { useReviewKeys } from "../hooks/useReviewKeys";
import { useSelection } from "../store/selection";

export function ReviewPage({
  imageId,
  onNextImage,
}: {
  imageId: number;
  onNextImage: () => void;
}) {
  const [image, setImage] = useState<ImageDetailDto | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drawMode, setDrawMode] = useState(false);
  const { selectedId, select } = useSelection();

  useEffect(() => {
    let cancelled = false;
    setImage(null);
    setError(null);
    select(null);
    getImage(imageId)
      .then((img) => {
        if (!cancelled) setImage(img);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [imageId, select]);

  // OCR is often still queued or running when the reviewer clicks an image,
  // and the fetch above would then be the only one - which is why the old UI
  // needed an F5. Re-fetch until the status is terminal, then stop.
  useEffect(() => {
    if (!isProcessing(image?.status)) return;
    let cancelled = false;
    const timer = setInterval(() => {
      getImage(imageId)
        .then((img) => {
          if (!cancelled) setImage(img);
        })
        .catch(() => {}); // a transient failure just means the next tick retries
    }, REVIEW_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [imageId, image?.status]);

  // Reset draw mode on every image switch - a leftover "drawing" state from
  // the previous image would otherwise silently disable panning on the next
  // one until the reviewer notices and toggles it off by hand.
  useEffect(() => setDrawMode(false), [imageId]);

  const replaceLine = (updated: LineDto) =>
    setImage((prev) =>
      prev === null
        ? prev
        : { ...prev, lines: prev.lines.map((l) => (l.id === updated.id ? updated : l)) },
    );

  const replaceLines = (lines: LineDto[]) =>
    setImage((prev) => (prev === null ? prev : { ...prev, lines }));

  async function approveImage() {
    if (image === null) return;
    const updated = await updateImageStatus(image.id, "approved");
    setImage((prev) => (prev === null ? prev : { ...prev, status: updated.status }));
    onNextImage();
  }

  async function retryOcr() {
    if (image === null) return;
    try {
      // Sets status back to "queued" - the poll effect above (keyed on
      // image?.status) picks that up on its own and starts refreshing
      // again, the same path a fresh upload takes.
      const updated = await retryImage(image.id);
      setImage((prev) =>
        prev === null ? prev : { ...prev, status: updated.status, error: updated.error },
      );
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err));
    }
  }

  useReviewKeys({
    lines: image?.lines ?? [],
    selectedId,
    select,
    onApproveLine: async (lineId) => replaceLine(await updateLine(lineId, { status: "approved" })),
    onApproveImage: approveImage,
    onNextImage,
  });

  if (error) return <p className="error">{error}</p>;
  if (!image) return <p className="empty">Loading…</p>;

  return (
    <div className="review-shell">
      <p className="processing-note" hidden={!isProcessing(image.status)}>
        OCR running — this view updates itself.
      </p>
      <Toolbar
        image={image}
        onApprove={approveImage}
        onNext={onNextImage}
        onRetry={retryOcr}
        drawMode={drawMode}
        onToggleDrawMode={() => setDrawMode((d) => !d)}
      />
      <div className="review-split">
        <section className="pane pane-image">
          <ImageCanvas image={image} drawMode={drawMode} onLinesChanged={replaceLines} />
        </section>
        <section className="pane pane-text">
          <LineCrop image={image} line={image.lines.find((l) => l.id === selectedId) ?? null} />
          <LineList lines={image.lines} onChange={replaceLine} onDelete={replaceLines} />
        </section>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck, lint, and run the existing test suite**

Run (from `frontend/`): `npm run build && npm run lint && npm run test`
Expected: all pass. This is the first point since Task 7 where `tsc -b` can succeed end-to-end, since every prop `ImageCanvas`/`Toolbar`/`LineList` now require is finally supplied.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/ReviewPage.tsx
git commit -m "feat(frontend): wire draw-box mode and line delete into ReviewPage

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 11: Manual browser verification

**Files:** none (verification only)

This is the DOM-dependent verification the spec's Testing section calls for in place of a unit test, covering both new interactions end to end against a running dev server.

- [ ] **Step 1: Start the backend and frontend dev servers**

Run: `uv run uvicorn app.main:create_app --factory --reload` (backend) and, in `frontend/`, `npm run dev` (frontend). Upload a test image with some text left undetected, or crop an existing reviewed image's box out of frame in a test fixture if easier.

- [ ] **Step 2: Verify the draw-box happy path**

In the browser: open a reviewed image, click "Draw box", drag a rectangle around a patch of missed text (or any text-bearing patch, for verification purposes), release. Confirm: panning is disabled while "Draw box" is active (dragging draws, not pans); the dashed rectangle appears while dragging and turns solid/pending on release; a new line appears in the line list, positioned by reading order (not always last); the drawn rectangle's outline matches the new line's box in `ImageCanvas`.

- [ ] **Step 3: Verify the draw-box error paths**

Drag a box smaller than ~8 screen-equivalent image pixels: confirm no network request fires (check the browser's network tab) and the rectangle just disappears. Draw a box over blank background (no text): confirm a 422 surfaces as the inline `.canvas-error` notice, the drawn rectangle is removed, and the review session is otherwise undisturbed (existing lines, selection, etc. unchanged).

- [ ] **Step 4: Verify zoom/pan still work normally with "Draw box" off**

Confirm the existing pinch/scroll-zoom and click-and-drag-to-pan behavior is unaffected when "Draw box" is not active, and that toggling it on mid-session doesn't leave panning disabled after toggling back off.

- [ ] **Step 5: Verify line delete**

Click the 🗑 button on a line: confirm it disappears immediately with no confirmation dialog, and the remaining lines' displayed position numbers (`line-index`, `reading_order + 1`) close the gap with no skipped number.

- [ ] **Step 6: Verify the draw-mode reset on image switch**

With "Draw box" active, click "Next (n)" to move to another image: confirm draw mode turns itself off (per Task 10 Step 1's `useEffect`) rather than silently carrying over and leaving panning disabled on the next image.

No commit for this task — it's a verification checkpoint, not a code change.

---

## Self-Review Notes

**Spec coverage:** Trigger (toggle button, Task 8) ✓; recognition mode (one line per box, recognition-only via `recognize_quad`, Tasks 1/4) ✓; reading order (re-sort via `reorder_lines`, Tasks 3/4/5) ✓; delete (real deletion + re-sort, no confirm, Task 5/9) ✓; `recognize_quad` extraction (Task 1) ✓; `POST .../detect-box` full 9-step handler (Task 4, steps map 1:1 to the spec's numbered list) ✓; `reorder_lines` adapter (Task 3) ✓; `DELETE /api/lines/{id}` (Task 5) ✓; frontend drawing (`drawMode`, pointer handlers, `getScreenCTM()`, live rect, 8px client-side check, `panning.disabled`, Task 7) ✓; frontend delete (Task 9) ✓; error handling (422 inline notice + rect removal, degenerate box never hits the network, network/server failure pattern matching `LineRow`'s existing save-failure style, Tasks 4/5/7/9) ✓; `FakeOcrEngine.recognize_quad` (Task 2) ✓; out-of-bounds quad tolerance (no code change needed - `cv2.warpPerspective` already handles it per the spec's Consequences section, and client-side clamping in Task 7 keeps it rare) ✓.

**Placeholder scan:** no TBD/TODO; every step has full runnable code or an exact shell command with expected output.

**Type consistency:** `ImageCanvas`'s `onLinesChanged: (lines: LineDto[]) => void` (Task 7) matches `LineList`'s `onDelete: (lines: LineDto[]) => void` (Task 9) and both are satisfied by the same `replaceLines` function in `ReviewPage` (Task 10) — deliberately named differently per-prop (`onLinesChanged` vs `onDelete`) since they're semantically distinct events even though today's implementation happens to handle both identically. `detectBox`/`deleteLine` (Task 6) both return `Promise<LineDto[]>`, matching what `ImageCanvas` and `LineRow` await. Backend `list[LineOut]` response models (Tasks 4, 5) match the frontend's `LineDto[]` (`types.ts` already defines this shape, unchanged). `PaddleOcrEngine.recognize_quad` (Task 1) and `FakeOcrEngine.recognize_quad` (Task 2) share the exact signature `(self, image, polygon: Polygon) -> Candidate | None`, satisfying the `hasattr(engine, "recognize_quad")` duck-typing check in Task 4.
