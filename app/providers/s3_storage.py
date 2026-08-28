"""S3-compatible object storage, used in production.

boto3 is synchronous, so every call runs in a worker thread rather than
blocking the event loop. The client is injected, which is what lets the tests
drive this against a fake without credentials or a network.
"""

import asyncio
from typing import Any

from app.providers.storage import ObjectNotFoundError, StorageBackend


class S3StorageBackend(StorageBackend):
    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def _is_missing(self, error: Exception) -> bool:
        code = getattr(error, "response", {}).get("Error", {}).get("Code")
        status = getattr(error, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
        return code in {"404", "NoSuchKey", "NotFound"} or status == 404

    async def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        kwargs: dict[str, Any] = {"Bucket": self._bucket, "Key": key, "Body": data}
        if content_type:
            kwargs["ContentType"] = content_type
        await asyncio.to_thread(lambda: self._client.put_object(**kwargs))

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            try:
                response = self._client.get_object(Bucket=self._bucket, Key=key)
            except Exception as exc:
                if self._is_missing(exc):
                    raise ObjectNotFoundError(key) from exc
                raise
            return response["Body"].read()

        return await asyncio.to_thread(_get)

    async def delete(self, key: str) -> None:
        # S3 delete is already idempotent; a missing key is not an error.
        await asyncio.to_thread(lambda: self._client.delete_object(Bucket=self._bucket, Key=key))

    async def exists(self, key: str) -> bool:
        def _head() -> bool:
            try:
                self._client.head_object(Bucket=self._bucket, Key=key)
            except Exception as exc:
                if self._is_missing(exc):
                    return False
                raise
            return True

        return await asyncio.to_thread(_head)


def build_s3_client(
    *,
    endpoint_url: str | None,
    region: str | None,
    access_key_id: str,
    secret_access_key: str,
) -> Any:
    """Construct a real boto3 S3 client. Never called from tests."""
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=region,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
    )
