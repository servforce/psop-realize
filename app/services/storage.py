from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings, settings


@dataclass(frozen=True, slots=True)
class StoredObject:
    bucket: str
    object_key: str
    media_type: str
    size_bytes: int
    checksum: str


class StorageService:
    def __init__(self, settings_: Settings = settings) -> None:
        self.settings = settings_
        self._client = None

    def upload_bytes(self, *, object_key: str, content: bytes, media_type: str, bucket: str | None = None) -> StoredObject:
        checksum = hashlib.sha256(content).hexdigest()
        if self.settings.storage_backend == "minio":
            target_bucket = bucket or self.settings.object_store_bucket
            self._put_minio(
                bucket=target_bucket,
                object_key=object_key,
                content=content,
                media_type=media_type,
                checksum=checksum,
            )
        else:
            path = self._local_path(object_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            target_bucket = "local"
        return StoredObject(
            bucket=target_bucket,
            object_key=object_key,
            media_type=media_type,
            size_bytes=len(content),
            checksum=checksum,
        )

    def upload_file(self, *, object_key: str, path: Path, media_type: str, bucket: str | None = None) -> StoredObject:
        return self.upload_bytes(object_key=object_key, content=path.read_bytes(), media_type=media_type, bucket=bucket)

    def download_file(self, *, bucket: str, object_key: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.settings.storage_backend == "minio" and bucket != "local":
            self._get_client().download_file(bucket, object_key, str(path))
            return
        path.write_bytes(self._local_path(object_key).read_bytes())

    def get_bytes(self, *, bucket: str, object_key: str) -> bytes:
        if self.settings.storage_backend == "minio" and bucket != "local":
            response = self._get_client().get_object(Bucket=bucket, Key=object_key)
            body = response.get("Body")
            if body is None:
                return b""
            try:
                return body.read()
            finally:
                body.close()
        return self._local_path(object_key).read_bytes()

    def url_for(self, object_key: str) -> str:
        return f"/api/objects/{object_key}"

    def _local_path(self, object_key: str) -> Path:
        safe_key = object_key.replace("\\", "/").lstrip("/")
        return Path(self.settings.local_storage_root) / safe_key

    def _put_minio(self, *, bucket: str, object_key: str, content: bytes, media_type: str, checksum: str) -> None:
        client = self._get_client()
        self._ensure_bucket(client, bucket)
        client.put_object(
            Bucket=bucket,
            Key=object_key,
            Body=content,
            ContentType=media_type,
            Metadata={"sha256": checksum},
        )

    def _get_client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "s3",
                endpoint_url=self.settings.object_store_endpoint,
                aws_access_key_id=self.settings.object_store_access_key,
                aws_secret_access_key=self.settings.object_store_secret_key,
                region_name=self.settings.object_store_region,
                use_ssl=self.settings.object_store_secure,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                    proxies={},
                    connect_timeout=10,
                    read_timeout=30,
                    retries={"max_attempts": 2},
                ),
            )
        return self._client

    def _ensure_bucket(self, client, bucket: str) -> None:
        try:
            client.head_bucket(Bucket=bucket)
        except Exception:
            client.create_bucket(Bucket=bucket)


storage_service = StorageService()
