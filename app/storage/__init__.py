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
