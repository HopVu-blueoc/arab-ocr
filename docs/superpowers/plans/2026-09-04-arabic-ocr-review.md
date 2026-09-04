# Arabic OCR + Review Tool — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local macOS app that OCRs a large folder of Arabic images with PaddleOCR and gives a reviewer a side-by-side view — original image left, editable line-by-line text right — where every text line is linked to its polygon on the image.

**Architecture:** Three processes on localhost. A **FastAPI** backend owns a **SQLite** (WAL) database of batches / images / lines and serves image bytes + JSON. A **Celery** worker (Redis broker) runs PaddleOCR with the model loaded once per worker process. A **React + TypeScript (Vite)** SPA renders the reviewer view. The OCR engine sits behind a narrow `OcrEngine` protocol so every backend test runs against a fake — no model download needed to run the suite.

**Tech Stack:** Python 3.12 · uv · PaddleOCR 3.7 (`arabic_PP-OCRv5_mobile_rec`) · paddlepaddle 3.3.1 (CPU) · FastAPI · SQLModel/SQLite · Celery + Redis · React 19 + TypeScript + Vite · pytest · vitest

**Spec:** [docs/superpowers/specs/2026-09-04-arabic-ocr-review.md](../specs/2026-09-04-arabic-ocr-review.md)

## Global Constraints

Every task's requirements implicitly include this section.

- **Python is pinned to 3.12.** `.python-version` must contain `3.12`. paddlepaddle 3.3.1 ships macOS arm64 wheels only for cp39–cp313; the system Python here is 3.14.3, so an unpinned `uv init` produces an environment where paddle cannot install at all.
- **Install paddle from plain PyPI**: `uv add paddleocr paddlepaddle`. Do **not** add the `https://www.paddleocr.ai/packages/stable/cpu/` index — that mirror is for `paddlepaddle-gpu` (the out-of-scope Ubuntu path).
- **OCR models are pinned explicitly, never left to `lang=` defaults**: `ocr_version="PP-OCRv5"`, `text_recognition_model_name="arabic_PP-OCRv5_mobile_rec"`, `text_detection_model_name="PP-OCRv5_mobile_det"`. In paddleocr 3.7.0 the default rec model is `PP-OCRv6_medium_rec`, whose 50-language list does not document Arabic.
- **Never reverse Arabic strings in code.** `rec_texts` are logical-order Unicode. RTL rendering is `dir="rtl"` plus the browser's bidi algorithm. Reversal + bidi = double reversal.
- **All text output is UTF-8 without BOM**, and every `json.dump`/`json.dumps` uses `ensure_ascii=False`.
- **Device is one config value** (`OCR_DEVICE`, default `cpu`). No device abstraction layer, no CUDA branches.
- **SQLite runs in WAL** with `PRAGMA busy_timeout=5000`. Worker transactions commit once per image, never per line.
- **`rec_text` is immutable.** Reviewer edits go in the separate nullable `corrected_text` column.
- **Tests must not download models.** Anything touching real PaddleOCR is marked `@pytest.mark.slow` and excluded from the default run.
- Repo root is `/Users/quanghop/Documents/Playground_2/Arab-ocr`. All paths below are relative to it.

## File Structure

```
pyproject.toml              uv project: deps, pytest config, ruff config
.python-version             "3.12"
.env.example                documented settings
app/
  config.py                 pydantic-settings Settings + get_settings()
  db.py                     engine, WAL pragmas, session helpers, init_db()
  models.py                 SQLModel tables: Batch, Image, Line + status enums
  schemas.py                API request/response models
  main.py                   FastAPI app, CORS, router registration
  dispatch.py               enqueue_image(): inline or celery, per settings
  service.py                run_ocr_for_image(), import_folder()
  exporters.py              export_jsonl(), export_txt()
  routers/
    batches.py              POST/GET batches, export endpoint
    images.py               GET image + lines, GET file bytes, PATCH status
    lines.py                PATCH line text/status
  ocr/
    engine.py               OcrLine/OcrResult dataclasses, OcrEngine protocol
    reading_order.py        RTL band-sorting
    paddle_engine.py        PaddleOcrEngine (real)
    fake_engine.py          FakeOcrEngine (tests, fixtures)
  worker/
    celery_app.py           Celery instance + worker_process_init model load
    tasks.py                ocr_image task
scripts/
  make_fixture.py           generates tests/fixtures/arabic_sample.png
  smoke_ocr.py              real-Paddle smoke test, prints resolved models
tests/
  fixtures/arabic_sample.png
  test_reading_order.py  test_models.py  test_api_*.py  test_exporters.py
  test_paddle_engine.py   (slow)
frontend/
  vite.config.ts            dev proxy /api -> :8000
  src/
    api/client.ts           typed fetch wrappers
    api/types.ts            mirrors app/schemas.py
    store/selection.ts      selected/hovered line id (zustand)
    components/ImageCanvas.tsx  image + SVG polygon overlay (+ zoom/pan)
    components/LineList.tsx     scrollable rows
    components/LineRow.tsx      one editable RTL row
    components/BatchList.tsx    batch picker + progress
    components/Toolbar.tsx      status, approve, export, nav
    pages/ReviewPage.tsx        side-by-side layout
    styles.css
data/                       gitignored: app.db, images/, exports/
```

Each backend module has one responsibility and stays small enough to hold in
context; routers are split per resource, and the OCR engine is isolated behind
`app/ocr/engine.py` so the rest of the codebase never imports paddle.

---

### Task 1: Project scaffold + real-PaddleOCR smoke test

Verifies the load-bearing assumption (Arabic model resolves and produces text)
before anything is built on it.

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `.env.example`
- Create: `scripts/make_fixture.py`, `scripts/smoke_ocr.py`
- Create: `tests/fixtures/arabic_sample.png` (generated, then committed)

**Interfaces:**
- Consumes: nothing.
- Produces: a uv project on Python 3.12 with `paddleocr`, `paddlepaddle`, `fastapi`, `uvicorn`, `sqlmodel`, `pydantic-settings`, `celery[redis]`, `pillow` installed; a committed fixture image at `tests/fixtures/arabic_sample.png`; `pytest -m slow` marker configured.

- [ ] **Step 1: Initialise the uv project pinned to Python 3.12**

```bash
cd /Users/quanghop/Documents/Playground_2/Arab-ocr
uv init --name arabic-ocr-review --python 3.12 --no-workspace
rm -f main.py hello.py
cat .python-version   # must print 3.12
```

- [ ] **Step 2: Add dependencies**

```bash
uv add fastapi "uvicorn[standard]" sqlmodel pydantic-settings "celery[redis]" redis pillow paddleocr paddlepaddle
uv add --dev pytest httpx ruff arabic-reshaper python-bidi
```

- [ ] **Step 3: Configure pytest and ruff in `pyproject.toml`**

Append to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
addopts = "-m 'not slow'"
markers = [
    "slow: needs real PaddleOCR models (downloads ~100MB on first run)",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.uv]
package = false
```

- [ ] **Step 4: Write `.gitignore`**

```gitignore
.venv/
__pycache__/
*.pyc
data/
node_modules/
frontend/dist/
.env
.DS_Store
```

- [ ] **Step 5: Write the fixture generator**

Arabic needs shaping. Pillow's BASIC layout engine cannot shape Arabic, so the
text is pre-shaped with `arabic_reshaper` and bidi-ordered with `python-bidi`
before drawing, and the layout engine is forced to BASIC so Pillow does not
apply bidi a second time. This is the *only* place in the codebase where text is
reordered, and it exists so the fixture pixels look like real Arabic.

Create `scripts/make_fixture.py`:

```python
"""Generate tests/fixtures/arabic_sample.png. Run once; commit the PNG."""

from pathlib import Path

import arabic_reshaper
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "/System/Library/Fonts/GeezaPro.ttc"
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "arabic_sample.png"

# Three lines, top to bottom. Line 2 is deliberately two separate blocks so the
# reading-order test has a right-to-left pair to sort.
LINES = [
    ["مرحبا بالعالم"],
    ["الثاني", "السطر"],          # drawn left-to-right on the canvas
    ["اختبار التعرف الضوئي"],
]


def render(text: str) -> str:
    return get_display(arabic_reshaper.reshape(text))


