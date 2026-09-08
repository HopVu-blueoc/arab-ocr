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
    with pytest.raises(ObjectNotFound), storage.as_local_path("batch-1/nope.png"):
        pass


def test_local_storage_rejects_a_key_escaping_its_root(tmp_path, source_file):
    storage = LocalStorage(tmp_path / "objects")
    with pytest.raises(ValueError, match="escapes the storage root"):
        storage.put("../../escaped.png", source_file)
    assert not (tmp_path / "escaped.png").exists()
