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
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
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