def main() -> None:
    width, height = 1000, 400
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, 48, layout_engine=ImageFont.Layout.BASIC)

    y = 40
    for blocks in LINES:
        x = 60
        for block in blocks:
            draw.text((x, y), render(block), font=font, fill="black")
            x += 380
        y += 120

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT)
    print(f"wrote {OUT} ({width}x{height})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Generate and eyeball the fixture**

```bash
uv run python scripts/make_fixture.py
open tests/fixtures/arabic_sample.png
```

Expected: three lines of connected (not disconnected-letter) Arabic on white.
If letters appear disconnected, the reshaper step did not run — fix before continuing.

- [ ] **Step 7: Write the smoke script**

Create `scripts/smoke_ocr.py`:

```python
"""Real-PaddleOCR smoke test. Prints resolved model names and recognised text."""

import sys
from pathlib import Path

from paddleocr import PaddleOCR

IMAGE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "arabic_sample.png"


def main() -> int:
    ocr = PaddleOCR(
        lang="ar",
        ocr_version="PP-OCRv5",
        text_recognition_model_name="arabic_PP-OCRv5_mobile_rec",
        text_detection_model_name="PP-OCRv5_mobile_det",
        device="cpu",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    results = ocr.predict(str(IMAGE))
    payload = results[0].json
    data = payload.get("res", payload)  # 3.x wraps the dict under "res"

    print("rec_texts:")
    for text, score in zip(data["rec_texts"], data["rec_scores"], strict=True):
        print(f"  {score:.3f}  {text}")
    print(f"boxes: {len(data['rec_polys'])}")

    if not data["rec_texts"]:
        print("FAIL: no text recognised", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 8: Run the smoke test**

```bash
uv run python scripts/smoke_ocr.py
```

Expected: first run downloads models, then prints ≥3 `rec_texts` lines of Arabic
and a box count ≥3, exit code 0.

If it fails on the model name, run `uv run python -c "import paddleocr; print(paddleocr.__version__)"`
and check the model list at <https://www.paddleocr.ai/latest/en/version3.x/module_usage/text_recognition.html>.
Record whatever name actually works in `.env.example` and use it for the rest of the plan.

- [ ] **Step 9: Write `.env.example`**

```dotenv
# Copy to .env to override. All values shown are the defaults.
DATA_DIR=data
REDIS_URL=redis://localhost:6379/0
JOB_BACKEND=inline          # inline | celery
OCR_DEVICE=cpu
OCR_LANG=ar
OCR_VERSION=PP-OCRv5
OCR_REC_MODEL=arabic_PP-OCRv5_mobile_rec
OCR_DET_MODEL=PP-OCRv5_mobile_det
REVIEWER_NAME=reviewer
```

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml uv.lock .python-version .gitignore .env.example scripts tests/fixtures docs
git commit -m "chore: scaffold uv project and verify Arabic PaddleOCR smoke test"
```

---

### Task 2: OCR engine protocol, RTL reading order, fake + real engines

**Files:**
- Create: `app/__init__.py`, `app/ocr/__init__.py`, `app/config.py`
- Create: `app/ocr/engine.py`, `app/ocr/reading_order.py`, `app/ocr/fake_engine.py`, `app/ocr/paddle_engine.py`
- Test: `tests/test_reading_order.py`, `tests/test_paddle_engine.py`

**Interfaces:**
- Consumes: the fixture and pinned model names from Task 1.
- Produces:
  - `OcrLine(text: str, score: float, polygon: list[tuple[float, float]])`
  - `OcrResult(width: int, height: int, lines: list[OcrLine])` — `lines` already in reading order
  - `class OcrEngine(Protocol)` with `run(self, image_path: Path) -> OcrResult` and `warmup(self) -> None` (no-op by default; `PaddleOcrEngine` overrides it to build the model)
  - `sort_reading_order(lines: Sequence[OcrLine], *, rtl: bool = True, overlap_ratio: float = 0.5) -> list[OcrLine]`
  - `FakeOcrEngine(lines: list[OcrLine] | None = None)` and `PaddleOcrEngine()`
  - `get_settings() -> Settings` (cached), with fields `data_dir`, `db_path`, `images_dir`, `exports_dir`, `redis_url`, `job_backend`, `ocr_device`, `ocr_lang`, `ocr_version`, `ocr_rec_model`, `ocr_det_model`, `reviewer_name`

- [ ] **Step 1: Write `app/config.py`**

```python
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    redis_url: str = "redis://localhost:6379/0"
    job_backend: str = "inline"  # inline | celery
    ocr_device: str = "cpu"
    ocr_lang: str = "ar"
    ocr_version: str = "PP-OCRv5"
    ocr_rec_model: str = "arabic_PP-OCRv5_mobile_rec"
    ocr_det_model: str = "PP-OCRv5_mobile_det"
    reviewer_name: str = "reviewer"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 2: Write `app/ocr/engine.py`**

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

Polygon = list[tuple[float, float]]


@dataclass(frozen=True)
class OcrLine:
    text: str
    score: float
    polygon: Polygon  # 4 (x, y) points in source-image pixels


@dataclass(frozen=True)
class OcrResult:
    width: int
    height: int
    lines: list[OcrLine]  # already in reading order


@runtime_checkable
class OcrEngine(Protocol):
    def run(self, image_path: Path) -> OcrResult: ...

    def warmup(self) -> None:
        """Build any expensive state. Called once per Celery worker process."""
        return None


def bbox(polygon: Polygon) -> tuple[float, float, float, float]:
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return min(xs), min(ys), max(xs), max(ys)
```

- [ ] **Step 3: Write the failing reading-order test**

Create `tests/test_reading_order.py`:

```python
from app.ocr.engine import OcrLine
from app.ocr.reading_order import sort_reading_order


def line(text: str, x0: float, y0: float, w: float = 300, h: float = 60) -> OcrLine:
    return OcrLine(
        text=text,
        score=0.9,
        polygon=[(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)],
    )


def test_rtl_within_band_is_rightmost_first():
    # Detection order is deliberately scrambled.
    lines = [
        line("left-block", 60, 160),
        line("bottom", 60, 280),
        line("right-block", 440, 165),  # same band as left-block (5px offset)
        line("top", 60, 40),
    ]
    ordered = [ln.text for ln in sort_reading_order(lines)]
    assert ordered == ["top", "right-block", "left-block", "bottom"]


def test_ltr_flag_flips_within_band_order():
    lines = [line("a", 60, 40), line("b", 440, 40)]
    assert [ln.text for ln in sort_reading_order(lines, rtl=False)] == ["a", "b"]


def test_does_not_mutate_or_rewrite_text():
    original = line("مرحبا بالعالم", 60, 40)
    (result,) = sort_reading_order([original])
    assert result.text == "مرحبا بالعالم"
    assert result is original


def test_empty_input():
    assert sort_reading_order([]) == []
```

- [ ] **Step 4: Run it to confirm it fails**

Run: `uv run pytest tests/test_reading_order.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ocr.reading_order'`

- [ ] **Step 5: Implement `app/ocr/reading_order.py`**

```python
from collections.abc import Sequence

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
    for ln in sorted(lines, key=lambda l: bbox(l.polygon)[1]):
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
        band["lines"].sort(key=lambda l: bbox(l.polygon)[0], reverse=rtl)
        ordered.extend(band["lines"])
    return ordered
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/test_reading_order.py -v`
Expected: 4 passed

- [ ] **Step 7: Write `app/ocr/fake_engine.py`**

```python
from pathlib import Path

from PIL import Image

from app.ocr.engine import OcrLine, OcrResult
from app.ocr.reading_order import sort_reading_order

DEFAULT_LINES = [
    OcrLine("مرحبا بالعالم", 0.98, [(60, 40), (600, 40), (600, 100), (60, 100)]),
    OcrLine("السطر", 0.95, [(440, 160), (820, 160), (820, 220), (440, 220)]),
    OcrLine("الثاني", 0.91, [(60, 160), (420, 160), (420, 220), (60, 220)]),
    OcrLine("اختبار التعرف الضوئي", 0.88, [(60, 280), (700, 280), (700, 340), (60, 340)]),
]


class FakeOcrEngine:
    """Deterministic engine for tests. Never imports paddle."""

    def __init__(self, lines: list[OcrLine] | None = None) -> None:
        self.lines = DEFAULT_LINES if lines is None else lines
        self.calls: list[Path] = []

    def warmup(self) -> None:
        return None

    def run(self, image_path: Path) -> OcrResult:
        self.calls.append(image_path)
        with Image.open(image_path) as img:
            width, height = img.size
        return OcrResult(width=width, height=height, lines=sort_reading_order(self.lines))
```

- [ ] **Step 8: Write `app/ocr/paddle_engine.py`**

The model is built lazily inside `run()`/`warmup()` and never at import time, so
Celery's prefork can fork before the model exists (paddle is not reliably
fork-safe when the model is built in the parent).

```python
from pathlib import Path

from PIL import Image

from app.config import Settings, get_settings
from app.ocr.engine import OcrLine, OcrResult
from app.ocr.reading_order import sort_reading_order


class PaddleOcrEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._ocr = None

    def warmup(self) -> None:
        """Build the model. Call once per worker process."""
        from paddleocr import PaddleOCR

        if self._ocr is None:
            s = self.settings
            self._ocr = PaddleOCR(
                lang=s.ocr_lang,
                ocr_version=s.ocr_version,
                text_recognition_model_name=s.ocr_rec_model,
                text_detection_model_name=s.ocr_det_model,
                device=s.ocr_device,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )

    def run(self, image_path: Path) -> OcrResult:
        self.warmup()
        results = self._ocr.predict(str(image_path))
        payload = results[0].json
        data = payload.get("res", payload)  # 3.x wraps the dict under "res"

        lines = [
            OcrLine(
                text=text,
                score=float(score),
                polygon=[(float(x), float(y)) for x, y in poly],
            )
            for text, score, poly in zip(
                data["rec_texts"], data["rec_scores"], data["rec_polys"], strict=True
            )
        ]
        with Image.open(image_path) as img:
            width, height = img.size
        return OcrResult(width=width, height=height, lines=sort_reading_order(lines))
```

- [ ] **Step 9: Write the slow integration test**

Create `tests/test_paddle_engine.py`:

```python
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "arabic_sample.png"


@pytest.mark.slow
def test_paddle_engine_reads_the_fixture():
    from app.ocr.paddle_engine import PaddleOcrEngine

    result = PaddleOcrEngine().run(FIXTURE)

    assert result.width == 1000
    assert result.height == 400
    assert len(result.lines) >= 3
    assert any("؀" <= ch <= "ۿ" for ln in result.lines for ch in ln.text)
    ys = [min(p[1] for p in ln.polygon) for ln in result.lines]
    assert ys == sorted(ys) or len(set(ys)) < len(ys)  # bands run top to bottom
```

- [ ] **Step 10: Run both suites**

```bash
uv run pytest -v            # fast suite: slow test excluded
uv run pytest -m slow -v    # real paddle, ~30s
```
Expected: fast suite passes and does not import paddle; slow suite passes.

- [ ] **Step 11: Commit**

```bash
git add app tests
git commit -m "feat: OCR engine protocol, RTL reading order, fake and paddle engines"
```

---

### Task 3: Database models and session handling

**Files:**
- Create: `app/models.py`, `app/db.py`
- Test: `tests/test_models.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: `app.config.get_settings`.
- Produces:
  - `Batch(id, name, source_dir, created_at)`
  - `Image(id, batch_id, path, filename, sha256, width, height, status, error, ocr_ms, created_at, updated_at)`
  - `Line(id, image_id, reading_order, rec_text, corrected_text, score, polygon, status, updated_at)` — `polygon` is a JSON column holding `[[x,y],...]`
  - `ImageStatus` = `pending | queued | running | done | failed | approved`
  - `LineStatus` = `unreviewed | approved | edited`
  - `app.db.init_db()`, `app.db.get_session()` (FastAPI dependency), `app.db.session_scope()` (context manager for the worker), `app.db.configure_engine(url)`
  - `Line.final_text` property → `corrected_text if corrected_text is not None else rec_text`

- [ ] **Step 1: Write the failing model test**

Create `tests/conftest.py`:

```python
import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s
```

Create `tests/test_models.py`:

```python
from sqlmodel import select

from app.models import Batch, Image, ImageStatus, Line, LineStatus


def test_final_text_prefers_correction(session):
    batch = Batch(name="b1", source_dir="/tmp/in")
    session.add(batch)
    session.commit()

    image = Image(
        batch_id=batch.id,
        path="/tmp/in/a.png",
        filename="a.png",
        sha256="abc",
        width=1000,
        height=400,
        status=ImageStatus.done,
    )
    session.add(image)
    session.commit()

    raw = Line(
        image_id=image.id,
        reading_order=0,
        rec_text="مرحبا",
        score=0.9,
        polygon=[[0, 0], [10, 0], [10, 5], [0, 5]],
    )
    fixed = Line(
        image_id=image.id,
        reading_order=1,
        rec_text="بالعالم",
        corrected_text="بالعالمين",
        score=0.7,
        polygon=[[0, 6], [10, 6], [10, 11], [0, 11]],
        status=LineStatus.edited,
    )
    session.add_all([raw, fixed])
    session.commit()

    lines = session.exec(select(Line).order_by(Line.reading_order)).all()
    assert [ln.final_text for ln in lines] == ["مرحبا", "بالعالمين"]
    assert lines[0].rec_text == "مرحبا"          # rec_text stays immutable
    assert lines[0].status is LineStatus.unreviewed
    assert lines[1].polygon == [[0, 6], [10, 6], [10, 11], [0, 11]]


def test_sha256_is_unique(session):
    import pytest
    from sqlalchemy.exc import IntegrityError

    batch = Batch(name="b", source_dir="/tmp")
    session.add(batch)
    session.commit()
    common = dict(batch_id=batch.id, width=1, height=1)
    session.add(Image(path="/a.png", filename="a.png", sha256="dup", **common))
    session.commit()
    session.add(Image(path="/b.png", filename="b.png", sha256="dup", **common))
    with pytest.raises(IntegrityError):
        session.commit()
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models'`

- [ ] **Step 3: Implement `app/models.py`**

```python
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class ImageStatus(StrEnum):
    pending = "pending"
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    approved = "approved"


class LineStatus(StrEnum):
    unreviewed = "unreviewed"
    approved = "approved"
    edited = "edited"


class Batch(SQLModel, table=True):
    __tablename__ = "batches"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    source_dir: str
    created_at: datetime = Field(default_factory=utcnow)


class Image(SQLModel, table=True):
    __tablename__ = "images"
    __table_args__ = (UniqueConstraint("sha256", name="uq_images_sha256"),)

    id: int | None = Field(default=None, primary_key=True)
    batch_id: int = Field(foreign_key="batches.id", index=True)
    path: str
    filename: str
    sha256: str = Field(index=True)
    width: int
    height: int
    status: ImageStatus = Field(default=ImageStatus.pending, index=True)
    error: str | None = None
    ocr_ms: int | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Line(SQLModel, table=True):
    __tablename__ = "lines"

    id: int | None = Field(default=None, primary_key=True)
    image_id: int = Field(foreign_key="images.id", index=True)
    reading_order: int
    rec_text: str
    corrected_text: str | None = None
    score: float
    polygon: list[list[float]] = Field(sa_column=Column(JSON, nullable=False))
    status: LineStatus = Field(default=LineStatus.unreviewed)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def final_text(self) -> str:
        return self.rec_text if self.corrected_text is None else self.corrected_text
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: 2 passed

- [ ] **Step 5: Implement `app/db.py`**

```python
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

