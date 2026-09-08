import io
import tempfile
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
