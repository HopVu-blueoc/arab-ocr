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

    # delete=False: the path must outlive this block for _image_size() and
    # storage.put() below, so it can't be opened as a `with` here directly.
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
        prefix="ocr-upload-", suffix=suffix, delete=False
    )
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