_engine: Engine | None = None


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):  # noqa: ANN001
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def configure_engine(url: str | None = None) -> Engine:
    global _engine
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.images_dir.mkdir(parents=True, exist_ok=True)
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(
        url or f"sqlite:///{settings.db_path}",
        connect_args={"check_same_thread": False},
    )
    return _engine


def get_engine() -> Engine:
    return _engine or configure_engine()


def init_db() -> None:
    SQLModel.metadata.create_all(get_engine())


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with Session(get_engine()) as session:
        yield session


@contextmanager
def session_scope() -> Iterator[Session]:
    """For the Celery worker: one commit per image."""
    with Session(get_engine()) as session:
        yield session
```

- [ ] **Step 6: Verify WAL is actually on**

```bash
uv run python -c "
from app.db import configure_engine, init_db, session_scope
from sqlalchemy import text
configure_engine(); init_db()
with session_scope() as s:
    print(s.exec(text('PRAGMA journal_mode')).one())
"
```
Expected: `('wal',)`

- [ ] **Step 7: Commit**

```bash
git add app tests
git commit -m "feat: SQLite schema for batches, images and lines with WAL pragmas"
```

---

### Task 4: Backend slice — import a folder, OCR it, serve it

Ends with a working HTTP API against the fake engine. Jobs run inline; Celery
arrives in Task 6.

**Files:**
- Create: `app/schemas.py`, `app/service.py`, `app/dispatch.py`, `app/main.py`
- Create: `app/routers/__init__.py`, `app/routers/batches.py`, `app/routers/images.py`
- Test: `tests/test_api_import.py`

**Interfaces:**
- Consumes: `OcrEngine`, `FakeOcrEngine`, models, `session_scope`.
- Produces:
  - `app.service.import_folder(session, name: str, source_dir: Path) -> Batch` — hashes each image, skips duplicates by sha256, records width/height, sets status `pending`
  - `app.service.run_ocr_for_image(session, image_id: int, engine: OcrEngine) -> None` — sets `running`, replaces that image's lines, sets `done` + `ocr_ms`, or `failed` + `error`
  - `app.dispatch.enqueue_image(image_id: int) -> None` — inline or celery per `settings.job_backend`
  - `app.dispatch.get_engine() -> OcrEngine` — module-level singleton, monkeypatched in tests
  - HTTP: `GET /api/health`, `POST /api/batches`, `GET /api/batches`, `GET /api/batches/{id}/images`, `GET /api/images/{id}`, `GET /api/images/{id}/file`
  - Response shapes `BatchOut`, `BatchDetailOut`, `ImageOut`, `ImageDetailOut`, `LineOut` (fields listed in Step 2)

- [ ] **Step 1: Write the failing API test**

Create `tests/test_api_import.py`:

```python
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image as PILImage


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JOB_BACKEND", "inline")  # never touch Redis from tests
    from app.config import get_settings

    get_settings.cache_clear()

    from app import db, dispatch
    from app.main import create_app
    from app.ocr.fake_engine import FakeOcrEngine

    db.configure_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.init_db()
    monkeypatch.setattr(dispatch, "_engine", FakeOcrEngine())
    return TestClient(create_app())


@pytest.fixture
def source_dir(tmp_path):
    d = tmp_path / "incoming"
    d.mkdir()
    for name in ("one.png", "two.png"):
        PILImage.new("RGB", (1000, 400), "white").save(d / name)
    (d / "notes.txt").write_text("ignore me", encoding="utf-8")
    return d


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_import_ocrs_every_image_and_serves_lines(client, source_dir):
    resp = client.post("/api/batches", json={"name": "batch-1", "source_dir": str(source_dir)})
    assert resp.status_code == 201
    batch = resp.json()
    assert batch["image_count"] == 2  # the .txt is ignored

    images = client.get(f"/api/batches/{batch['id']}/images").json()
    assert [i["status"] for i in images] == ["done", "done"]

    detail = client.get(f"/api/images/{images[0]['id']}").json()
    assert detail["width"] == 1000
    assert [ln["reading_order"] for ln in detail["lines"]] == [0, 1, 2, 3]
    assert detail["lines"][1]["rec_text"] == "السطر"   # rightmost of its band
    assert detail["lines"][1]["corrected_text"] is None
    assert len(detail["lines"][0]["polygon"]) == 4

    raw = client.get(f"/api/images/{images[0]['id']}/file")
    assert raw.status_code == 200
    assert raw.headers["content-type"] == "image/png"


def test_reimport_same_folder_adds_nothing(client, source_dir):
    first = client.post("/api/batches", json={"name": "a", "source_dir": str(source_dir)}).json()
    second = client.post("/api/batches", json={"name": "b", "source_dir": str(source_dir)}).json()
    assert first["image_count"] == 2
    assert second["image_count"] == 0
    assert second["skipped_count"] == 2


def test_import_rejects_missing_dir(client, tmp_path):
    resp = client.post("/api/batches", json={"name": "x", "source_dir": str(tmp_path / "nope")})
    assert resp.status_code == 400
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/test_api_import.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Write `app/schemas.py`**

```python
from datetime import datetime

from pydantic import BaseModel

from app.models import ImageStatus, LineStatus


class BatchCreate(BaseModel):
    name: str
    source_dir: str


class BatchOut(BaseModel):
    id: int
    name: str
    source_dir: str
    created_at: datetime
    image_count: int = 0
    skipped_count: int = 0
    done_count: int = 0
    approved_count: int = 0
    failed_count: int = 0


class LineOut(BaseModel):
    id: int
    reading_order: int
    rec_text: str
    corrected_text: str | None
    final_text: str
    score: float
    polygon: list[list[float]]
    status: LineStatus


class ImageOut(BaseModel):
    id: int
    batch_id: int
    filename: str
    width: int
    height: int
    status: ImageStatus
    error: str | None


class ImageDetailOut(ImageOut):
    lines: list[LineOut]


class LineUpdate(BaseModel):
    corrected_text: str | None = None
    status: LineStatus | None = None


class ImageUpdate(BaseModel):
    status: ImageStatus
```

- [ ] **Step 4: Write `app/service.py`**

```python
import hashlib
import time
from pathlib import Path

from PIL import Image as PILImage
from sqlmodel import Session, select

from app.models import Batch, Image, ImageStatus, Line, utcnow
from app.ocr.engine import OcrEngine

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def import_folder(session: Session, name: str, source_dir: Path) -> tuple[Batch, int, int]:
    """Returns (batch, imported_count, skipped_count). Skips by sha256."""
    if not source_dir.is_dir():
        raise NotADirectoryError(str(source_dir))

    batch = Batch(name=name, source_dir=str(source_dir))
    session.add(batch)
    session.commit()
    session.refresh(batch)

    imported = skipped = 0
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        digest = sha256_of(path)
        if session.exec(select(Image).where(Image.sha256 == digest)).first():
            skipped += 1
            continue
        with PILImage.open(path) as img:
            width, height = img.size
        session.add(
            Image(
                batch_id=batch.id,
                path=str(path),
                filename=path.name,
                sha256=digest,
                width=width,
                height=height,
                status=ImageStatus.pending,
            )
        )
        imported += 1
    session.commit()
    return batch, imported, skipped


def run_ocr_for_image(session: Session, image_id: int, engine: OcrEngine) -> None:
    image = session.get(Image, image_id)
    if image is None:
        return

    image.status = ImageStatus.running
    image.updated_at = utcnow()
    session.commit()

    started = time.perf_counter()
    try:
        result = engine.run(Path(image.path))
    except Exception as exc:  # noqa: BLE001 - the message is shown to the reviewer
        image.status = ImageStatus.failed
        image.error = f"{type(exc).__name__}: {exc}"
        image.updated_at = utcnow()
        session.commit()
        return

    for old in session.exec(select(Line).where(Line.image_id == image.id)).all():
        session.delete(old)

    for index, ocr_line in enumerate(result.lines):
        session.add(
            Line(
                image_id=image.id,
                reading_order=index,
                rec_text=ocr_line.text,
                score=ocr_line.score,
                polygon=[[float(x), float(y)] for x, y in ocr_line.polygon],
            )
        )

    image.width, image.height = result.width, result.height
    image.status = ImageStatus.done
    image.error = None
    image.ocr_ms = int((time.perf_counter() - started) * 1000)
    image.updated_at = utcnow()
    session.commit()  # one commit per image
```

- [ ] **Step 5: Write `app/dispatch.py`**

```python
from app.config import get_settings
from app.ocr.engine import OcrEngine

_engine: OcrEngine | None = None


def get_engine() -> OcrEngine:
    global _engine
    if _engine is None:
        from app.ocr.paddle_engine import PaddleOcrEngine

        _engine = PaddleOcrEngine()
    return _engine


def enqueue_image(image_id: int) -> None:
    """Run OCR for one image. Inline today; Celery once JOB_BACKEND=celery."""
    if get_settings().job_backend == "celery":
        from app.worker.tasks import ocr_image

        ocr_image.delay(image_id)
        return

    from app.db import session_scope
    from app.service import run_ocr_for_image

    with session_scope() as session:
        run_ocr_for_image(session, image_id, get_engine())
```

- [ ] **Step 6: Write `app/routers/batches.py`**

```python
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, func, select

from app.db import get_session
from app.dispatch import enqueue_image
from app.models import Batch, Image, ImageStatus
from app.schemas import BatchCreate, BatchOut, ImageOut
from app.service import import_folder

router = APIRouter(prefix="/api/batches", tags=["batches"])


def _counts(session: Session, batch_id: int) -> dict[str, int]:
    rows = session.exec(
        select(Image.status, func.count(Image.id)).where(Image.batch_id == batch_id).group_by(Image.status)
    ).all()
    by_status = {s: n for s, n in rows}
    return {
        "image_count": sum(by_status.values()),
        "done_count": by_status.get(ImageStatus.done, 0),
        "approved_count": by_status.get(ImageStatus.approved, 0),
        "failed_count": by_status.get(ImageStatus.failed, 0),
    }


@router.post("", response_model=BatchOut, status_code=status.HTTP_201_CREATED)
def create_batch(payload: BatchCreate, session: Session = Depends(get_session)) -> BatchOut:
    try:
        batch, imported, skipped = import_folder(session, payload.name, Path(payload.source_dir))
    except NotADirectoryError:
        raise HTTPException(status_code=400, detail=f"not a directory: {payload.source_dir}") from None

    for image in session.exec(
        select(Image).where(Image.batch_id == batch.id, Image.status == ImageStatus.pending)
    ).all():
        image.status = ImageStatus.queued
        session.add(image)
    session.commit()

    for image_id in session.exec(select(Image.id).where(Image.batch_id == batch.id)).all():
        enqueue_image(image_id)

    return BatchOut(
        **batch.model_dump(),
        image_count=imported,
        skipped_count=skipped,
        **{k: v for k, v in _counts(session, batch.id).items() if k != "image_count"},
    )


@router.get("", response_model=list[BatchOut])
def list_batches(session: Session = Depends(get_session)) -> list[BatchOut]:
    return [
        BatchOut(**b.model_dump(), **_counts(session, b.id))
        for b in session.exec(select(Batch).order_by(Batch.created_at.desc())).all()
    ]


@router.get("/{batch_id}", response_model=BatchOut)
def get_batch(batch_id: int, session: Session = Depends(get_session)) -> BatchOut:
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    return BatchOut(**batch.model_dump(), **_counts(session, batch_id))


@router.get("/{batch_id}/images", response_model=list[ImageOut])
def list_images(batch_id: int, session: Session = Depends(get_session)) -> list[Image]:
    return session.exec(
        select(Image).where(Image.batch_id == batch_id).order_by(Image.filename)
    ).all()
```

