# Object-Storage Upload & Live Review Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the macOS-only native file picker with a browser upload whose bytes land in S3-compatible object storage, and make the UI show which batch is selected plus live progress so no page reload is ever needed.

**Architecture:** A `Storage` protocol with `LocalStorage` (dev/tests) and `S3Storage` (on-prem, RustFS) backends, resolved by `get_storage()` exactly as `get_engine()` already resolves `OcrEngine`. `Image.path` becomes a storage key. Uploads stream to a temp file where they are hashed, size-capped and decoded, then handed to `storage.put()`. The OCR worker gets a real filesystem path back through `storage.as_local_path()`, which is what keeps the whole `app/ocr/` package untouched. Ingestion is two HTTP calls — `POST /api/batches` creates an empty batch, then `POST /api/batches/{id}/images` uploads into it. On the frontend, upload progress comes from `XMLHttpRequest.upload.onprogress`, the batch row gains an active outline and a done/total bar, and `ReviewPage` polls one image's detail while its status is non-terminal.

**Tech Stack:** FastAPI + `python-multipart`, `boto3` against RustFS (S3-compatible), SQLModel/SQLite, Pillow for image validation, React 19 + TypeScript, Vitest (pure functions only — `environment: "node"`, no jsdom).

**Spec:** `docs/superpowers/specs/2026-09-08-upload-and-live-review.md`

**Dependency graph (for parallel execution):**

```
Task 1 (storage layer) ──┬─▶ Task 2 (upload validation) ─▶ Task 3 (cutover) ─▶ Task 5 (frontend upload) ─┬─▶ Task 6 (selected indicator)
                          │                                                                                └─▶ Task 7 (auto-refresh)
                          └─▶ Task 8 (RustFS compose + docs)   [only its compose/env pieces need Task 1; the doc-only steps need nothing]

Task 4 (delete picker) — independent of everything, can run anytime after the frontend no longer calls it (i.e. alongside or after Task 5).
```

Task 1 and Task 4 have no shared files and can start in parallel. Task 8 can start alongside Task 2 for its config/doc steps, but its "verify against a real RustFS" step (Step 8) is only meaningful once Task 3 lands. Tasks 6 and 7 both depend on Task 5 but not on each other.

## Global Constraints

- **No host filesystem paths in any request or response the browser sends.** The client uploads bytes. This is what makes on-prem Docker work.
- **No macOS-only code.** No `osascript`, no `sys.platform` branches. Deployment target is on-premise Docker on Linux.
- **`app/ocr/` is off limits.** `OcrEngine.run(Path)`, `crops.py` and `load_bgr` stay exactly as they are — `storage.as_local_path()` exists so they never learn about object storage. Measurement has reversed assumptions in those files three times; this plan does not reopen them.
- **`STORAGE_BACKEND` defaults to `local`.** `uv run pytest` and `./scripts/dev.sh` must work with no object store running.
- **The GitHub repo (`HopVu-blueoc/arab-ocr`) is public.** `S3_ACCESS_KEY`/`S3_SECRET_KEY` belong in `.env` (gitignored). `.env.example` and compose defaults carry placeholders only — never a real credential, never a real endpoint hostname.
- **`OCR_REC_MODEL=arabic_PP-OCRv5_mobile_rec` and `OCR_DET_MODEL=PP-OCRv5_server_det`** are the settled defaults — already correct in `app/config.py:26` and `:31`. No code change; documentation only (Task 8).
- **No schema migrations.** There is no Alembic; tables come from `SQLModel.metadata.create_all`. `Image.sha256` stays globally `UniqueConstraint`.
- **`rec_text` is never overwritten.** Corrections live in `corrected_text`. Nothing here touches that.
- **Client-supplied filenames and bytes are untrusted.** Never build a key or path from a client string; never trust an extension as proof of file type.
- **Dockerfiles are out of scope.** Only the RustFS *service* joins the existing `docker-compose.yml`, matching how Redis is already handled there.
- Style: `ruff` clean (`uv run ruff check . && uv run ruff format --check .`), sync `def` endpoints, `SessionDep` for DB access, `Annotated` aliases rather than `# noqa: B008`.

---

### Task 1: Storage layer

Additive — nothing calls it yet, so the app keeps working throughout.

**Files:**
- Create: `app/storage/__init__.py`, `app/storage/base.py`, `app/storage/local.py`, `app/storage/s3.py`
- Modify: `app/config.py` (storage settings), `pyproject.toml` (`boto3`), `.env.example`
- Test: `tests/test_storage.py`

**Interfaces:**
- Produces:
  - `ObjectNotFound(Exception)`
  - `Storage` protocol: `put(key: str, source: Path) -> None`, `open(key: str) -> BinaryIO`, `as_local_path(key: str) -> AbstractContextManager[Path]`, `delete(key: str) -> None`, `exists(key: str) -> bool`
  - `LocalStorage(root: Path)`, `S3Storage(settings: Settings)`
  - `get_storage() -> Storage` (module-level cache, resettable via `app.storage._storage = None`)

- [ ] **Step 1: Add the S3 client dependency**

`boto3` rather than the `minio` SDK: RustFS is S3-compatible by design, so one client keeps RustFS, Silo, MinIO, Ceph and AWS S3 all working.

```bash
uv add boto3
```

- [ ] **Step 2: Add the storage settings**

In `app/config.py`, after `ocr_device: str = "cpu"` (line 13):

```python
    # local = a directory under DATA_DIR (dev default; tests and dev.sh must
    # not need an object store running). s3 = any S3-compatible store; on-prem
    # this is RustFS. See docker-compose.yml.
    storage_backend: str = "local"  # local | s3
    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "ocr-images"
    s3_region: str = "us-east-1"
    # Never commit real values - this repo is public. Set them in .env.
    s3_access_key: str = ""
    s3_secret_key: str = ""

    # Per-file upload cap. Enforced while streaming, so an oversize file never
    # gets fully written. Scene photos from a phone are 3-8MB.
    max_upload_mb: int = 25
```

- [ ] **Step 3: Write the failing contract tests**

Create `tests/test_storage.py`. The `storage` fixture is parametrised over both backends so `LocalStorage` is verified against the *same* assertions as `S3Storage` — that is what makes it trustworthy as the test stand-in. The S3 leg skips unless an endpoint is provided, keeping the default suite free of external services.

```python
import os
from pathlib import Path

import pytest

from app.storage.base import ObjectNotFound
from app.storage.local import LocalStorage


@pytest.fixture(params=["local", "s3"])
def storage(request, tmp_path):
    if request.param == "local":
        return LocalStorage(tmp_path / "objects")

    endpoint = os.environ.get("S3_TEST_ENDPOINT")
    if not endpoint:
        pytest.skip("set S3_TEST_ENDPOINT (e.g. http://localhost:9000) for the S3 leg")

    from app.config import Settings
    from app.storage.s3 import S3Storage

    return S3Storage(
        Settings(
            s3_endpoint=endpoint,
            s3_bucket=os.environ.get("S3_TEST_BUCKET", "ocr-test"),
            s3_access_key=os.environ["S3_TEST_ACCESS_KEY"],
            s3_secret_key=os.environ["S3_TEST_SECRET_KEY"],
        )
    )


@pytest.fixture
def source_file(tmp_path) -> Path:
    path = tmp_path / "payload.bin"
    path.write_bytes(b"pretend this is a jpeg")
    return path


def test_put_then_open_round_trips_the_bytes(storage, source_file):
    storage.put("batch-1/abc.png", source_file)
    with storage.open("batch-1/abc.png") as fh:
        assert fh.read() == b"pretend this is a jpeg"


def test_put_leaves_the_source_file_for_its_owner_to_delete(storage, source_file):
    storage.put("batch-1/abc.png", source_file)
    assert source_file.is_file()  # put copies; the caller owns its temp file


def test_as_local_path_yields_a_readable_file(storage, source_file):
    storage.put("batch-1/abc.png", source_file)
    with storage.as_local_path("batch-1/abc.png") as path:
        assert path.read_bytes() == b"pretend this is a jpeg"


def test_exists_and_delete(storage, source_file):
    assert storage.exists("batch-1/abc.png") is False
    storage.put("batch-1/abc.png", source_file)
    assert storage.exists("batch-1/abc.png") is True
    storage.delete("batch-1/abc.png")
    assert storage.exists("batch-1/abc.png") is False


def test_open_a_missing_key_raises_object_not_found(storage):
    with pytest.raises(ObjectNotFound):
        storage.open("batch-1/nope.png")


def test_as_local_path_on_a_missing_key_raises_object_not_found(storage):
    with pytest.raises(ObjectNotFound):
        with storage.as_local_path("batch-1/nope.png"):
            pass


def test_local_storage_rejects_a_key_escaping_its_root(tmp_path, source_file):
    storage = LocalStorage(tmp_path / "objects")
    with pytest.raises(ValueError, match="escapes the storage root"):
        storage.put("../../escaped.png", source_file)
    assert not (tmp_path / "escaped.png").exists()
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_storage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.storage'`

