"""MinIO/S3 storage client — upload, dedup, presign.

MD5 hash = object key, so identical files are stored once.
All blocking boto3 calls are wrapped in asyncio.to_thread.
"""
import asyncio
import hashlib
import logging
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from config import settings

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _client():
    scheme = "https" if settings.MINIO_SECURE else "http"
    return boto3.client(
        "s3",
        endpoint_url=f"{scheme}://{settings.MINIO_ENDPOINT}",
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def _md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _exists(key: str) -> bool:
    try:
        _client().head_object(Bucket=settings.MINIO_BUCKET, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return False
        raise


def _upload(path: str, key: str) -> None:
    _client().upload_file(
        path, settings.MINIO_BUCKET, key,
        ExtraArgs={"ContentType": "audio/mpeg"},
    )


def _presign(key: str, expires: int) -> str:
    url: str = _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.MINIO_BUCKET, "Key": key},
        ExpiresIn=expires,
    )
    if settings.MINIO_PUBLIC_URL:
        scheme = "https" if settings.MINIO_SECURE else "http"
        internal = f"{scheme}://{settings.MINIO_ENDPOINT}"
        url = url.replace(internal, settings.MINIO_PUBLIC_URL.rstrip("/"), 1)
    return url


def _init_bucket() -> None:
    """Create bucket (idempotent) and set CORS."""
    try:
        _client().create_bucket(Bucket=settings.MINIO_BUCKET)
        log.info("S3 bucket created: %s", settings.MINIO_BUCKET)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code not in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
            log.warning("S3 create_bucket: %s", e)

    try:
        _client().put_bucket_cors(
            Bucket=settings.MINIO_BUCKET,
            CORSConfiguration={
                "CORSRules": [{
                    "AllowedHeaders": ["*"],
                    "AllowedMethods": ["GET", "HEAD"],
                    "AllowedOrigins": ["*"],
                    "MaxAgeSeconds": 3600,
                }]
            },
        )
        log.info("S3 CORS set for bucket %s", settings.MINIO_BUCKET)
    except Exception as e:
        log.warning("S3 CORS setup failed: %s", e)


async def setup() -> None:
    """Initialize bucket + CORS on bot startup."""
    try:
        await asyncio.to_thread(_init_bucket)
    except Exception as e:
        log.warning("S3 setup failed (MinIO may not be ready): %s", e)


async def upload_if_needed(path: str) -> str:
    """MD5-hash file, upload to MinIO if not already stored. Returns object_key."""
    key = await asyncio.to_thread(_md5, path)
    exists = await asyncio.to_thread(_exists, key)
    if not exists:
        await asyncio.to_thread(_upload, path, key)
        log.info("S3 uploaded key=%s", key)
    else:
        log.debug("S3 dedup hit key=%s", key)
    return key


async def get_presigned_url(key: str, expires: int = 7200) -> str:
    """Generate a presigned GET URL for the given object key."""
    return await asyncio.to_thread(_presign, key, expires)
