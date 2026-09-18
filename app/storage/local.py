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

    def delete_many(self, keys: list[str]) -> None:
        # No batch syscall to exploit; the loop is the whole implementation.
        for key in keys:
            self.delete(key)

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()