- [ ] **Step 5: Write the protocol**

Create `app/storage/base.py`:

```python
"""Where uploaded images live.

Two backends, resolved by get_storage() the same way get_engine() resolves
OcrEngine: a directory for development and tests, and any S3-compatible
object store (RustFS on-prem) for deployment.

as_local_path() is the load-bearing method. PaddleOCR wants a filesystem
path, and app/ocr/ is deliberately never told that object storage exists -
the S3 backend downloads to a temp file and cleans up, the local backend
yields the real path with no copy at all.
"""

from contextlib import AbstractContextManager
from pathlib import Path
from typing import BinaryIO, Protocol, runtime_checkable


class ObjectNotFound(Exception):
    """No object is stored under that key."""


@runtime_checkable
class Storage(Protocol):
    def put(self, key: str, source: Path) -> None:
        """Copy `source` in under `key`. The caller keeps ownership of `source`
        and is responsible for deleting it - both backends only read it."""

    def open(self, key: str) -> BinaryIO:
        """A readable, closable stream. Raises ObjectNotFound."""

    def as_local_path(self, key: str) -> AbstractContextManager[Path]:
        """A real filesystem path, valid for the duration of the context.
        Raises ObjectNotFound."""

    def delete(self, key: str) -> None:
        """Remove the object. Missing keys are not an error."""

    def exists(self, key: str) -> bool: ...
```

- [ ] **Step 6: Write the local backend**

Create `app/storage/local.py`:

```python
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from app.storage.base import ObjectNotFound


class LocalStorage:
    """Objects as files under one root directory."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _resolve(self, key: str) -> Path:
        """Map a key to a path, refusing anything that climbs out of the root.

        Keys are built from content hashes and so cannot traverse, but this is
        the one place where a key becomes a filesystem path - the check belongs
        here rather than in every caller.
        """
        root = self.root.resolve()
        path = (root / key).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"key escapes the storage root: {key}")
        return path

    def put(self, key: str, source: Path) -> None:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Copy, not move: put()'s contract is that the caller still owns
        # `source`, so the S3 and local backends behave identically. One extra
        # copy of a few MB is worth not having two ownership rules.
        shutil.copy2(source, target)

    def open(self, key: str) -> BinaryIO:
        path = self._resolve(key)
        try:
            return path.open("rb")
        except FileNotFoundError:
            raise ObjectNotFound(key) from None

    @contextmanager
    def as_local_path(self, key: str) -> Iterator[Path]:
        path = self._resolve(key)
        if not path.is_file():
            raise ObjectNotFound(key)
        yield path  # already a real file: no temp copy, no cleanup

    def delete(self, key: str) -> None:
        self._resolve(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()
```

- [ ] **Step 7: Write the S3 backend**

Create `app/storage/s3.py`:

```python
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from app.storage.base import ObjectNotFound

if TYPE_CHECKING:
    from app.config import Settings

_MISSING = {"404", "NoSuchKey", "NoSuchBucket"}


class S3Storage:
    """Any S3-compatible object store. On-prem this is RustFS.

    Path-style addressing is required: virtual-host addressing expects
    bucket.host DNS, which a self-hosted store on a service name does not have.
    """

    def __init__(self, settings: "Settings") -> None:
        self._bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
            ),
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        """Create the bucket on first use. No separate setup script or README
        step - `docker compose up -d rustfs` plus a normal app start is
        the whole on-prem setup."""
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code not in {"404", "NoSuchBucket"}:
                raise
            self._client.create_bucket(Bucket=self._bucket)

    def put(self, key: str, source: Path) -> None:
        self._client.upload_file(str(source), self._bucket, key)

    def open(self, key: str) -> BinaryIO:
        try:
            return self._client.get_object(Bucket=self._bucket, Key=key)["Body"]
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _MISSING:
                raise ObjectNotFound(key) from None
            raise

    @contextmanager
    def as_local_path(self, key: str) -> Iterator[Path]:
        suffix = Path(key).suffix
        handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        handle.close()
        temp = Path(handle.name)
        try:
            try:
                self._client.download_file(self._bucket, key, str(temp))
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in _MISSING:
                    raise ObjectNotFound(key) from None
                raise
            yield temp
        finally:
            temp.unlink(missing_ok=True)

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _MISSING:
                return False
            raise
        return True
```

- [ ] **Step 8: Write the resolver**

Create `app/storage/__init__.py`, mirroring `app/dispatch.py`'s shape so both singletons behave the same way (and are patched the same way in tests):

```python
from app.config import get_settings
from app.storage.base import ObjectNotFound, Storage

_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        settings = get_settings()
        if settings.storage_backend == "s3":
            from app.storage.s3 import S3Storage

            _storage = S3Storage(settings)
        else:
            from app.storage.local import LocalStorage

            _storage = LocalStorage(settings.images_dir)
    return _storage


__all__ = ["ObjectNotFound", "Storage", "get_storage"]
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `uv run pytest tests/test_storage.py -v`
Expected: PASS — 7 local tests pass, 6 S3-parametrised tests SKIP (no `S3_TEST_ENDPOINT`)

- [ ] **Step 10: Document the settings**

In `.env.example`:

```dotenv
# Where uploaded images go. local = a directory under DATA_DIR (default;
# needs nothing running). s3 = any S3-compatible store; on-prem that is
# RustFS, see docker-compose.yml.
STORAGE_BACKEND=local
S3_ENDPOINT=http://localhost:9000
S3_BUCKET=ocr-images
S3_REGION=us-east-1
# Placeholders. Put real credentials in .env, which is gitignored - this
# repository is public.
S3_ACCESS_KEY=rustfsadmin
S3_SECRET_KEY=CHANGE_ME

# Per-file upload cap, enforced while streaming.
MAX_UPLOAD_MB=25
```

- [ ] **Step 11: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add app/storage app/config.py tests/test_storage.py pyproject.toml uv.lock .env.example
git commit -m "feat: add a Storage abstraction with local and S3 backends"
```

---

### Task 2: Upload validation service

Still additive — `app/uploads.py` has no callers until Task 3.

**Files:**
- Create: `app/uploads.py`
- Modify: `pyproject.toml` (`python-multipart`)
- Test: `tests/test_uploads.py`

**Interfaces:**
- Consumes: `Storage`, `get_storage`, `ObjectNotFound` (Task 1); `app.service.IMAGE_SUFFIXES`; `app.models.Image`
- Produces:
  - `UploadRejected(Exception)` — message is reviewer-facing
  - `StoredUpload` frozen dataclass: `key: str`, `sha256: str`, `width: int`, `height: int`, `display_name: str`
  - `display_name_for(raw: str | None) -> str`
  - `key_for(batch_id: int, digest: str, suffix: str) -> str`
  - `store_upload(stream: BinaryIO, *, display_name: str, batch_id: int, storage: Storage, max_bytes: int) -> StoredUpload`
  - `register_image(session: Session, *, batch_id: int, stored: StoredUpload, storage: Storage) -> Image | None` (`None` = duplicate sha256)

- [ ] **Step 1: Add the multipart dependency**

FastAPI raises a cryptic startup error on any `File(...)` parameter without this. Verified absent: `uv pip list | grep -i multipart` returns nothing.

```bash
uv add python-multipart
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_uploads.py`:

```python
import io
from pathlib import Path

import pytest
from PIL import Image as PILImage

from app.storage.local import LocalStorage
from app.uploads import UploadRejected, display_name_for, store_upload


def png_bytes(width: int = 40, height: int = 20) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def storage(tmp_path) -> LocalStorage:
    return LocalStorage(tmp_path / "objects")


def store(storage, data: bytes, name: str, *, max_bytes: int = 1 << 20):
    return store_upload(
        io.BytesIO(data),
        display_name=name,
        batch_id=1,
        storage=storage,
        max_bytes=max_bytes,
    )


def test_key_is_the_content_hash_under_the_batch_prefix(storage):
    stored = store(storage, png_bytes(), "holiday snap.png")
    assert stored.key == f"batch-1/{stored.sha256}.png"
    assert storage.exists(stored.key)
    assert (stored.width, stored.height) == (40, 20)
    assert stored.display_name == "holiday snap.png"


def test_traversal_in_the_filename_cannot_escape_the_batch_prefix(storage, tmp_path):
    stored = store(storage, png_bytes(), "../../evil.png")
    assert stored.key.startswith("batch-1/")
    assert not (tmp_path / "evil.png").exists()


def test_rejects_a_non_image_with_an_image_extension(storage, tmp_path):
    with pytest.raises(UploadRejected, match="not a readable image"):
        store(storage, b"%PDF-1.7 this is not a png", "invoice.png")
    assert list((tmp_path / "objects").rglob("*")) == []


def test_rejects_an_unsupported_extension(storage):
    with pytest.raises(UploadRejected, match="unsupported file type"):
        store(storage, png_bytes(), "notes.pdf")


def test_rejects_a_file_over_the_cap(storage, tmp_path):
    with pytest.raises(UploadRejected, match="larger than"):
        store(storage, png_bytes(2000, 2000), "huge.png", max_bytes=64)
    assert list((tmp_path / "objects").rglob("*")) == []


def test_rejects_an_empty_file(storage):
    with pytest.raises(UploadRejected, match="empty file"):
        store(storage, b"", "nothing.png")


def test_leaves_no_temp_files_behind_on_success(storage, tmp_path):
    before = set(Path(tempfile.gettempdir()).glob("ocr-upload-*"))
    store(storage, png_bytes(), "ok.png")
    assert set(Path(tempfile.gettempdir()).glob("ocr-upload-*")) == before


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("photo.png", "photo.png"),
        ("../../etc/passwd.png", "passwd.png"),
        (r"C:\Users\me\shot.PNG", "shot.PNG"),
        # Arabic names survive: the corpus has them and this is display text.
        ("لافتة.png", "لافتة.png"),
        ("with space.png", "with space.png"),
        ("", "upload"),
        (None, "upload"),
        ("...", "upload"),
    ],
)
def test_display_name_is_sanitised(raw, expected):
    assert display_name_for(raw) == expected
```

