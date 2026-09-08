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