- [ ] **Step 7: Write `app/routers/images.py`**

```python
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from app.db import get_session
from app.models import Image, Line
from app.schemas import ImageDetailOut, LineOut

router = APIRouter(prefix="/api/images", tags=["images"])


def line_out(line: Line) -> LineOut:
    return LineOut(**line.model_dump(), final_text=line.final_text)


@router.get("/{image_id}", response_model=ImageDetailOut)
def get_image(image_id: int, session: Session = Depends(get_session)) -> ImageDetailOut:
    image = session.get(Image, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="image not found")
    lines = session.exec(
        select(Line).where(Line.image_id == image_id).order_by(Line.reading_order)
    ).all()
    return ImageDetailOut(**image.model_dump(), lines=[line_out(ln) for ln in lines])


@router.get("/{image_id}/file")
def get_image_file(image_id: int, session: Session = Depends(get_session)) -> FileResponse:
    image = session.get(Image, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="image not found")
    path = Path(image.path)
    if not path.is_file():
        raise HTTPException(status_code=410, detail=f"source file is gone: {path}")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)
```

- [ ] **Step 8: Write `app/main.py`**

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_db
from app.routers import batches, images


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        init_db()
        yield

    app = FastAPI(title="Arabic OCR Review", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(batches.router)
    app.include_router(images.router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
```

Also create an empty `app/routers/__init__.py`.

- [ ] **Step 9: Run the test to verify it passes**

Run: `uv run pytest tests/test_api_import.py -v`
Expected: 4 passed

- [ ] **Step 10: Run the API by hand against the fixture folder**

```bash
mkdir -p data/incoming && cp tests/fixtures/arabic_sample.png data/incoming/
JOB_BACKEND=inline uv run uvicorn app.main:app --reload --port 8000
```

In another shell:

```bash
curl -s -X POST localhost:8000/api/batches -H 'content-type: application/json' \
  -d '{"name":"smoke","source_dir":"data/incoming"}' | python3 -m json.tool
curl -s localhost:8000/api/images/1 | python3 -m json.tool
```
Expected: real Arabic text (not `\uXXXX`) with 4-point polygons.

- [ ] **Step 11: Commit**

```bash
git add app tests
git commit -m "feat: import folders, run OCR inline, serve images and lines over HTTP"
```

---

### Task 5: Frontend slice — side-by-side view with linked boxes

Completes the thin end-to-end slice: image left, lines right, click either side
to highlight the other.

**Files:**
- Create: `frontend/` (Vite scaffold), `frontend/vite.config.ts`
- Create: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`
- Create: `frontend/src/store/selection.ts`
- Create: `frontend/src/components/ImageCanvas.tsx`, `LineList.tsx`, `LineRow.tsx`
- Create: `frontend/src/pages/ReviewPage.tsx`, `frontend/src/App.tsx`, `frontend/src/styles.css`
- Test: `frontend/src/components/polygon.test.ts`

**Interfaces:**
- Consumes: `GET /api/images/{id}`, `GET /api/images/{id}/file`, `GET /api/batches/{id}/images`.
- Produces:
  - `types.ts`: `LineDto`, `ImageDto`, `ImageDetailDto`, `BatchDto` mirroring `app/schemas.py`
  - `client.ts`: `getBatches()`, `createBatch(name, sourceDir)`, `getBatchImages(batchId)`, `getImage(imageId)`, `imageFileUrl(imageId)`
  - `polygonToPoints(polygon: number[][]): string` — SVG `points` attribute
  - `useSelection()` store: `{ selectedId, hoveredId, select(id), hover(id) }`

- [ ] **Step 1: Scaffold the frontend**

```bash
cd /Users/quanghop/Documents/Playground_2/Arab-ocr
npm create vite@latest frontend -- --template react-ts
cd frontend && npm install && npm install zustand && npm install -D vitest
```

- [ ] **Step 2: Configure the dev proxy and test runner**

Replace `frontend/vite.config.ts`:

```ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
  test: { environment: "node" },
});
```

Add to `frontend/package.json` scripts: `"test": "vitest run"`.

- [ ] **Step 3: Write the failing polygon test**

Create `frontend/src/components/polygon.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { polygonToPoints } from "./polygon";

describe("polygonToPoints", () => {
  it("formats an SVG points attribute in image pixel space", () => {
    expect(
      polygonToPoints([
        [60, 40],
        [600, 40],
        [600, 100],
        [60, 100],
      ]),
    ).toBe("60,40 600,40 600,100 60,100");
  });

  it("handles float coordinates", () => {
    expect(polygonToPoints([[1.5, 2.25]])).toBe("1.5,2.25");
  });
});
```

- [ ] **Step 4: Run it to confirm it fails**

Run: `cd frontend && npm test`
Expected: FAIL — cannot resolve `./polygon`

- [ ] **Step 5: Implement `frontend/src/components/polygon.ts`**

```ts
export function polygonToPoints(polygon: number[][]): string {
  return polygon.map(([x, y]) => `${x},${y}`).join(" ");
}
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd frontend && npm test`
Expected: 2 passed

- [ ] **Step 7: Write `frontend/src/api/types.ts`**

```ts
export type ImageStatus = "pending" | "queued" | "running" | "done" | "failed" | "approved";
export type LineStatus = "unreviewed" | "approved" | "edited";

export interface LineDto {
  id: number;
  reading_order: number;
  rec_text: string;
  corrected_text: string | null;
  final_text: string;
  score: number;
  polygon: number[][];
  status: LineStatus;
}

export interface ImageDto {
  id: number;
  batch_id: number;
  filename: string;
  width: number;
  height: number;
  status: ImageStatus;
  error: string | null;
}

export interface ImageDetailDto extends ImageDto {
  lines: LineDto[];
}

export interface BatchDto {
  id: number;
  name: string;
  source_dir: string;
  created_at: string;
  image_count: number;
  skipped_count: number;
  done_count: number;
  approved_count: number;
  failed_count: number;
}
```

- [ ] **Step 8: Write `frontend/src/api/client.ts`**

```ts
import type { BatchDto, ImageDetailDto, ImageDto } from "./types";

async function json<T>(input: string, init?: RequestInit): Promise<T> {
  const res = await fetch(input, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`${init?.method ?? "GET"} ${input} -> ${res.status}`);
  return (await res.json()) as T;
}

export const getBatches = () => json<BatchDto[]>("/api/batches");

export const createBatch = (name: string, sourceDir: string) =>
  json<BatchDto>("/api/batches", {
    method: "POST",
    body: JSON.stringify({ name, source_dir: sourceDir }),
  });

export const getBatchImages = (batchId: number) =>
  json<ImageDto[]>(`/api/batches/${batchId}/images`);

export const getImage = (imageId: number) => json<ImageDetailDto>(`/api/images/${imageId}`);

export const imageFileUrl = (imageId: number) => `/api/images/${imageId}/file`;
```

- [ ] **Step 9: Write `frontend/src/store/selection.ts`**

```ts
import { create } from "zustand";

interface SelectionState {
  selectedId: number | null;
  hoveredId: number | null;
  select: (id: number | null) => void;
  hover: (id: number | null) => void;
}

export const useSelection = create<SelectionState>((set) => ({
  selectedId: null,
  hoveredId: null,
  select: (id) => set({ selectedId: id }),
  hover: (id) => set({ hoveredId: id }),
}));
```

- [ ] **Step 10: Write `frontend/src/components/ImageCanvas.tsx`**

The SVG sits inside the same box as the `<img>` and uses
`viewBox="0 0 width height"`, so box coordinates are raw image pixels and no
scale arithmetic exists anywhere in the codebase.

```tsx
import type { ImageDetailDto } from "../api/types";
import { imageFileUrl } from "../api/client";
import { useSelection } from "../store/selection";
import { polygonToPoints } from "./polygon";

export function ImageCanvas({ image }: { image: ImageDetailDto }) {
  const { selectedId, hoveredId, select, hover } = useSelection();

  return (
    <div className="canvas-frame">
      <div className="canvas-stack" style={{ aspectRatio: `${image.width} / ${image.height}` }}>
        <img src={imageFileUrl(image.id)} alt={image.filename} className="canvas-img" />
        <svg
          className="canvas-svg"
          viewBox={`0 0 ${image.width} ${image.height}`}
          preserveAspectRatio="xMidYMid meet"
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
        </svg>
      </div>
    </div>
  );
}
```

- [ ] **Step 11: Write `frontend/src/components/LineRow.tsx`**

Read-only in this task; editing arrives in Task 8.

```tsx
import { useEffect, useRef } from "react";
import type { LineDto } from "../api/types";
import { useSelection } from "../store/selection";

export function LineRow({ line }: { line: LineDto }) {
  const { selectedId, hoveredId, select, hover } = useSelection();
  const ref = useRef<HTMLDivElement>(null);
  const isSelected = line.id === selectedId;

  useEffect(() => {
    if (isSelected) ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [isSelected]);

  return (
    <div
      ref={ref}
      className={[
        "line-row",
        isSelected ? "line-selected" : "",
        line.id === hoveredId ? "line-hovered" : "",
      ].join(" ")}
      onClick={() => select(line.id)}
      onMouseEnter={() => hover(line.id)}
      onMouseLeave={() => hover(null)}
    >
      <span className="line-index">{line.reading_order + 1}</span>
      <span className="line-text" dir="rtl" lang="ar">
        {line.final_text}
      </span>
      <span className="line-score">{line.score.toFixed(2)}</span>
    </div>
  );
}
```

- [ ] **Step 12: Write `frontend/src/components/LineList.tsx`**

```tsx
import type { LineDto } from "../api/types";
import { LineRow } from "./LineRow";

export function LineList({ lines }: { lines: LineDto[] }) {
  if (lines.length === 0) return <p className="empty">No text detected.</p>;
  return (
    <div className="line-list">
      {lines.map((line) => (
        <LineRow key={line.id} line={line} />
      ))}
    </div>
  );
}
```

- [ ] **Step 13: Write `frontend/src/pages/ReviewPage.tsx`**

```tsx
import { useEffect, useState } from "react";
import { getImage } from "../api/client";
import type { ImageDetailDto } from "../api/types";
import { ImageCanvas } from "../components/ImageCanvas";
import { LineList } from "../components/LineList";

export function ReviewPage({ imageId }: { imageId: number }) {
  const [image, setImage] = useState<ImageDetailDto | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setImage(null);
    getImage(imageId).then(setImage).catch((e: Error) => setError(e.message));
  }, [imageId]);

  if (error) return <p className="error">{error}</p>;
  if (!image) return <p className="empty">Loading…</p>;

  return (
    <div className="review-split">
      <section className="pane pane-image">
        <ImageCanvas image={image} />
      </section>
      <section className="pane pane-text">
        <h2>{image.filename}</h2>
        <LineList lines={image.lines} />
      </section>
    </div>
  );
}
```

- [ ] **Step 14: Write `frontend/src/App.tsx` and `frontend/src/styles.css`**

`App.tsx` — temporary hard-coded image id; Task 7 replaces it with the batch list:

```tsx
import "./styles.css";
import { ReviewPage } from "./pages/ReviewPage";

export default function App() {
  return <ReviewPage imageId={1} />;
}
```

`styles.css`:

```css
:root { --bg: #14161a; --fg: #e8e8ea; --muted: #8b909a; --accent: #4da3ff; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 14px/1.5 system-ui, -apple-system, sans-serif; }

.review-split { display: grid; grid-template-columns: 1fr 1fr; height: 100vh; }
.pane { overflow: auto; padding: 12px; }
.pane-image { background: #0d0f12; border-inline-end: 1px solid #262a31; }

.canvas-stack { position: relative; width: 100%; }
.canvas-img, .canvas-svg { position: absolute; inset: 0; width: 100%; height: 100%; }

.box { fill: rgba(77, 163, 255, 0.10); stroke: var(--accent); stroke-width: 2;
  vector-effect: non-scaling-stroke; cursor: pointer; }
.box-hovered { fill: rgba(77, 163, 255, 0.22); }
.box-selected { fill: rgba(255, 193, 7, 0.25); stroke: #ffc107; stroke-width: 3; }

.line-row { display: grid; grid-template-columns: 2rem 1fr 3rem; gap: 8px;
  align-items: center; padding: 6px 8px; border-radius: 6px; cursor: pointer; }
.line-hovered { background: #1c2029; }
.line-selected { background: #2a2415; outline: 1px solid #ffc107; }
.line-index, .line-score { color: var(--muted); font-variant-numeric: tabular-nums; }
.line-text { font-size: 20px; font-family: "Geeza Pro", "Noto Naskh Arabic", serif; }
.empty, .error { color: var(--muted); padding: 12px; }
.error { color: #ff6b6b; }
```

- [ ] **Step 15: Verify the slice end to end**

```bash
# shell 1
JOB_BACKEND=inline uv run uvicorn app.main:app --reload --port 8000
# shell 2
cd frontend && npm run dev
```

Open <http://localhost:5173>. Expected: image left with polygons drawn over the
Arabic lines, rows right in RTL; clicking a row turns its polygon amber, clicking
a polygon highlights its row. Resize the window — boxes stay on the text.

- [ ] **Step 16: Commit**

```bash
git add frontend
git commit -m "feat: side-by-side review view with box-linked Arabic line list"
```

---

### Task 6: Celery + Redis worker with a warm model

**Files:**
- Create: `app/worker/__init__.py`, `app/worker/celery_app.py`, `app/worker/tasks.py`
- Modify: `.env.example` (`JOB_BACKEND=celery`)
- Test: `tests/test_tasks.py`

**Interfaces:**
- Consumes: `run_ocr_for_image`, `session_scope`, `dispatch.get_engine`.
- Produces:
  - `app.worker.celery_app.celery` — Celery app, broker + backend from `settings.redis_url`
  - `app.worker.tasks.ocr_image(image_id: int)` — Celery task, name `"app.ocr_image"`
  - Worker command: `uv run celery -A app.worker.celery_app.celery worker --loglevel=info --pool=prefork --concurrency=2`

- [ ] **Step 1: Write the failing task test**

The test calls the task function directly — no broker, no Redis, fake engine.

Create `tests/test_tasks.py`:

```python
from pathlib import Path

import pytest
from PIL import Image as PILImage

from app.models import Batch, Image, ImageStatus, Line
from app.ocr.fake_engine import FakeOcrEngine


@pytest.fixture
def image_row(session, tmp_path) -> Image:
    path = tmp_path / "a.png"
    PILImage.new("RGB", (1000, 400), "white").save(path)
    batch = Batch(name="b", source_dir=str(tmp_path))
    session.add(batch)
    session.commit()
    image = Image(
        batch_id=batch.id, path=str(path), filename="a.png", sha256="h",
        width=1000, height=400, status=ImageStatus.queued,
    )
    session.add(image)
    session.commit()
    return image


def test_task_body_writes_lines_and_marks_done(session, image_row, monkeypatch):
    from app.worker import tasks

    tasks.run_ocr(session, image_row.id, FakeOcrEngine())
    session.refresh(image_row)

    assert image_row.status is ImageStatus.done
    assert image_row.ocr_ms is not None
    from sqlmodel import select

    lines = session.exec(select(Line).where(Line.image_id == image_row.id)).all()
    assert len(lines) == 4


def test_task_body_records_failure(session, image_row):
    from app.worker import tasks

    class Boom:
        def run(self, path: Path):
            raise RuntimeError("model exploded")

    tasks.run_ocr(session, image_row.id, Boom())
    session.refresh(image_row)

    assert image_row.status is ImageStatus.failed
    assert "model exploded" in image_row.error


def test_rerunning_replaces_lines_instead_of_appending(session, image_row):
    from sqlmodel import select

    from app.worker import tasks

    tasks.run_ocr(session, image_row.id, FakeOcrEngine())
    tasks.run_ocr(session, image_row.id, FakeOcrEngine())
    lines = session.exec(select(Line).where(Line.image_id == image_row.id)).all()
    assert len(lines) == 4
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/test_tasks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.worker'`

- [ ] **Step 3: Write `app/worker/celery_app.py`**

`worker_process_init` is the key line: prefork forks *before* the handler runs,
so the model is built inside the child process and stays warm for every task that
child handles. Building it at module import would build it in the parent and fork
a paddle runtime, which is not reliably fork-safe.

```python
from celery import Celery
from celery.signals import worker_process_init

from app.config import get_settings
from app.db import configure_engine

settings = get_settings()

celery = Celery(
    "arabic_ocr",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.worker.tasks"],
)
celery.conf.update(
    task_acks_late=True,            # a crashed worker re-queues its image
    worker_prefetch_multiplier=1,   # no hoarding; long tasks spread evenly
    task_track_started=True,
    broker_connection_retry_on_startup=True,
)


@worker_process_init.connect
def _init_worker_process(**_kwargs) -> None:
    """Build the DB engine and load PaddleOCR once per forked child."""
    from app.dispatch import get_engine

    configure_engine()
    get_engine().warmup()
```

- [ ] **Step 4: Write `app/worker/tasks.py`**

```python
from sqlmodel import Session

from app.db import session_scope
from app.dispatch import get_engine
from app.ocr.engine import OcrEngine
from app.service import run_ocr_for_image
from app.worker.celery_app import celery


def run_ocr(session: Session, image_id: int, engine: OcrEngine) -> None:
    """Task body, importable and testable without a broker."""
    run_ocr_for_image(session, image_id, engine)


@celery.task(name="app.ocr_image", bind=True, max_retries=0)
def ocr_image(self, image_id: int) -> None:  # noqa: ANN001
    with session_scope() as session:
        run_ocr(session, image_id, get_engine())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tasks.py -v`
Expected: 3 passed

- [ ] **Step 6: Install and start Redis**

```bash
brew install redis
brew services start redis
redis-cli ping   # expects: PONG
```

- [ ] **Step 7: Switch the default backend to celery**

In `.env.example` change `JOB_BACKEND=inline` to `JOB_BACKEND=celery`, and create
a local `.env` with the same value.

- [ ] **Step 8: Run the real three-process stack**

```bash
# shell 1
uv run celery -A app.worker.celery_app.celery worker --loglevel=info --pool=prefork --concurrency=2
# shell 2
uv run uvicorn app.main:app --reload --port 8000
# shell 3
curl -s -X POST localhost:8000/api/batches -H 'content-type: application/json' \
  -d '{"name":"celery-smoke","source_dir":"data/incoming"}'
sleep 20 && curl -s localhost:8000/api/batches | python3 -m json.tool
```

Expected: worker log shows the model loading **once per child process**, then
`Task app.ocr_image ... succeeded`; the batch reports `done_count` equal to
`image_count`. Concurrency is 2 because each prefork child holds its own copy of
the model (~1–2 GB RSS); raise it only if `Activity Monitor` says there is room.

- [ ] **Step 9: Commit**

```bash
git add app .env.example
git commit -m "feat: Celery worker with per-process warm PaddleOCR model"
```

---

### Task 7: Batch list, progress polling and image navigation

**Files:**
- Create: `frontend/src/components/BatchList.tsx`, `frontend/src/components/ImageStrip.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/styles.css`, `frontend/src/pages/ReviewPage.tsx`

**Interfaces:**
- Consumes: `getBatches`, `createBatch`, `getBatchImages`, `BatchDto`, `ImageDto`.
- Produces: `<BatchList onPick={(batchId) => void} />`, `<ImageStrip images={ImageDto[]} activeId onPick />`; `App` owns `batchId` / `imageId` state.

- [ ] **Step 1: Write `frontend/src/components/BatchList.tsx`**

```tsx
import { useEffect, useState } from "react";
import { createBatch, getBatches } from "../api/client";
import type { BatchDto } from "../api/types";

export function BatchList({ onPick }: { onPick: (batchId: number) => void }) {
  const [batches, setBatches] = useState<BatchDto[]>([]);
  const [name, setName] = useState("");
  const [dir, setDir] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = () => getBatches().then(setBatches).catch(() => {});

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 3000); // progress ticks while OCR runs
    return () => clearInterval(timer);
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const batch = await createBatch(name, dir);
      setName("");
      setDir("");
      await refresh();
      onPick(batch.id);
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="batch-list">
      <form onSubmit={submit} className="batch-form">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Batch name" required />
        <input value={dir} onChange={(e) => setDir(e.target.value)} placeholder="/path/to/images" required />
        <button disabled={busy}>{busy ? "Importing…" : "Import folder"}</button>
      </form>
      {batches.map((b) => (
        <button key={b.id} className="batch-item" onClick={() => onPick(b.id)}>
          <span>{b.name}</span>
          <span className="muted">
            {b.done_count + b.approved_count}/{b.image_count} done
            {b.failed_count > 0 ? ` · ${b.failed_count} failed` : ""}
          </span>
        </button>
      ))}
    </aside>
  );
}
```

- [ ] **Step 2: Write `frontend/src/components/ImageStrip.tsx`**

```tsx
import type { ImageDto } from "../api/types";

export function ImageStrip({
  images,
  activeId,
  onPick,
}: {
  images: ImageDto[];
  activeId: number | null;
  onPick: (id: number) => void;
}) {
  return (
    <div className="image-strip">
      {images.map((img) => (
        <button
          key={img.id}
          className={`strip-item status-${img.status} ${img.id === activeId ? "strip-active" : ""}`}
          onClick={() => onPick(img.id)}
          title={img.error ?? img.status}
        >
          {img.filename}
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 3: Rewrite `frontend/src/App.tsx`**

```tsx
import { useEffect, useState } from "react";
import "./styles.css";
import { getBatchImages } from "./api/client";
import type { ImageDto } from "./api/types";
import { BatchList } from "./components/BatchList";
import { ImageStrip } from "./components/ImageStrip";
import { ReviewPage } from "./pages/ReviewPage";

export default function App() {
  const [batchId, setBatchId] = useState<number | null>(null);
  const [images, setImages] = useState<ImageDto[]>([]);
  const [imageId, setImageId] = useState<number | null>(null);

  useEffect(() => {
    if (batchId === null) return;
    const load = () =>
      getBatchImages(batchId).then((rows) => {
        setImages(rows);
        setImageId((current) => current ?? rows[0]?.id ?? null);
      });
    load();
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [batchId]);

  return (
    <div className="app-shell">
      <BatchList onPick={(id) => { setBatchId(id); setImageId(null); }} />
      <main className="app-main">
        <ImageStrip images={images} activeId={imageId} onPick={setImageId} />
        {imageId === null ? <p className="empty">Pick a batch, then an image.</p> : <ReviewPage imageId={imageId} />}
      </main>
    </div>
  );
}
```

- [ ] **Step 4: Add layout styles**

Append to `frontend/src/styles.css`:

```css
.app-shell { display: grid; grid-template-columns: 260px 1fr; height: 100vh; }
.batch-list { border-inline-end: 1px solid #262a31; overflow: auto; padding: 8px;
  display: flex; flex-direction: column; gap: 6px; }
.batch-form { display: flex; flex-direction: column; gap: 6px; margin-bottom: 8px; }
.batch-form input, .batch-form button { padding: 6px 8px; border-radius: 6px;
  border: 1px solid #333842; background: #1a1d23; color: var(--fg); }
.batch-item { display: flex; flex-direction: column; align-items: flex-start;
  gap: 2px; padding: 8px; border: 1px solid #262a31; border-radius: 6px;
  background: #171a1f; color: var(--fg); cursor: pointer; text-align: start; }
.muted { color: var(--muted); font-size: 12px; }

.app-main { display: grid; grid-template-rows: auto 1fr; min-height: 0; }
.image-strip { display: flex; gap: 6px; overflow-x: auto; padding: 8px;
  border-block-end: 1px solid #262a31; }
.strip-item { white-space: nowrap; padding: 4px 8px; border-radius: 999px;
  border: 1px solid #333842; background: #171a1f; color: var(--fg); cursor: pointer; }
.strip-active { outline: 2px solid var(--accent); }
.status-failed { border-color: #ff6b6b; color: #ff6b6b; }
.status-approved { border-color: #4caf50; color: #9ae6a0; }
.status-queued, .status-running { color: var(--muted); }
.review-split { min-height: 0; }
```

- [ ] **Step 5: Verify in the browser**

With the three processes running, import `data/incoming`, watch the batch counter
climb from `0/N` to `N/N` without reloading, click through images in the strip.

- [ ] **Step 6: Commit**

```bash
git add frontend
git commit -m "feat: batch import UI with live progress and image navigation"
```

---

### Task 8: Line editing and per-line status

**Files:**
- Create: `app/routers/lines.py`, `tests/test_api_lines.py`
- Modify: `app/main.py` (register router), `frontend/src/api/client.ts`, `frontend/src/components/LineRow.tsx`, `frontend/src/pages/ReviewPage.tsx`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: `Line`, `LineOut`, `LineUpdate`, `useSelection`.
- Produces:
  - `PATCH /api/lines/{line_id}` with body `{corrected_text?: string | null, status?: LineStatus}` → `LineOut`. Setting `corrected_text` to a string different from `rec_text` forces status `edited`; setting it to `null` clears the correction and resets status to `unreviewed`.
  - `updateLine(lineId, patch): Promise<LineDto>` in `client.ts`
  - `<LineRow line onChange={(line: LineDto) => void} />`

- [ ] **Step 1: Write the failing API test**

Create `tests/test_api_lines.py`:

```python
import pytest
from PIL import Image as PILImage


@pytest.fixture
def client_with_lines(client, tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    PILImage.new("RGB", (1000, 400), "white").save(d / "a.png")
    batch = client.post("/api/batches", json={"name": "b", "source_dir": str(d)}).json()
    image_id = client.get(f"/api/batches/{batch['id']}/images").json()[0]["id"]
    return client, client.get(f"/api/images/{image_id}").json()


def test_correction_marks_line_edited_and_keeps_rec_text(client_with_lines):
    client, image = client_with_lines
    line = image["lines"][0]

    resp = client.patch(f"/api/lines/{line['id']}", json={"corrected_text": "مرحبا بالعالمين"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["corrected_text"] == "مرحبا بالعالمين"
    assert body["final_text"] == "مرحبا بالعالمين"
    assert body["rec_text"] == line["rec_text"]
    assert body["status"] == "edited"


def test_clearing_correction_resets_to_unreviewed(client_with_lines):
    client, image = client_with_lines
    line_id = image["lines"][0]["id"]
    client.patch(f"/api/lines/{line_id}", json={"corrected_text": "x"})

    body = client.patch(f"/api/lines/{line_id}", json={"corrected_text": None}).json()
    assert body["corrected_text"] is None
    assert body["final_text"] == image["lines"][0]["rec_text"]
    assert body["status"] == "unreviewed"


def test_approving_a_line_without_editing(client_with_lines):
    client, image = client_with_lines
    body = client.patch(f"/api/lines/{image['lines'][0]['id']}", json={"status": "approved"}).json()
    assert body["status"] == "approved"
    assert body["corrected_text"] is None


def test_patch_unknown_line_is_404(client_with_lines):
    client, _ = client_with_lines
    assert client.patch("/api/lines/9999", json={"status": "approved"}).status_code == 404
```

Move the `client` fixture from `tests/test_api_import.py` into `tests/conftest.py`
unchanged so both modules use it.

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/test_api_lines.py -v`
Expected: FAIL — 404 on `PATCH /api/lines/...` (router not registered)

- [ ] **Step 3: Write `app/routers/lines.py`**

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.db import get_session
from app.models import Line, LineStatus, utcnow
from app.routers.images import line_out
from app.schemas import LineOut, LineUpdate

router = APIRouter(prefix="/api/lines", tags=["lines"])


@router.patch("/{line_id}", response_model=LineOut)
def update_line(
    line_id: int,
    payload: LineUpdate,
    session: Session = Depends(get_session),
) -> LineOut:
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
```

Register it in `app/main.py`:

```python
from app.routers import batches, images, lines
...
    app.include_router(lines.router)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_api_lines.py -v`
Expected: 4 passed

- [ ] **Step 5: Add `updateLine` to `frontend/src/api/client.ts`**

Merge the new names into the file's existing `import type { ... } from "./types"`
line rather than adding a second import statement.

```ts
import type { LineDto, LineStatus } from "./types";

export const updateLine = (
  lineId: number,
  patch: { corrected_text?: string | null; status?: LineStatus },
) => json<LineDto>(`/api/lines/${lineId}`, { method: "PATCH", body: JSON.stringify(patch) });
```

- [ ] **Step 6: Make `LineRow` editable**

Replace `frontend/src/components/LineRow.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";
import { updateLine } from "../api/client";
import type { LineDto } from "../api/types";
import { useSelection } from "../store/selection";

export function LineRow({
  line,
  onChange,
}: {
  line: LineDto;
  onChange: (line: LineDto) => void;
}) {
  const { selectedId, hoveredId, select, hover } = useSelection();
  const ref = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState(line.final_text);
  const [saving, setSaving] = useState(false);
  const isSelected = line.id === selectedId;

  useEffect(() => setDraft(line.final_text), [line.id, line.final_text]);
  useEffect(() => {
    if (isSelected) ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [isSelected]);

  async function commit() {
    if (draft === line.final_text) return;
    setSaving(true);
    try {
      onChange(await updateLine(line.id, { corrected_text: draft === line.rec_text ? null : draft }));
    } finally {
      setSaving(false);
    }
  }

  async function approve() {
    onChange(await updateLine(line.id, { status: "approved" }));
  }

  async function revert() {
    setDraft(line.rec_text);
    onChange(await updateLine(line.id, { corrected_text: null }));
  }

  return (
    <div
      ref={ref}
      className={[
        "line-row",
        `line-status-${line.status}`,
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
          if (e.key === "Enter") { e.preventDefault(); (e.target as HTMLInputElement).blur(); }
          if (e.key === "Escape") setDraft(line.final_text);
        }}
      />
      <span className="line-score" title={`confidence ${line.score}`}>
        {saving ? "…" : line.score.toFixed(2)}
      </span>
      <div className="line-actions">
        <button onClick={approve} title="Mark this line correct">✓</button>
        <button onClick={revert} disabled={line.corrected_text === null} title="Restore OCR text">⟲</button>
      </div>
    </div>
  );
}
```

- [ ] **Step 7: Thread `onChange` through `LineList` and `ReviewPage`**

In `LineList.tsx`, accept and forward `onChange`:

```tsx
export function LineList({
  lines,
  onChange,
}: {
  lines: LineDto[];
  onChange: (line: LineDto) => void;
}) {
  if (lines.length === 0) return <p className="empty">No text detected.</p>;
  return (
    <div className="line-list">
      {lines.map((line) => (
        <LineRow key={line.id} line={line} onChange={onChange} />
      ))}
    </div>
  );
}
```

In `ReviewPage.tsx`, replace the `<LineList lines={image.lines} />` call with:

```tsx
<LineList
  lines={image.lines}
  onChange={(updated) =>
    setImage((prev) =>
      prev === null
        ? prev
        : { ...prev, lines: prev.lines.map((l) => (l.id === updated.id ? updated : l)) },
    )
  }
/>
```

- [ ] **Step 8: Add status styles**

Append to `frontend/src/styles.css`:

```css
.line-row { grid-template-columns: 2rem 1fr 3rem auto; }
.line-text { background: transparent; border: 1px solid transparent; border-radius: 4px;
  color: var(--fg); padding: 4px 6px; width: 100%; }
.line-text:focus { border-color: var(--accent); outline: none; background: #101318; }
.line-actions { display: flex; gap: 4px; }
.line-actions button { background: #1a1d23; color: var(--fg); border: 1px solid #333842;
  border-radius: 4px; cursor: pointer; padding: 2px 6px; }
.line-status-edited { border-inline-start: 3px solid #ffc107; }
.line-status-approved { border-inline-start: 3px solid #4caf50; }
```

- [ ] **Step 9: Verify by hand**

Edit a line, click away (blur saves), reload the page — the correction persists
and the row shows the amber edited bar. Retype the original OCR text: the row
returns to unreviewed. Confirm the Arabic in the input reads right-to-left and
the cursor behaves; nothing anywhere reverses the string.

- [ ] **Step 10: Commit**

```bash
git add app tests frontend
git commit -m "feat: per-line correction and approval"
```

---

### Task 9: Image approval and keyboard-only review flow

**Files:**
- Modify: `app/routers/images.py` (PATCH), `frontend/src/pages/ReviewPage.tsx`
- Create: `frontend/src/components/Toolbar.tsx`, `frontend/src/hooks/useReviewKeys.ts`
- Test: `tests/test_api_images.py`

**Interfaces:**
- Consumes: `ImageUpdate`, `useSelection`.
- Produces:
  - `PATCH /api/images/{image_id}` body `{status: ImageStatus}` → `ImageOut`; rejects `approved` when the image is not `done` or `approved` (409)
  - `updateImageStatus(imageId, status): Promise<ImageDto>` in `client.ts`
  - `useReviewKeys({ lines, selectedId, select, onApproveImage, onNextImage })` — `j`/`k` next/previous line, `a` approve line, `Enter` focus the selected line's input, `A` approve image, `n` next image
  - `<Toolbar image onApprove onNext />`

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_images.py`:

```python
import pytest
from PIL import Image as PILImage


@pytest.fixture
def image_id(client, tmp_path) -> int:
    d = tmp_path / "in"
    d.mkdir()
    PILImage.new("RGB", (1000, 400), "white").save(d / "a.png")
    batch = client.post("/api/batches", json={"name": "b", "source_dir": str(d)}).json()
    return client.get(f"/api/batches/{batch['id']}/images").json()[0]["id"]


def test_approve_a_done_image(client, image_id):
    body = client.patch(f"/api/images/{image_id}", json={"status": "approved"}).json()
    assert body["status"] == "approved"


def test_cannot_approve_a_failed_image(client, image_id, session_factory):
    from app.models import Image, ImageStatus

    with session_factory() as session:
        image = session.get(Image, image_id)
        image.status = ImageStatus.failed
        session.add(image)
        session.commit()

    resp = client.patch(f"/api/images/{image_id}", json={"status": "approved"})
    assert resp.status_code == 409
```

Add to `tests/conftest.py` a `session_factory` fixture that opens a `Session` on
the same engine the `client` fixture configured:

```python
@pytest.fixture
def session_factory():
    from sqlmodel import Session

    from app.db import get_engine

    def factory():
        return Session(get_engine())

    return factory
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/test_api_images.py -v`
Expected: FAIL — 405 Method Not Allowed on PATCH

- [ ] **Step 3: Add the PATCH endpoint to `app/routers/images.py`**

```python
from app.models import ImageStatus, utcnow
from app.schemas import ImageOut, ImageUpdate

APPROVABLE = {ImageStatus.done, ImageStatus.approved}


@router.patch("/{image_id}", response_model=ImageOut)
def update_image(
    image_id: int,
    payload: ImageUpdate,
    session: Session = Depends(get_session),
) -> Image:
    image = session.get(Image, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="image not found")
    if payload.status is ImageStatus.approved and image.status not in APPROVABLE:
        raise HTTPException(status_code=409, detail=f"cannot approve an image in state {image.status}")

    image.status = payload.status
    image.updated_at = utcnow()
    session.add(image)
    session.commit()
    session.refresh(image)
    return image
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_api_images.py -v`
Expected: 2 passed

- [ ] **Step 5: Add `updateImageStatus` to `client.ts`**

```ts
import type { ImageStatus } from "./types";

export const updateImageStatus = (imageId: number, status: ImageStatus) =>
  json<ImageDto>(`/api/images/${imageId}`, { method: "PATCH", body: JSON.stringify({ status }) });
```

- [ ] **Step 6: Write `frontend/src/hooks/useReviewKeys.ts`**

```ts
import { useEffect } from "react";
import type { LineDto } from "../api/types";

interface Options {
  lines: LineDto[];
  selectedId: number | null;
  select: (id: number | null) => void;
  onApproveLine: (lineId: number) => void;
  onApproveImage: () => void;
  onNextImage: () => void;
}

export function useReviewKeys(opts: Options) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement;
      const typing = target.tagName === "INPUT" || target.tagName === "TEXTAREA";
      if (typing && event.key !== "Escape") return;

      const index = opts.lines.findIndex((l) => l.id === opts.selectedId);
      const move = (delta: number) => {
        const next = opts.lines[Math.min(Math.max(index + delta, 0), opts.lines.length - 1)];
        if (next) opts.select(next.id);
      };

      switch (event.key) {
        case "j": move(index === -1 ? 0 : 1); break;
        case "k": move(-1); break;
        case "a": if (opts.selectedId !== null) opts.onApproveLine(opts.selectedId); break;
        case "A": opts.onApproveImage(); break;
        case "n": opts.onNextImage(); break;
        case "Enter": {
          const el = document.querySelector<HTMLInputElement>(".line-selected .line-text");
          el?.focus();
          event.preventDefault();
          break;
        }
        default: return;
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [opts]);
}
```

- [ ] **Step 7: Write `frontend/src/components/Toolbar.tsx`**

```tsx
import type { ImageDetailDto } from "../api/types";

export function Toolbar({
  image,
  onApprove,
  onNext,
}: {
  image: ImageDetailDto;
  onApprove: () => void;
  onNext: () => void;
}) {
  const reviewed = image.lines.filter((l) => l.status !== "unreviewed").length;
  return (
    <header className="toolbar">
      <strong>{image.filename}</strong>
      <span className="muted">
        {reviewed}/{image.lines.length} lines touched · {image.status}
      </span>
      <div className="toolbar-actions">
        <button onClick={onApprove} disabled={image.status === "approved"}>Approve image (A)</button>
        <button onClick={onNext}>Next (n)</button>
      </div>
      <span className="muted">j/k move · a approve line · Enter edit</span>
    </header>
  );
}
```

- [ ] **Step 8: Wire both into `ReviewPage`**

`ReviewPage` now takes an `onNextImage` prop (passed down from `App`, which
advances `imageId` to the next entry in `images`):

```tsx
import { updateImageStatus, updateLine } from "../api/client";
import { Toolbar } from "../components/Toolbar";
import { useReviewKeys } from "../hooks/useReviewKeys";
import { useSelection } from "../store/selection";

// inside ReviewPage, after `image` is loaded:
const { selectedId, select } = useSelection();

const replaceLine = (updated: LineDto) =>
  setImage((prev) =>
    prev === null ? prev : { ...prev, lines: prev.lines.map((l) => (l.id === updated.id ? updated : l)) },
  );

useReviewKeys({
  lines: image?.lines ?? [],
  selectedId,
  select,
  onApproveLine: async (lineId) => replaceLine(await updateLine(lineId, { status: "approved" })),
  onApproveImage: async () => {
    const updated = await updateImageStatus(image!.id, "approved");
    setImage((prev) => (prev === null ? prev : { ...prev, status: updated.status }));
    onNextImage();
  },
  onNextImage,
});
```

`ReviewPage`'s props become `{ imageId: number; onNextImage: () => void }`, and
the `LineDto` type import is added to its existing `../api/types` import. Render
`<Toolbar image={image} onApprove={approveImage} onNext={onNextImage} />` above
the split panes.

In `App.tsx`, supply `onNextImage` by advancing through the loaded `images`:

```tsx
const nextImage = () => {
  const index = images.findIndex((img) => img.id === imageId);
  const next = images[index + 1];
  if (next) setImageId(next.id);
};

// ...
{imageId === null ? (
  <p className="empty">Pick a batch, then an image.</p>
) : (
  <ReviewPage imageId={imageId} onNextImage={nextImage} />
)}
```

Add to `styles.css`:

```css
.toolbar { display: flex; gap: 12px; align-items: center; padding: 8px 12px;
  border-block-end: 1px solid #262a31; }
.toolbar-actions { margin-inline-start: auto; display: flex; gap: 6px; }
.toolbar-actions button { padding: 4px 10px; border-radius: 6px; border: 1px solid #333842;
  background: #1a1d23; color: var(--fg); cursor: pointer; }
```

- [ ] **Step 9: Verify keyboard-only review**

Load a batch, then without touching the mouse: `j` down the lines, `Enter` to
edit, `Escape` then `a` to approve a line, `A` to approve the image and jump to
the next one. The image strip turns the approved item green.

- [ ] **Step 10: Commit**

```bash
git add app tests frontend
git commit -m "feat: image approval and keyboard-driven review flow"
```

---

### Task 10: Exports — JSONL master and per-image .txt

**Files:**
- Create: `app/exporters.py`, `tests/test_exporters.py`
- Modify: `app/routers/batches.py` (export endpoint), `frontend/src/api/client.ts`, `frontend/src/components/BatchList.tsx`

**Interfaces:**
- Consumes: `Batch`, `Image`, `Line`, `settings.exports_dir`.
- Produces:
  - `export_jsonl(session, batch_id: int, out_dir: Path) -> Path` — writes `<out_dir>/<batch_name>-<batch_id>.jsonl`, one object per image: `{image_id, filename, path, width, height, status, ocr_ms, lines: [{reading_order, rec_text, corrected_text, final_text, score, polygon, status}]}`
  - `export_txt(session, batch_id: int, out_dir: Path) -> Path` — writes `<out_dir>/<batch_name>-<batch_id>/<filename>.txt`, `final_text` joined by `\n` in reading order; returns the directory
  - `POST /api/batches/{batch_id}/export` body `{format: "jsonl" | "txt"}` → `{"path": str, "count": int}`
  - `exportBatch(batchId, format)` in `client.ts`

- [ ] **Step 1: Write the failing exporter test**

Create `tests/test_exporters.py`:

```python
import json
from pathlib import Path

from PIL import Image as PILImage


def _seed(client, tmp_path) -> int:
    d = tmp_path / "in"
    d.mkdir()
    PILImage.new("RGB", (1000, 400), "white").save(d / "page-1.png")
    batch = client.post("/api/batches", json={"name": "export-me", "source_dir": str(d)}).json()
    image = client.get(f"/api/batches/{batch['id']}/images").json()[0]
    detail = client.get(f"/api/images/{image['id']}").json()
    client.patch(f"/api/lines/{detail['lines'][0]['id']}", json={"corrected_text": "نص مصحح"})
    return batch["id"]


def test_jsonl_export_is_utf8_and_keeps_both_texts(client, tmp_path, session_factory):
    from app.exporters import export_jsonl

    batch_id = _seed(client, tmp_path)
    out = tmp_path / "out"
    with session_factory() as session:
        path = export_jsonl(session, batch_id, out)

    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")          # no BOM
    assert "نص مصحح".encode() in raw                    # literal Arabic, not \uXXXX
    record = json.loads(raw.decode("utf-8").splitlines()[0])
    assert record["filename"] == "page-1.png"
    assert [ln["reading_order"] for ln in record["lines"]] == [0, 1, 2, 3]
    assert record["lines"][0]["corrected_text"] == "نص مصحح"
    assert record["lines"][0]["rec_text"] != "نص مصحح"  # original preserved
    assert record["lines"][0]["final_text"] == "نص مصحح"
    assert len(record["lines"][0]["polygon"]) == 4


def test_txt_export_uses_final_text_in_reading_order(client, tmp_path, session_factory):
    from app.exporters import export_txt

    batch_id = _seed(client, tmp_path)
    out = tmp_path / "out"
    with session_factory() as session:
        directory = export_txt(session, batch_id, out)

    text_file = Path(directory) / "page-1.txt"
    lines = text_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "نص مصحح"
    assert lines[1] == "السطر"    # reading order preserved: rightmost of band 2
    assert len(lines) == 4
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/test_exporters.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.exporters'`

- [ ] **Step 3: Implement `app/exporters.py`**

```python
import json
from pathlib import Path

from sqlmodel import Session, select

from app.models import Batch, Image, Line


def _images(session: Session, batch_id: int) -> list[Image]:
    return session.exec(
        select(Image).where(Image.batch_id == batch_id).order_by(Image.filename)
    ).all()


def _lines(session: Session, image_id: int) -> list[Line]:
    return session.exec(
        select(Line).where(Line.image_id == image_id).order_by(Line.reading_order)
    ).all()


def _batch_slug(session: Session, batch_id: int) -> str:
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise LookupError(f"batch {batch_id} not found")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in batch.name)
    return f"{safe}-{batch_id}"


def export_jsonl(session: Session, batch_id: int, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_batch_slug(session, batch_id)}.jsonl"

    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for image in _images(session, batch_id):
            record = {
                "image_id": image.id,
                "filename": image.filename,
                "path": image.path,
                "width": image.width,
                "height": image.height,
                "status": str(image.status),
                "ocr_ms": image.ocr_ms,
                "lines": [
                    {
                        "reading_order": ln.reading_order,
                        "rec_text": ln.rec_text,
                        "corrected_text": ln.corrected_text,
                        "final_text": ln.final_text,
                        "score": ln.score,
                        "polygon": ln.polygon,
                        "status": str(ln.status),
                    }
                    for ln in _lines(session, image.id)
                ],
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def export_txt(session: Session, batch_id: int, out_dir: Path) -> Path:
    directory = out_dir / _batch_slug(session, batch_id)
    directory.mkdir(parents=True, exist_ok=True)

    for image in _images(session, batch_id):
        body = "\n".join(ln.final_text for ln in _lines(session, image.id))
        target = directory / f"{Path(image.filename).stem}.txt"
        target.write_text(body + "\n", encoding="utf-8", newline="\n")
    return directory
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_exporters.py -v`
Expected: 2 passed

- [ ] **Step 5: Add the export endpoint to `app/routers/batches.py`**

```python
from pydantic import BaseModel

from app.config import get_settings
from app.exporters import export_jsonl, export_txt


class ExportRequest(BaseModel):
    format: str  # jsonl | txt


@router.post("/{batch_id}/export")
def export_batch(
    batch_id: int,
    payload: ExportRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    if session.get(Batch, batch_id) is None:
        raise HTTPException(status_code=404, detail="batch not found")
    out_dir = get_settings().exports_dir
    if payload.format == "jsonl":
        path = export_jsonl(session, batch_id, out_dir)
    elif payload.format == "txt":
        path = export_txt(session, batch_id, out_dir)
    else:
        raise HTTPException(status_code=400, detail="format must be 'jsonl' or 'txt'")

    count = len(session.exec(select(Image.id).where(Image.batch_id == batch_id)).all())
    return {"path": str(path.resolve()), "count": count}
```

- [ ] **Step 6: Add the export button**

In `client.ts`:

```ts
export const exportBatch = (batchId: number, format: "jsonl" | "txt") =>
  json<{ path: string; count: number }>(`/api/batches/${batchId}/export`, {
    method: "POST",
    body: JSON.stringify({ format }),
  });
```

In `BatchList.tsx`, inside the `batches.map(...)` block, under the batch button:

```tsx
<div className="batch-exports">
  <button onClick={() => exportBatch(b.id, "jsonl").then((r) => alert(`Wrote ${r.path}`))}>
    JSONL
  </button>
  <button onClick={() => exportBatch(b.id, "txt").then((r) => alert(`Wrote ${r.path}`))}>
    .txt
  </button>
</div>
```

- [ ] **Step 7: Verify the bytes on disk**

```bash
curl -s -X POST localhost:8000/api/batches/1/export -H 'content-type: application/json' -d '{"format":"jsonl"}'
file data/exports/*.jsonl                     # expects: UTF-8 Unicode text
head -c 200 data/exports/*.jsonl              # Arabic must be readable, no \uXXXX
```

- [ ] **Step 8: Commit**

```bash
git add app tests frontend
git commit -m "feat: JSONL and per-image .txt exports"
```

---

### Task 11: Zoom, pan and reviewer polish

**Files:**
- Modify: `frontend/src/components/ImageCanvas.tsx`, `frontend/src/styles.css`, `frontend/src/pages/ReviewPage.tsx`
- Create: `frontend/src/components/LineCrop.tsx`

**Interfaces:**
- Consumes: `react-zoom-pan-pinch`, `bbox` of a polygon.
- Produces:
  - `<ImageCanvas image />` with wheel zoom + drag pan; the SVG overlay lives inside the transformed container so boxes and image share one transform — there is no scale arithmetic
  - `polygonBBox(polygon: number[][]): {x: number; y: number; w: number; h: number}` in `polygon.ts`
  - `<LineCrop image line />` — a zoomed crop of the selected line rendered above the line list

- [ ] **Step 1: Install the zoom library**

```bash
cd frontend && npm install react-zoom-pan-pinch
```

- [ ] **Step 2: Write the failing bbox test**

Append to `frontend/src/components/polygon.test.ts`:

```ts
import { polygonBBox } from "./polygon";

describe("polygonBBox", () => {
  it("returns the enclosing rectangle", () => {
    expect(
      polygonBBox([
        [60, 40],
        [600, 45],
        [600, 100],
        [60, 95],
      ]),
    ).toEqual({ x: 60, y: 40, w: 540, h: 60 });
  });
});
```

- [ ] **Step 3: Run it to confirm it fails**

Run: `cd frontend && npm test`
Expected: FAIL — `polygonBBox is not a function`

- [ ] **Step 4: Implement `polygonBBox` in `polygon.ts`**

```ts
export function polygonBBox(polygon: number[][]) {
  const xs = polygon.map(([x]) => x);
  const ys = polygon.map(([, y]) => y);
  const x = Math.min(...xs);
  const y = Math.min(...ys);
  return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y };
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd frontend && npm test`
Expected: 3 passed

- [ ] **Step 6: Wrap the canvas in zoom/pan**

Replace the body of `ImageCanvas.tsx`'s returned JSX:

```tsx
import { TransformComponent, TransformWrapper } from "react-zoom-pan-pinch";

// ...
return (
  <div className="canvas-frame">
    <TransformWrapper minScale={0.5} maxScale={8} doubleClick={{ mode: "reset" }} wheel={{ step: 0.15 }}>
      <TransformComponent wrapperClass="canvas-wrapper" contentClass="canvas-content">
        <div className="canvas-stack" style={{ aspectRatio: `${image.width} / ${image.height}` }}>
          {/* unchanged <img> and <svg> from Task 5 */}
        </div>
      </TransformComponent>
    </TransformWrapper>
  </div>
);
```

Both `<img>` and `<svg>` stay inside `.canvas-stack`, so the transform applies to
them identically and the overlay cannot drift. `vector-effect: non-scaling-stroke`
(already set in Task 5) keeps outlines 2px at every zoom level.

Add:

```css
.canvas-frame { height: 100%; }
.canvas-wrapper { width: 100%; height: 100%; }
.canvas-content { width: 100%; }
```

- [ ] **Step 7: Write `frontend/src/components/LineCrop.tsx`**

```tsx
import { imageFileUrl } from "../api/client";
import type { ImageDetailDto, LineDto } from "../api/types";
import { polygonBBox } from "./polygon";

const PAD = 12;

export function LineCrop({ image, line }: { image: ImageDetailDto; line: LineDto | null }) {
  if (!line) return <div className="line-crop line-crop-empty">Select a line to magnify it.</div>;

  const box = polygonBBox(line.polygon);
  const vb = [
    Math.max(box.x - PAD, 0),
    Math.max(box.y - PAD, 0),
    box.w + PAD * 2,
    box.h + PAD * 2,
  ].join(" ");

  return (
    <svg className="line-crop" viewBox={vb} preserveAspectRatio="xMidYMid meet">
      <image href={imageFileUrl(image.id)} x={0} y={0} width={image.width} height={image.height} />
    </svg>
  );
}
```

Render it in `ReviewPage` above the line list:

```tsx
<LineCrop image={image} line={image.lines.find((l) => l.id === selectedId) ?? null} />
```

```css
.line-crop { width: 100%; height: 90px; background: #0d0f12; border: 1px solid #262a31;
  border-radius: 6px; margin-block-end: 8px; }
.line-crop-empty { display: grid; place-items: center; color: var(--muted); }
```

- [ ] **Step 8: Verify**

Zoom to 4×, pan around: boxes stay glued to the glyphs and outlines stay thin.
Select a line: the crop strip shows that line magnified, so the reviewer can
compare glyphs without zooming the main canvas.

- [ ] **Step 9: Commit**

```bash
git add frontend
git commit -m "feat: zoom/pan canvas and magnified crop of the selected line"
```

---

### Task 12: Run scripts, README and full verification

**Files:**
- Create: `README.md`, `scripts/dev.sh`
- Modify: `.env.example` (final defaults)

**Interfaces:**
- Consumes: everything above.
- Produces: `./scripts/dev.sh` starting Redis check + worker + API + Vite; a README documenting setup, run, review workflow, export locations and the CUDA-later note.

- [ ] **Step 1: Write `scripts/dev.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

redis-cli ping >/dev/null 2>&1 || { echo "redis is not running: brew services start redis"; exit 1; }

uv run celery -A app.worker.celery_app.celery worker --loglevel=info --pool=prefork --concurrency=2 &
WORKER=$!
uv run uvicorn app.main:app --port 8000 &
API=$!
(cd frontend && npm run dev) &
UI=$!

trap 'kill $WORKER $API $UI 2>/dev/null || true' EXIT
echo "API http://localhost:8000/docs   UI http://localhost:5173"
wait
```

```bash
chmod +x scripts/dev.sh
```

- [ ] **Step 2: Write `README.md`**

````markdown
# Arabic OCR Review

OCR a folder of Arabic images with PaddleOCR, then verify the output side by side.

## Setup (macOS)

```bash
brew install redis && brew services start redis
uv sync
cd frontend && npm install && cd ..
cp .env.example .env
uv run python scripts/smoke_ocr.py   # downloads models on first run
```

Python is pinned to 3.12: paddlepaddle 3.3.1 has no cp314 macOS wheel.

## Run

```bash
./scripts/dev.sh          # worker + API + UI
```

UI at <http://localhost:5173>, API docs at <http://localhost:8000/docs>.

## Review workflow

1. Left sidebar → enter a batch name and the absolute path of the image folder → **Import folder**.
   Re-importing the same folder is a no-op: images are deduplicated by sha256.
2. Watch `done/total` climb. Failed images stay in the strip in red with the error in their tooltip.
3. Pick an image. Left = original with polygon overlay; right = one row per detected line, RTL.
4. `j`/`k` move between lines, `Enter` edits, `a` approves the line, `A` approves the image and
   advances, `n` skips to the next image. Clicking a polygon selects its row and vice versa.
5. Export from the sidebar. Files land in `data/exports/`.

## Data model note

`rec_text` (what the model said) is never overwritten. Corrections live in
`corrected_text`, and `final_text` is the correction when present. This keeps
diffs, QA sampling and a future fine-tuning export possible.

## Tests

```bash
uv run pytest              # fast; uses a fake OCR engine, no model download
uv run pytest -m slow      # real PaddleOCR against tests/fixtures/arabic_sample.png
cd frontend && npm test
```

## Later: Ubuntu + CUDA

Out of scope for now. The only device-aware line in the codebase is
`OCR_DEVICE` in `.env` — set it to `gpu:0` and install the CUDA paddle build
from PaddlePaddle's own index on that machine. Nothing else changes.
````

- [ ] **Step 3: Run the full verification sweep**

```bash
uv run ruff check .
uv run pytest -v
uv run pytest -m slow -v
cd frontend && npm test && npx tsc --noEmit && npm run build && cd ..
```
Expected: ruff clean, all pytest green, vitest green, no TypeScript errors, Vite build succeeds.

- [ ] **Step 4: End-to-end run against a real batch**

Copy 20+ real Arabic images into a folder, then:

```bash
./scripts/dev.sh
```

Import the folder in the UI. Confirm: every image reaches `done` or `failed`
with a readable error; boxes align with text at 4× zoom; an edit survives a page
reload; `A` advances to the next image; both exports are readable UTF-8.

- [ ] **Step 5: Commit**

```bash
git add README.md scripts .env.example
git commit -m "docs: setup, run and review instructions"
```

---

## Appendix: chosen defaults and why

| Choice | Rationale |
|--------|-----------|
| Celery prefork, `--concurrency=2` | Each child holds its own ~1–2 GB model. RQ was rejected because it forks per job, reloading the model every image. |
| `task_acks_late=True`, `worker_prefetch_multiplier=1` | A crashed worker re-queues its image instead of silently dropping it; no task hoarding on long jobs. |
| SQLite + WAL, one commit per image | Two writer processes plus the API; WAL and a 5 s busy timeout cover this without a Postgres dependency. |
| sha256 on import | Idempotent re-import of a large folder — the customer will re-run it. |
| `width`/`height` stored on the image row | The overlay `viewBox` is built before the bitmap loads, so boxes never pop into place. |
| SVG overlay inside the transformed container | Zoom/pan applies to image and boxes identically; there is no scale math to get wrong. |
| `dir="rtl"`, never reversing strings | `rec_texts` are logical order already; reversal plus the browser's bidi pass would double-reverse and look almost right. |