Add `import tempfile` to the test file's imports for the temp-file assertion.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_uploads.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.uploads'`

- [ ] **Step 4: Write the implementation**

Create `app/uploads.py`:

```python
"""Accepting browser-uploaded images.

This is the trust boundary: filenames and bytes both come from the client.
Two rules follow, and they are enforced here rather than in the router:

* the storage key is derived from the content hash, never from the client
  string, which makes traversal and unicode-filename bugs impossible rather
  than merely guarded against;
* an extension allowlist does not prove a file is an image, so every upload
  is decoded before its row is created.

Uploads are streamed to a temp file first because hashing, the size cap and
the decode check all need the bytes before anything is stored - and because
that makes the flow identical for both storage backends.
"""

import hashlib
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from PIL import Image as PILImage
from sqlmodel import Session, select

from app.models import Image, ImageStatus
from app.service import IMAGE_SUFFIXES
from app.storage.base import Storage

CHUNK_BYTES = 1 << 20

# Strips path separators, control characters and the Windows-reserved set.
# Deliberately NOT an ASCII allowlist: this is an Arabic OCR tool, its corpus
# has Arabic filenames, and the result is only ever display text - the storage
# key comes from the content hash.
_UNSAFE = re.compile(r'[\x00-\x1f\x7f<>:"|?*\\/]+')


class UploadRejected(Exception):
    """One file could not be stored. The message is shown to the reviewer."""


@dataclass(frozen=True)
class StoredUpload:
    key: str
    sha256: str
    width: int
    height: int
    display_name: str


def display_name_for(raw: str | None) -> str:
    """Sanitise a client filename down to display text.

    Never used to build a key or a path - it only lands in Image.filename,
    which the UI shows and the .txt exporter uses as a stem. Windows clients
    send backslash separators, so strip both kinds.
    """
    candidate = (raw or "").replace("\\", "/").split("/")[-1]
    cleaned = _UNSAFE.sub("_", candidate).strip().lstrip(".")[:120]
    return cleaned or "upload"


def key_for(batch_id: int, digest: str, suffix: str) -> str:
    """Content-addressed, prefixed per batch so a batch is easy to sweep."""
    return f"batch-{batch_id}/{digest}{suffix}"


def _image_size(path: Path) -> tuple[int, int]:
    try:
        with PILImage.open(path) as img:
            img.verify()  # verify() leaves the object unusable, hence the reopen
        with PILImage.open(path) as img:
            return img.size
    except Exception:  # noqa: BLE001 - any decode failure means "not an image"
        raise UploadRejected("not a readable image") from None


def store_upload(
    stream: BinaryIO,
    *,
    display_name: str,
    batch_id: int,
    storage: Storage,
    max_bytes: int,
) -> StoredUpload:
    """Validate one upload and hand it to storage. Raises UploadRejected."""
    suffix = Path(display_name).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise UploadRejected(f"unsupported file type: {suffix or 'no extension'}")

    handle = tempfile.NamedTemporaryFile(prefix="ocr-upload-", suffix=suffix, delete=False)
    temp = Path(handle.name)
    digest = hashlib.sha256()
    written = 0
    try:
        with handle:
            while chunk := stream.read(CHUNK_BYTES):
                written += len(chunk)
                if written > max_bytes:
                    # Checked mid-stream: a cap enforced after the write has
                    # already spent the disk it was meant to protect.
                    raise UploadRejected(f"larger than the {max_bytes // (1 << 20)}MB limit")
                digest.update(chunk)  # same chunks, so the file is never read twice
                handle.write(chunk)
        if written == 0:
            raise UploadRejected("empty file")

        width, height = _image_size(temp)
        key = key_for(batch_id, digest.hexdigest(), suffix)
        storage.put(key, temp)
    finally:
        temp.unlink(missing_ok=True)  # put() copies, so the temp is ours to drop

    return StoredUpload(
        key=key,
        sha256=digest.hexdigest(),
        width=width,
        height=height,
        display_name=display_name,
    )


def register_image(
    session: Session,
    *,
    batch_id: int,
    stored: StoredUpload,
    storage: Storage,
) -> Image | None:
    """Create the Image row, or return None if these bytes were already imported.

    Image.sha256 is globally unique, so a duplicate may belong to a different
    batch. Callers surface that to the reviewer rather than dropping it silently.
    """
    existing = session.exec(select(Image).where(Image.sha256 == stored.sha256)).first()
    if existing is not None:
        # Identical bytes in the same batch produce an identical key, so the
        # put() above was an idempotent overwrite of the object `existing`
        # points at - deleting it then would orphan a live row.
        if existing.path != stored.key:
            storage.delete(stored.key)
        return None

    image = Image(
        batch_id=batch_id,
        path=stored.key,  # a storage key, not a filesystem path
        filename=stored.display_name,
        sha256=stored.sha256,
        width=stored.width,
        height=stored.height,
        status=ImageStatus.pending,
    )
    session.add(image)
    return image
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_uploads.py -v`
Expected: PASS (15 tests — 7 behaviours + 8 parametrised names)

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add app/uploads.py tests/test_uploads.py pyproject.toml uv.lock
git commit -m "feat: validate and store uploads by content hash"
```

---

### Task 3: Cut ingestion, OCR and serving over to storage

The atomic cutover. `Image.path` changes meaning here, so the write side, the read side and the serving side all move together — splitting them would leave a commit where OCR cannot find its own images.

**Files:**
- Modify: `app/routers/batches.py:1-59`, `app/schemas.py:8-24`, `app/service.py` (delete dead code, `run_ocr_for_image`), `app/routers/images.py:29-40`
- Modify: `tests/conftest.py`, `tests/test_api_images.py:5-11`, `tests/test_api_lines.py:5-12`, `tests/test_exporters.py:6-14`
- Create: `tests/test_api_upload.py`
- Delete: `tests/test_api_import.py` (its `test_health` moves)

**Interfaces:**
- Consumes: everything from Tasks 1 and 2
- Produces:
  - `POST /api/batches` — body `{"name": str}` → `BatchOut`, 201
  - `POST /api/batches/{batch_id}/images` — multipart, repeated `files` → `UploadResult`, 201
  - `UploadFailure{filename: str, reason: str}`, `UploadResult{imported: int, skipped: int, failed: list[UploadFailure]}`
  - `BatchCreate` loses `source_dir`; `BatchOut` loses `skipped_count`
  - `upload` pytest fixture: `upload(name: str, paths: list[Path]) -> tuple[dict, dict]`

- [ ] **Step 1: Update the schemas**

In `app/schemas.py`, replace `BatchCreate` and `BatchOut` (lines 8-24):

```python
class BatchCreate(BaseModel):
    name: str


class BatchOut(BaseModel):
    id: int
    name: str
    # The storage prefix this batch's objects live under. Informational for
    # operators; never a client-supplied value.
    source_dir: str
    created_at: datetime
    image_count: int = 0
    done_count: int = 0
    approved_count: int = 0
    failed_count: int = 0


class UploadFailure(BaseModel):
    filename: str
    reason: str


class UploadResult(BaseModel):
    imported: int
    skipped: int  # bytes already imported, possibly into another batch
    failed: list[UploadFailure] = []
```

- [ ] **Step 2: Add the test fixtures**

In `tests/conftest.py`, extend the `client` fixture to reset the storage singleton, then add the `upload` helper. Without the reset, a storage built during one test's `tmp_path` leaks into the next.

Inside `client`, after `monkeypatch.setattr(dispatch, "_engine", FakeOcrEngine())`:

```python
    from app import storage as storage_module

    # Same singleton reset as dispatch._engine: LocalStorage captures
    # images_dir, which is tmp_path-specific.
    monkeypatch.setattr(storage_module, "_storage", None)
```

Then, after the `client` fixture:

```python
@pytest.fixture
def upload(client):
    """Create a batch and upload files to it through the API.

    Returns (batch_json, upload_result_json). Batches are created empty now,
    so image_count on the create response is always 0 - read the upload
    result, or re-GET the batch, for counts.
    """

    def do(name: str, paths: list) -> tuple[dict, dict]:
        batch = client.post("/api/batches", json={"name": name})
        assert batch.status_code == 201, batch.text
        files = [("files", (p.name, p.read_bytes(), "image/png")) for p in paths]
        result = client.post(f"/api/batches/{batch.json()['id']}/images", files=files)
        assert result.status_code == 201, result.text
        return batch.json(), result.json()

    return do
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_api_upload.py`:

```python
import io

from PIL import Image as PILImage


def distinct_png(index: int) -> bytes:
    """Blank images of the same size are byte-identical and would deduplicate."""
    img = PILImage.new("RGB", (1000, 400), "white")
    img.putpixel((index, 0), (0, 0, 0))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_batch_is_created_empty_and_names_its_storage_prefix(client):
    resp = client.post("/api/batches", json={"name": "batch-1"})
    assert resp.status_code == 201
    batch = resp.json()
    assert batch["image_count"] == 0
    assert batch["source_dir"] == f"batch-{batch['id']}"


def test_uploaded_images_are_ocred_and_serve_their_lines(client, source_dir):
    batch = client.post("/api/batches", json={"name": "b"}).json()
    files = [
        ("files", (p.name, p.read_bytes(), "image/png"))
        for p in sorted(source_dir.glob("*.png"))
    ]
    resp = client.post(f"/api/batches/{batch['id']}/images", files=files)
    assert resp.status_code == 201
    assert resp.json() == {"imported": 2, "skipped": 0, "failed": []}

    images = client.get(f"/api/batches/{batch['id']}/images").json()
    assert [i["status"] for i in images] == ["done", "done"]
    assert [i["filename"] for i in images] == ["one.png", "two.png"]

    detail = client.get(f"/api/images/{images[0]['id']}").json()
    assert detail["width"] == 1000
    assert [ln["reading_order"] for ln in detail["lines"]] == [0, 1, 2, 3]
    assert detail["lines"][1]["rec_text"] == "السطر"  # rightmost of its band

    raw = client.get(f"/api/images/{images[0]['id']}/file")
    assert raw.status_code == 200
    assert raw.headers["content-type"] == "image/png"
    assert raw.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_missing_object_still_reports_410(client, session_factory):
    """The API never exposes the storage key, so read it from the database."""
    from app.models import Image
    from app.storage import get_storage

    batch = client.post("/api/batches", json={"name": "gone"}).json()
    client.post(
        f"/api/batches/{batch['id']}/images",
        files=[("files", ("a.png", distinct_png(2), "image/png"))],
    )
    image_id = client.get(f"/api/batches/{batch['id']}/images").json()[0]["id"]

    with session_factory() as session:
        key = session.get(Image, image_id).path
    get_storage().delete(key)

    assert client.get(f"/api/images/{image_id}/file").status_code == 410


def test_reuploading_the_same_bytes_is_skipped_not_duplicated(client):
    first = client.post("/api/batches", json={"name": "a"}).json()
    second = client.post("/api/batches", json={"name": "b"}).json()
    payload = [("files", ("shot.png", distinct_png(3), "image/png"))]

    assert client.post(f"/api/batches/{first['id']}/images", files=payload).json()["imported"] == 1
    again = client.post(f"/api/batches/{second['id']}/images", files=payload).json()
    assert again == {"imported": 0, "skipped": 1, "failed": []}
    assert client.get(f"/api/batches/{second['id']}/images").json() == []


def test_one_bad_file_does_not_fail_the_whole_upload(client):
    batch = client.post("/api/batches", json={"name": "mixed"}).json()
    resp = client.post(
        f"/api/batches/{batch['id']}/images",
        files=[
            ("files", ("good.png", distinct_png(5), "image/png")),
            ("files", ("invoice.png", b"%PDF-1.7 not a png", "image/png")),
            ("files", ("notes.pdf", b"whatever", "application/pdf")),
        ],
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["imported"] == 1
    assert [f["filename"] for f in body["failed"]] == ["invoice.png", "notes.pdf"]
    assert "not a readable image" in body["failed"][0]["reason"]
    assert len(client.get(f"/api/batches/{batch['id']}/images").json()) == 1


def test_traversal_filename_stays_inside_the_batch_prefix(client, tmp_path):
    batch = client.post("/api/batches", json={"name": "evil"}).json()
    resp = client.post(
        f"/api/batches/{batch['id']}/images",
        files=[("files", ("../../escaped.png", distinct_png(7), "image/png"))],
    )
    assert resp.json()["imported"] == 1
    objects_root = tmp_path / "data" / "images"
    assert [p.name for p in objects_root.iterdir()] == [f"batch-{batch['id']}"]
    assert not (tmp_path / "escaped.png").exists()
    image = client.get(f"/api/batches/{batch['id']}/images").json()[0]
    assert image["filename"] == "escaped.png"


def test_upload_to_a_missing_batch_is_404(client):
    resp = client.post(
        "/api/batches/999/images",
        files=[("files", ("a.png", distinct_png(1), "image/png"))],
    )
    assert resp.status_code == 404


def test_oversize_file_is_reported_per_file(client, monkeypatch):
    # Patch the router's seam rather than the MAX_UPLOAD_MB env var: the
    # `client` fixture has already built the app and warmed get_settings'
    # lru_cache, so clearing it mid-test to change one field is a trap.
    from app.routers import batches

    monkeypatch.setattr(batches, "_max_upload_bytes", lambda: 64)
    batch = client.post("/api/batches", json={"name": "tiny-cap"}).json()
    resp = client.post(
        f"/api/batches/{batch['id']}/images",
        files=[("files", ("big.png", distinct_png(9), "image/png"))],
    )
    assert resp.status_code == 201
    assert resp.json()["imported"] == 0
    assert "larger than" in resp.json()["failed"][0]["reason"]
```

**Do not add an assertion comparing `Image.path` to a filesystem path.** It is a storage key now (`batch-1/3f2a….png`), meaningful only to the `Storage` backend.

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_api_upload.py -v`
Expected: FAIL — `POST /api/batches` still requires `source_dir` (422), `/images` does not exist (405)

- [ ] **Step 5: Rewrite the batch endpoints**

In `app/routers/batches.py`, replace the imports (lines 1-13):

```python
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlmodel import Session, func, select

from app.config import get_settings
from app.db import SessionDep
from app.dispatch import enqueue_image
from app.exporters import export_jsonl, export_txt
from app.models import Batch, Image, ImageStatus
from app.schemas import BatchCreate, BatchOut, ImageOut, UploadFailure, UploadResult
from app.storage import get_storage
from app.uploads import UploadRejected, display_name_for, register_image, store_upload
```

Then replace `create_batch` (lines 33-59). `UploadFiles` follows the same `Annotated` shape as `SessionDep` in `app/db.py` — this codebase fixed B008 that way instead of suppressing it. `_max_upload_bytes` exists as a seam the API test patches, which keeps tests out of `get_settings`'s `lru_cache`:

```python
UploadFiles = Annotated[list[UploadFile], File()]


def _max_upload_bytes() -> int:
    return get_settings().max_upload_mb * (1 << 20)


@router.post("", response_model=BatchOut, status_code=status.HTTP_201_CREATED)
def create_batch(payload: BatchCreate, session: SessionDep) -> BatchOut:
    """Create an empty batch. Images arrive separately, via POST .../images.

    Two commits because source_dir is derived from the row's own id, which
    only exists after the insert.
    """
    batch = Batch(name=payload.name, source_dir="")
    session.add(batch)
    session.commit()
    session.refresh(batch)

    batch.source_dir = f"batch-{batch.id}"  # the storage prefix, not a path
    session.add(batch)
    session.commit()
    session.refresh(batch)
    return BatchOut(**batch.model_dump(), **_counts(session, batch.id))


@router.post(
    "/{batch_id}/images",
    response_model=UploadResult,
    status_code=status.HTTP_201_CREATED,
)
def upload_images(batch_id: int, session: SessionDep, files: UploadFiles) -> UploadResult:
    """Store uploaded images and queue each new one for OCR.

    Per-file outcomes rather than a single status code: a 200-file upload must
    not be rejected wholesale because one file was a PDF.
    """
    if session.get(Batch, batch_id) is None:
        raise HTTPException(status_code=404, detail="batch not found")

    storage = get_storage()
    max_bytes = _max_upload_bytes()

    stored_images: list[Image] = []
    skipped = 0
    failed: list[UploadFailure] = []

    for upload in files:
        display_name = display_name_for(upload.filename)
        try:
            stored = store_upload(
                upload.file,
                display_name=display_name,
                batch_id=batch_id,
                storage=storage,
                max_bytes=max_bytes,
            )
        except UploadRejected as exc:
            failed.append(UploadFailure(filename=display_name, reason=str(exc)))
            continue

        image = register_image(session, batch_id=batch_id, stored=stored, storage=storage)
        if image is None:
            skipped += 1
            continue
        image.status = ImageStatus.queued
        stored_images.append(image)

    session.commit()

    # Enqueue exactly the rows this request created. Selecting every queued row
    # of the batch instead would re-enqueue anything an earlier upload left
    # queued, which under the Celery backend means OCRing it twice.
    for image in stored_images:
        enqueue_image(image.id)  # readable post-commit: the row refreshes on access

    return UploadResult(imported=len(stored_images), skipped=skipped, failed=failed)
```

- [ ] **Step 6: Point OCR at storage and delete the dead folder-import code**

In `app/service.py`, delete `sha256_of`, `_candidate_files` and `import_folder` entirely, along with the now-unused `hashlib`, `PILImage`, `Batch` and `select`-of-`Batch` imports (keep `select`, `run_ocr_for_image` still uses it). Verified by grep: `import_folder`'s only caller was the `create_batch` endpoint replaced in Step 5, and `scripts/bench_ocr.py` never used it. Keep `IMAGE_SUFFIXES` — `app/uploads.py` imports it.

Then change the engine call in `run_ocr_for_image`:

```python
    started = time.perf_counter()
    try:
        # image.path is a storage key. as_local_path gives PaddleOCR the real
        # filesystem path it wants without app/ocr/ ever knowing about object
        # storage; for the local backend it is the file itself, no copy.
        with get_storage().as_local_path(image.path) as local_path:
            result = engine.run(local_path)
    except Exception as exc:  # noqa: BLE001 - the message is shown to the reviewer
        image.status = ImageStatus.failed
        image.error = f"{type(exc).__name__}: {exc}"
        image.updated_at = utcnow()
        session.commit()
        return
```

Add the import at the top of `app/service.py`:

```python
from app.storage import get_storage
```

- [ ] **Step 7: Serve image bytes from storage**

In `app/routers/images.py`, replace `get_image_file` (lines 29-40) and its imports. Proxying rather than redirecting to a presigned URL is deliberate: on-prem the API reaches the store by service name, but the browser generally cannot, and a presigned URL to an unreachable host is a broken image with no error message.

Imports become:

```python
import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import select

from app.db import SessionDep
from app.models import Image, ImageStatus, Line, utcnow
from app.schemas import ImageDetailOut, ImageOut, ImageUpdate, LineOut
from app.storage import ObjectNotFound, get_storage
```

```python
@router.get("/{image_id}/file")
def get_image_file(image_id: int, session: SessionDep) -> StreamingResponse:
    image = session.get(Image, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="image not found")
    try:
        stream = get_storage().open(image.path)
    except ObjectNotFound:
        # 410 rather than 500: a vanished object is the reviewer's only signal
        # that the stored image is gone, and it is not a server fault.
        raise HTTPException(status_code=410, detail=f"stored image is gone: {image.path}") from None
    media_type = mimetypes.guess_type(Path(image.path).name)[0] or "application/octet-stream"
    return StreamingResponse(stream, media_type=media_type)
```

- [ ] **Step 8: Migrate the three existing test helpers**

`tests/test_api_images.py` lines 5-11:

```python
@pytest.fixture
def image_id(client, upload, tmp_path) -> int:
    path = tmp_path / "a.png"
    PILImage.new("RGB", (1000, 400), "white").save(path)
    batch, _ = upload("b", [path])
    return client.get(f"/api/batches/{batch['id']}/images").json()[0]["id"]
```

`tests/test_api_lines.py` lines 5-12:

```python
@pytest.fixture
def client_with_lines(client, upload, tmp_path):
    path = tmp_path / "a.png"
    PILImage.new("RGB", (1000, 400), "white").save(path)
    batch, _ = upload("b", [path])
    image_id = client.get(f"/api/batches/{batch['id']}/images").json()[0]["id"]
    return client, client.get(f"/api/images/{image_id}").json()
```

`tests/test_exporters.py` lines 6-14:

```python
def _seed(client, upload, tmp_path) -> int:
    path = tmp_path / "page-1.png"
    PILImage.new("RGB", (1000, 400), "white").save(path)
    batch, _ = upload("export-me", [path])
    image = client.get(f"/api/batches/{batch['id']}/images").json()[0]
    detail = client.get(f"/api/images/{image['id']}").json()
    client.patch(f"/api/lines/{detail['lines'][0]['id']}", json={"corrected_text": "نص مصحح"})
    return batch["id"]
```

Every `_seed(client, tmp_path)` call site gains the fixture — `_seed(client, upload, tmp_path)` — and each test function's signature gains `upload`. Find them with:

```bash
grep -n "_seed(client" tests/test_exporters.py
```

If a JSONL assertion checks the `path` field, update it: the value is now a storage key (`batch-1/<sha256>.png`), not an absolute path. The field keeps its name because export consumers may already parse it.

- [ ] **Step 9: Delete the superseded test file**

`test_health` moved into `tests/test_api_upload.py`; the rest tested the host-path import that no longer exists.

```bash
git rm tests/test_api_import.py
```

- [ ] **Step 10: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS, with the S3 legs of `test_storage.py` skipped.

- [ ] **Step 11: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add app tests
git commit -m "feat: upload images to object storage instead of importing a host path"
```

---

### Task 4: Delete the native picker

**Files:**
- Delete: `app/routers/picker.py`
- Modify: `app/main.py:7` and `:25`

Note the frontend's `pickPath` is removed in Task 5, where the form that calls it is rewritten. Deleting the backend first is safe: the browse buttons simply error until then, and no test covers them.

- [ ] **Step 1: Check what references it**

```bash
grep -rn "picker\|osascript\|pickPath" app/ frontend/src/ tests/ scripts/
```
Expected: `app/routers/picker.py`, two lines in `app/main.py`, and `pickPath` in `frontend/src/api/client.ts` + `BatchList.tsx`.

- [ ] **Step 2: Remove the router**

```bash
git rm app/routers/picker.py
```

In `app/main.py` line 7:

```python
from app.routers import batches, images, lines
```

and delete line 25:

```python
    app.include_router(picker.router)
```

- [ ] **Step 3: Verify the app still boots**

Run: `uv run pytest -v && uv run python -c "import app.main"`
Expected: PASS, no ImportError

- [ ] **Step 4: Commit**

```bash
uv run ruff check .
git add app/main.py app/routers
git commit -m "refactor: drop the macOS osascript picker, uploads replace it"
```

---

### Task 5: Frontend upload UI with progress

**Files:**
- Create: `frontend/src/api/uploads.ts`, `frontend/src/api/uploads.test.ts`
- Modify: `frontend/src/api/client.ts:21-25` and `:48-49`, `frontend/src/api/types.ts`, `frontend/src/components/BatchList.tsx`, `frontend/src/App.tsx:34`, `frontend/src/styles.css:90-93`

**Interfaces:**
- Consumes: `POST /api/batches` `{name}` and `POST /api/batches/{id}/images` (Task 3)
- Produces:
  - `createBatch(name: string) => Promise<BatchDto>` (signature change: `sourceDir` gone)
  - `uploadImages(batchId, files, onProgress) => Promise<UploadResultDto>`
  - `chunk<T>(items: T[], size: number): T[][]`
  - `imageFilesFrom(list: FileList | null): File[]`
  - `summarizeUpload(result: UploadResultDto): string`
  - `UploadResultDto`, `UploadFailureDto`
  - `BatchList` prop change: `{ activeId, onPick }` — the indicator itself lands in Task 6

- [ ] **Step 1: Write the failing tests for the pure helpers**

Create `frontend/src/api/uploads.test.ts`. Vitest runs `environment: "node"` with no jsdom here, so only pure functions are unit-tested; components are verified in the browser.

```ts
import { describe, expect, it } from "vitest";
import { chunk, imageFilesFrom, summarizeUpload } from "./uploads";

const named = (...names: string[]) => names.map((name) => ({ name })) as unknown as FileList;

describe("chunk", () => {
  it("splits into groups of at most size", () => {
    expect(chunk([1, 2, 3, 4, 5], 2)).toEqual([[1, 2], [3, 4], [5]]);
  });

  it("returns nothing for an empty list", () => {
    expect(chunk([], 20)).toEqual([]);
  });

  it("rejects a size below one rather than looping forever", () => {
    expect(() => chunk([1], 0)).toThrow(/at least 1/);
  });
});

describe("imageFilesFrom", () => {
  it("keeps images and drops everything else", () => {
    const files = imageFilesFrom(named("a.png", "b.JPG", ".DS_Store", "notes.pdf", "c.webp"));
    expect(files.map((f) => f.name)).toEqual(["a.png", "b.JPG", "c.webp"]);
  });

  it("handles a null list", () => {
    expect(imageFilesFrom(null)).toEqual([]);
  });
});

describe("summarizeUpload", () => {
  it("reports the happy path", () => {
    expect(summarizeUpload({ imported: 3, skipped: 0, failed: [] })).toBe("3 uploaded");
  });

  it("explains why duplicates were skipped", () => {
    expect(summarizeUpload({ imported: 1, skipped: 2, failed: [] })).toBe(
      "1 uploaded · 2 skipped (already imported)",
    );
  });

  it("names every rejected file and its reason", () => {
    const text = summarizeUpload({
      imported: 0,
      skipped: 0,
      failed: [{ filename: "x.png", reason: "not a readable image" }],
    });
    expect(text).toBe("0 uploaded · 1 rejected: x.png (not a readable image)");
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/api/uploads.test.ts`
Expected: FAIL — cannot resolve `./uploads`

- [ ] **Step 3: Write the helpers and the XHR upload**

Create `frontend/src/api/uploads.ts`:

```ts
import type { UploadResultDto } from "./types";

const IMAGE_SUFFIXES = [".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"];

/** Files per request. Keeps one failed request from losing a whole folder,
 *  and gives the progress bar something to advance between. */
export const UPLOAD_CHUNK_SIZE = 20;

export function chunk<T>(items: T[], size: number): T[][] {
  if (size < 1) throw new Error("chunk size must be at least 1");
  const groups: T[][] = [];
  for (let i = 0; i < items.length; i += size) groups.push(items.slice(i, i + size));
  return groups;
}

/** A folder pick returns everything in the tree - .DS_Store, sidecar files,
 *  thumbnails. Filter client-side so the server is not asked about junk. */
export function imageFilesFrom(list: FileList | null): File[] {
  return Array.from(list ?? []).filter((file) =>
    IMAGE_SUFFIXES.some((suffix) => file.name.toLowerCase().endsWith(suffix)),
  );
}

export function summarizeUpload(result: UploadResultDto): string {
  const parts = [`${result.imported} uploaded`];
  if (result.skipped > 0) parts.push(`${result.skipped} skipped (already imported)`);
  if (result.failed.length > 0) {
    const detail = result.failed.map((f) => `${f.filename} (${f.reason})`).join(", ");
    parts.push(`${result.failed.length} rejected: ${detail}`);
  }
  return parts.join(" · ");
}

/** Upload one chunk. XMLHttpRequest rather than fetch(): fetch cannot report
 *  upload progress at all, and a folder of scene photos takes long enough
 *  that a bar is the difference between "working" and "frozen". */
export function uploadImages(
  batchId: number,
  files: File[],
  onProgress: (fraction: number) => void,
): Promise<UploadResultDto> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    for (const file of files) form.append("files", file, file.name);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/batches/${batchId}/images`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as UploadResultDto);
      } else {
        reject(new Error(`POST /api/batches/${batchId}/images -> ${xhr.status}`));
      }
    };
    xhr.onerror = () => reject(new Error("upload failed: network error"));
    xhr.send(form);
  });
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/api/uploads.test.ts`
Expected: PASS (8 tests)

- [ ] **Step 5: Update the API types**

In `frontend/src/api/types.ts`, remove `skipped_count` from `BatchDto` (it no longer exists server-side) and add:

```ts
export type UploadFailureDto = { filename: string; reason: string };

export type UploadResultDto = {
  imported: number;
  skipped: number;
  failed: UploadFailureDto[];
};
```

- [ ] **Step 6: Update the API client**

In `frontend/src/api/client.ts`, replace `createBatch` (lines 21-25), delete `pickPath` (lines 48-49), and re-export the upload helper so callers have one import site:

```ts
export const createBatch = (name: string) =>
  json<BatchDto>("/api/batches", { method: "POST", body: JSON.stringify({ name }) });
```

```ts
export { uploadImages } from "./uploads";
```

- [ ] **Step 7: Rewrite the batch form**

Replace `frontend/src/components/BatchList.tsx` entirely:

```tsx
import { useEffect, useRef, useState } from "react";
import { createBatch, exportBatch, getBatches, uploadImages } from "../api/client";
import { UPLOAD_CHUNK_SIZE, chunk, imageFilesFrom, summarizeUpload } from "../api/uploads";
import type { BatchDto, UploadFailureDto } from "../api/types";

// React's InputHTMLAttributes has no webkitdirectory, but it is a real
// attribute Chromium and WebKit honour, and the browser walks the tree so the
// server never has to.
const DIRECTORY_ATTRS = {
  webkitdirectory: "",
  directory: "",
} as unknown as React.InputHTMLAttributes<HTMLInputElement>;

type Progress = { sent: number; total: number; fraction: number };

export function BatchList({
  activeId,
  onPick,
}: {
  activeId: number | null;
  onPick: (batchId: number) => void;
}) {
  const [batches, setBatches] = useState<BatchDto[]>([]);
  const [name, setName] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Bumped after a submit so picking the same folder again still fires change.
  const [inputKey, setInputKey] = useState(0);
  const nameTouched = useRef(false);

  const refresh = () => getBatches().then(setBatches).catch(() => {});

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 3000); // progress ticks while OCR runs
    return () => clearInterval(timer);
  }, []);

  function choose(list: FileList | null) {
    const picked = imageFilesFrom(list);
    setFiles(picked);
    setNotice(
      list && picked.length < list.length
        ? `${list.length - picked.length} non-image file(s) ignored`
        : null,
    );
    if (!nameTouched.current && picked.length > 0) {
      // webkitRelativePath is "folder/sub/file.png" for a directory pick.
      const folder = picked[0].webkitRelativePath?.split("/")[0];
      setName(folder || picked[0].name.replace(/\.[^.]+$/, ""));
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setNotice(null);
    const total = files.length;
    let imported = 0;
    let skipped = 0;
    const failed: UploadFailureDto[] = [];
    let sent = 0;

    try {
      const batch = await createBatch(name.trim());
      for (const group of chunk(files, UPLOAD_CHUNK_SIZE)) {
        const result = await uploadImages(batch.id, group, (fraction) =>
          setProgress({ sent, total, fraction }),
        );
        imported += result.imported;
        skipped += result.skipped;
        failed.push(...result.failed);
        sent += group.length;
        setProgress({ sent, total, fraction: 1 });
      }
      setNotice(summarizeUpload({ imported, skipped, failed }));
      setName("");
      setFiles([]);
      nameTouched.current = false;
      setInputKey((k) => k + 1);
      await refresh();
      onPick(batch.id);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err));
    } finally {
      setProgress(null);
    }
  }

  async function runExport(batchId: number, format: "jsonl" | "txt") {
    try {
      const res = await exportBatch(batchId, format);
      setNotice(`Wrote ${res.path}`);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err));
    }
  }

  const uploading = progress !== null;
  const barFraction = progress
    ? (progress.sent +
        progress.fraction * Math.min(UPLOAD_CHUNK_SIZE, progress.total - progress.sent)) /
      Math.max(progress.total, 1)
    : 0;

  return (
    <aside className="batch-list">
      <form onSubmit={submit} className="batch-form">
        <label className="file-pick">
          <span>🖼 Choose images</span>
          <input
            key={`files-${inputKey}`}
            type="file"
            multiple
            accept="image/*"
            disabled={uploading}
            onChange={(e) => choose(e.target.files)}
          />
        </label>
        <label className="file-pick">
          <span>📁 Choose a folder</span>
          <input
            key={`dir-${inputKey}`}
            type="file"
            multiple
            disabled={uploading}
            onChange={(e) => choose(e.target.files)}
            {...DIRECTORY_ATTRS}
          />
        </label>
        {files.length > 0 && <p className="muted">{files.length} image(s) ready</p>}
        <input
          value={name}
          onChange={(e) => {
            nameTouched.current = true;
            setName(e.target.value);
          }}
          placeholder="Batch name"
          required
        />
        <button disabled={uploading || files.length === 0 || !name.trim()}>
          {uploading ? "Uploading…" : "Upload"}
        </button>
        {progress && (
          <div className="upload-progress">
            <div className="batch-bar">
              <span style={{ width: `${barFraction * 100}%` }} />
            </div>
            <p className="muted">
              {progress.sent}/{progress.total} uploaded
            </p>
          </div>
        )}
      </form>
      {notice && <p className="batch-notice">{notice}</p>}
      {batches.map((b) => (
        <div key={b.id} className="batch-entry">
          <button
            className={`batch-item${b.id === activeId ? " batch-item-active" : ""}`}
            aria-current={b.id === activeId}
            onClick={() => onPick(b.id)}
          >
            <span>{b.name}</span>
            <span className="muted">
              {b.done_count + b.approved_count}/{b.image_count} done
              {b.failed_count > 0 ? ` · ${b.failed_count} failed` : ""}
            </span>
          </button>
          <div className="batch-exports">
            <button onClick={() => runExport(b.id, "jsonl")}>JSONL</button>
            <button onClick={() => runExport(b.id, "txt")}>.txt</button>
          </div>
        </div>
      ))}
    </aside>
  );
}
```

- [ ] **Step 8: Keep the tree compiling**

`BatchList` now requires `activeId`. In `frontend/src/App.tsx:34`, pass it — Task 6 builds the visual treatment on top:

```tsx
      <BatchList
        activeId={batchId}
        onPick={(id) => {
          setBatchId(id);
          setImageId(null);
        }}
      />
```

- [ ] **Step 9: Replace the picker CSS**

In `frontend/src/styles.css`, replace the `.browse-row` block (lines 90-93):

```css
.file-pick { display: flex; flex-direction: column; gap: 4px; font-size: 12px;
  color: var(--muted); }
.file-pick input[type="file"] { font-size: 11px; color: var(--fg); }
.upload-progress { display: flex; flex-direction: column; gap: 4px; }
.batch-bar { height: 4px; width: 100%; background: #262a31; border-radius: 999px;
  overflow: hidden; }
.batch-bar > span { display: block; height: 100%; background: var(--accent);
  transition: width 120ms linear; }
```

- [ ] **Step 10: Typecheck, test and commit**

```bash
cd frontend && npx tsc --noEmit && npx vitest run && npx oxlint src && cd ..
git add frontend/src
git commit -m "feat: upload images from the browser with a progress bar"
```

---

### Task 6: Selected-batch indicator and OCR progress bar

**Files:**
- Modify: `frontend/src/components/BatchList.tsx` (batch row), `frontend/src/styles.css:37-40`

- [ ] **Step 1: Add the OCR progress bar to each batch row**

In `frontend/src/components/BatchList.tsx`, replace the batch row `<button>` so the done/total count is joined by a bar:

```tsx
          <button
            className={`batch-item${b.id === activeId ? " batch-item-active" : ""}`}
            aria-current={b.id === activeId}
            onClick={() => onPick(b.id)}
          >
            <span>{b.name}</span>
            <span className="muted">
              {b.done_count + b.approved_count}/{b.image_count} done
              {b.failed_count > 0 ? ` · ${b.failed_count} failed` : ""}
            </span>
            <div className="batch-bar">
              <span
                style={{
                  width: `${
                    b.image_count === 0
                      ? 0
                      : ((b.done_count + b.approved_count) / b.image_count) * 100
                  }%`,
                }}
              />
            </div>
          </button>
```

- [ ] **Step 2: Style the active row**

In `frontend/src/styles.css`, after the `.batch-item` rule (lines 37-39). The outline matches `.strip-active` so selection reads the same way in both lists:

```css
.batch-item { width: 100%; }
.batch-item-active { outline: 2px solid var(--accent); background: #1b2027; }
```

- [ ] **Step 3: Verify in the browser**

```bash
./scripts/dev.sh
```

Open <http://localhost:5173>, upload `tests/data/pack1/*.png`, then click between two batches. Expected: the clicked row keeps a visible accent outline and lighter background; its bar fills as images finish. Tab through the list too — the focus ring and the selection outline must be distinguishable from each other.

- [ ] **Step 4: Typecheck and commit**

```bash
cd frontend && npx tsc --noEmit && npx oxlint src && cd ..
git add frontend/src
git commit -m "feat: show which batch is selected and how far its OCR has got"
```

---

### Task 7: Auto-refresh the review pane while OCR runs

The "no F5" fix. Root cause: `ReviewPage.tsx:22-29` fetches the image detail exactly once.

**Files:**
- Create: `frontend/src/api/status.ts`, `frontend/src/api/status.test.ts`
- Modify: `frontend/src/pages/ReviewPage.tsx:1-29` and its render block, `frontend/src/styles.css:64`

**Interfaces:**
- Produces: `isProcessing(status: ImageStatus | undefined): boolean`, `REVIEW_POLL_MS: number`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/api/status.test.ts`:

```ts
import { expect, it } from "vitest";
import { isProcessing } from "./status";

it("polls only while the image has not reached a terminal status", () => {
  expect(isProcessing("pending")).toBe(true);
  expect(isProcessing("queued")).toBe(true);
  expect(isProcessing("running")).toBe(true);
  expect(isProcessing("done")).toBe(false);
  expect(isProcessing("failed")).toBe(false);
  expect(isProcessing("approved")).toBe(false);
  expect(isProcessing(undefined)).toBe(false);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/api/status.test.ts`
Expected: FAIL — cannot resolve `./status`

- [ ] **Step 3: Write the predicate**

Create `frontend/src/api/status.ts`:

```ts
import type { ImageStatus } from "./types";

const ACTIVE: ImageStatus[] = ["pending", "queued", "running"];

export const REVIEW_POLL_MS = 1500;

/** True while OCR may still change this image.
 *
 * Polling MUST stop at a terminal status: a poll response replaces
 * image.lines wholesale, and once lines exist the reviewer is editing them -
 * a late refresh would silently discard an in-flight correction.
 */
export const isProcessing = (status: ImageStatus | undefined): boolean =>
  status !== undefined && ACTIVE.includes(status);
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/api/status.test.ts`
Expected: PASS

- [ ] **Step 5: Split the fetch and poll effects**

In `frontend/src/pages/ReviewPage.tsx`, add the import:

```tsx
import { REVIEW_POLL_MS, isProcessing } from "../api/status";
```

Replace the single `useEffect` (lines 22-29) with two. The poll lives in its own effect keyed on the status, so a poll result does not tear down and recreate its own timer:

```tsx
  useEffect(() => {
    setImage(null);
    setError(null);
    select(null);
    getImage(imageId)
      .then(setImage)
      .catch((e: Error) => setError(e.message));
  }, [imageId, select]);

  // OCR is often still queued or running when the reviewer clicks an image,
  // and the fetch above would then be the only one - which is why the old UI
  // needed an F5. Re-fetch until the status is terminal, then stop.
  useEffect(() => {
    if (!isProcessing(image?.status)) return;
    const timer = setInterval(() => {
      getImage(imageId)
        .then(setImage)
        .catch(() => {}); // a transient failure just means the next tick retries
    }, REVIEW_POLL_MS);
    return () => clearInterval(timer);
  }, [imageId, image?.status]);
```

- [ ] **Step 6: Tell the reviewer the pane is live**

Insert a banner as the first child of `<div className="review-shell">`, before `<Toolbar …>`.

Render it **always** and toggle `hidden`, rather than conditionally rendering it. `.review-shell` is a grid: a child that appears and disappears shifts every later child into a different row, so the split pane would get the `auto` row instead of `1fr` half the time — the same failure mode as the earlier `.review-split { height: 100vh }` bug. `[hidden]` keeps the row assignment constant.

```tsx
      <p className="processing-note" hidden={!isProcessing(image.status)}>
        OCR running — this view updates itself.
      </p>
```

Add to `frontend/src/styles.css`:

```css
.processing-note { margin: 0; padding: 4px 12px; font-size: 12px; color: var(--muted);
  border-block-end: 1px solid #262a31; }
.processing-note[hidden] { display: none; }
```

The explicit `[hidden]` rule is there because a grid child can ignore the default `hidden` behaviour.

`.review-shell` needs a third row so the banner does not eat the split pane's height. Place this after the existing `.review-shell` rule on line 64 so it overrides the `grid-template-rows` half of it:

```css
.review-shell { grid-template-rows: auto auto 1fr; }
```

- [ ] **Step 7: Verify the F5 complaint is actually fixed**

```bash
./scripts/dev.sh
```

With the worker running, upload a folder and **immediately** click the first image in the strip, while it still shows `queued`. Expected: the banner appears, then lines populate on their own within a couple of seconds. No reload, no deselect/reselect.

Then confirm the stop condition: with an image `done`, type a correction into a line, wait 5 seconds, and check the text is still there. If polling had not stopped, it would have been overwritten.

- [ ] **Step 8: Typecheck, test and commit**

```bash
cd frontend && npx tsc --noEmit && npx vitest run && npx oxlint src && cd ..
git add frontend/src
git commit -m "fix: refresh the review pane while OCR runs instead of needing F5"
```

---

### Task 8: RustFS service and documentation

**Files:**
- Modify: `docker-compose.yml`, `README.md`, `.env.example`, `app/config.py:15-24`

No new script and no extra manual step: `S3Storage._ensure_bucket()` (Task 1) creates the bucket on first use, so setup is `docker compose up -d rustfs` followed by a normal app start — nothing else to run or document.

- [ ] **Step 1: Add RustFS to compose**

In `docker-compose.yml`, alongside the existing `redis` service. Credentials come from `.env` with obvious-placeholder defaults — this repository is public, so no real value belongs here:

```yaml
  rustfs:
    image: rustfs/rustfs:latest
    container_name: arabic-ocr-rustfs
    ports:
      - "9000:9000"   # S3 API
      - "9001:9001"   # web console
    environment:
      RUSTFS_ACCESS_KEY: ${S3_ACCESS_KEY:-rustfsadmin}
      RUSTFS_SECRET_KEY: ${S3_SECRET_KEY:-rustfsadmin}
    volumes:
      - rustfs-data:/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/health"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  rustfs-data:
```

Merge the `volumes:` key with any that already exists rather than adding a second one. Verify the env var names and the health endpoint against the image's own docs before committing — they are the two things most likely to differ from this sketch:

```bash
docker compose up -d rustfs && docker compose logs rustfs | tail -30
```

- [ ] **Step 2: Mark the VL engine experimental in config**

In `app/config.py`, replace the `ocr_engine` comment block (lines 15-24):

```python
    # Which OcrEngine to build.
    #
    # "paddle" (default, the only supported value for production) = the
    # detector+CRNN pipeline configured below: arabic_PP-OCRv5_mobile_rec for
    # recognition, PP-OCRv5_server_det for detection.
    #
    # "paddle_vl" = PaddleOCR-VL, a ~1B-param vision-language model.
    # EXPERIMENTAL, not for on-prem deployment: it hangs on this Mac's CPU
    # (see README) and at ~1B params it is too heavy for batch throughput on a
    # large corpus. Kept wired up because it sits behind the OcrEngine
    # protocol and costs nothing to leave in place.
    ocr_engine: str = "paddle"
```

- [ ] **Step 3: Mark it experimental in `.env.example`**

```dotenv
# paddle = supported. paddle_vl = EXPERIMENTAL, hangs on CPU and is too heavy
# for large-scale batch OCR; do not use on-prem. See README.
OCR_ENGINE=paddle
```

- [ ] **Step 4: Document storage in the README**

Replace the paragraph about Redis-only services (around line 34-37) by adding after it:

```markdown
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
```

- [ ] **Step 5: Rewrite the Review workflow section**

In `README.md`, replace steps 1-2 of "Review workflow" (lines 152-156):

```markdown
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
```

- [ ] **Step 6: Soften the VL section's opening**

Replace the first sentence of item 2 in "Two real upgrade paths" (lines 73-74):

```markdown
2. **PaddleOCR-VL** — wired in (`app/ocr/paddle_vl_engine.py`,
   `OCR_ENGINE=paddle_vl`) but **experimental and not used in production**: at
   ~1B params it is far too slow for a large corpus on CPU, and it **does not
   currently work on this Mac at all**.
```

- [ ] **Step 7: Check the docs match reality**

```bash
grep -rn "source_dir\|Import folder\|/path/to/images" README.md
```
Expected: nothing telling the reviewer to type a path.

- [ ] **Step 8: Optional — verify the S3 backend end to end**

Worth doing once, since the default test suite never exercises S3:

```bash
docker compose up -d rustfs
S3_TEST_ENDPOINT=http://localhost:9000 \
  S3_TEST_BUCKET=ocr-images \
  S3_TEST_ACCESS_KEY=rustfsadmin \
  S3_TEST_SECRET_KEY=rustfsadmin \
  uv run pytest tests/test_storage.py -v
```

Expected: the previously-skipped S3 legs now pass, with the same assertions the local backend passes.

Then run the app itself against it — this is the only step that proves the on-prem path works:

```bash
STORAGE_BACKEND=s3 ./scripts/dev.sh
```

Upload two images and confirm they OCR and display.

- [ ] **Step 9: Commit**

```bash
git add docker-compose.yml README.md .env.example app/config.py
git commit -m "docs: RustFS storage service, upload workflow, paddle_vl marked experimental"
```

---

## Self-Review

**1. Spec coverage**

| Requirement | Task |
|---|---|
| R1 upload replaces host paths | 2 (validation), 3 (endpoints), 4 (picker deleted) |
| R1 multi-file **and** folder select | 5, Step 7 (`DIRECTORY_ATTRS`) |
| R2 `Storage` protocol, two backends | 1 |
| R2 `local` default, no service needed for tests/dev | 1, Steps 2 and 8; verified 3, Step 10 |
| R2 `as_local_path` keeps `app/ocr/` untouched | 1, Steps 5-7; used 3, Step 6 |
| R3 hash-derived keys | 2, Step 4 (`key_for`, `store_upload`) |
| R3 key cannot escape the root | 1, Step 6 (`_resolve`) + Step 3 test; 3, Step 3 test |
| R3 streaming with mid-stream cap | 2, Step 4 |
| R3 real-image verification | 2, Step 4 (`_image_size`) |
| R3 `MAX_UPLOAD_MB` | 1, Step 2; 3, Step 5 (`_max_upload_bytes`) |
| R3 credentials never committed | 1, Steps 2 and 10; 8, Step 1 |
| R4 per-file outcomes | 3, Steps 1, 3, 5; surfaced by 5's `summarizeUpload` |
| R4 duplicate explained, not silent | 5, Step 3; 8, Step 6 |
| R5 serving proxies from storage | 3, Step 7 |
| R5 missing object keeps 410 | 3, Step 3 test + Step 7 |
| R5 OCR reads via `as_local_path` | 3, Step 6 |
| R6 selected batch visible | 5, Step 7 (class) + 6, Steps 1-2 (treatment) |
| R7 upload progress | 5, Steps 3 and 7 |
| R7 OCR progress per batch | 6, Step 1 |
| R8 no F5 | 7 |
| R8 polling stops at terminal status | 7, Steps 1, 3, 5; verified Step 7 |
| R9 model defaults documented, VL parked | 8, Steps 3, 4, 7 |
| Consequence: dead `import_folder` deleted | 3, Step 6 |
| Consequence: `Image.path` is a key | 3, Steps 6-8; documented 8, Step 5 |

No gaps.

**2. Placeholder scan** — no TBDs, no "similar to Task N", every code step carries the code to write.

**3. Type consistency**

- `Storage` protocol methods (`put`, `open`, `as_local_path`, `delete`, `exists`) — defined Task 1 Step 5, implemented Steps 6-7, consumed in Task 2 Step 4 (`put`, `delete`), Task 3 Step 6 (`as_local_path`) and Step 7 (`open`).
- `StoredUpload` fields (`key`, `sha256`, `width`, `height`, `display_name`) — defined Task 2 Step 4, consumed Task 3 Step 5.
- `ObjectNotFound` — raised by both backends (Task 1 Steps 6-7), caught in Task 3 Step 7, asserted in Task 1 Step 3 and Task 3 Step 3.
- `UploadResult{imported, skipped, failed}` — server schema (Task 3 Step 1) matches `UploadResultDto` (Task 5 Step 5) and `summarizeUpload`'s parameter (Task 5 Step 3).
- `UploadFailure{filename, reason}` ↔ `UploadFailureDto{filename, reason}`.
- `BatchList` props `{activeId, onPick}` — declared Task 5 Step 7, supplied Task 5 Step 8, styled Task 6.
- `isProcessing` / `REVIEW_POLL_MS` — defined Task 7 Step 3, used Steps 5-6.
- `_max_upload_bytes` — defined Task 3 Step 5, patched by the test in Task 3 Step 3.
- `BatchOut.skipped_count` removed server-side (Task 3 Step 1) **and** from `BatchDto` (Task 5 Step 5); `BatchList` only reads `done_count`, `approved_count`, `image_count`, `failed_count`.
- `batch-{id}` prefix — produced by `key_for` (Task 2 Step 4) and by `create_batch`'s `source_dir` (Task 3 Step 5). These must agree; the Task 3 Step 3 test asserts both.

## Known follow-ups (not in this plan)

- **Dockerfiles and the full app compose stack** — api/worker/frontend images, the CUDA variant. Only the RustFS service joins compose here.
- **Presigned URLs** — would take image bytes off the API process, but needs the browser to reach the store directly. Revisit if proxying becomes a bottleneck.
- **Resumable uploads** — a dropped connection mid-folder means re-selecting and re-uploading. Duplicates are hash-skipped, so a retry is cheap and correct, just not resumable.
- **Fine-tuning export** — the JSONL export names objects by key; a training export would need to pull the bytes and write `image → label` pairs to a directory.
- **Per-batch dedup** — `Image.sha256` is globally unique, so the same photo cannot be reviewed in two batches. Changing that needs migration tooling this project does not have.
- **Bucket lifecycle/retention** — no expiry policy is set; objects live until deleted.
- **No ground-truth accuracy harness** — confidence is not accuracy. CER/WER against reviewer-corrected text would make the fine-tuning case measurable.
