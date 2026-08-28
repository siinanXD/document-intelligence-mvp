"""Both storage backends against one shared contract.

The local backend runs for real against a temporary directory. The S3 backend
runs against a fake client that mimics boto3's shapes, including the error
responses that distinguish "missing" from "broken" - so the tests need no
credentials and make no network call.
"""

import pytest

from app.providers.local_storage import LocalStorageBackend
from app.providers.s3_storage import S3StorageBackend
from app.providers.storage import ObjectNotFoundError


class _FakeS3Error(Exception):
    def __init__(self, code: str, status_code: int = 400) -> None:
        super().__init__(code)
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": status_code},
        }


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}

    def put_object(self, *, Bucket, Key, Body, ContentType=None):  # noqa: N803
        self.objects[Key] = Body
        if ContentType:
            self.content_types[Key] = ContentType

    def get_object(self, *, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise _FakeS3Error("NoSuchKey", 404)
        return {"Body": _FakeBody(self.objects[Key])}

    def head_object(self, *, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise _FakeS3Error("404", 404)
        return {}

    def delete_object(self, *, Bucket, Key):  # noqa: N803
        self.objects.pop(Key, None)


@pytest.fixture
def local_backend(tmp_path):
    return LocalStorageBackend(tmp_path)


@pytest.fixture
def s3_client():
    return _FakeS3Client()


@pytest.fixture
def s3_backend(s3_client):
    return S3StorageBackend(client=s3_client, bucket="documents")


@pytest.fixture(params=["local", "s3"])
def backend(request, local_backend, s3_backend):
    """Every contract test runs against both implementations."""
    return local_backend if request.param == "local" else s3_backend


async def test_an_object_reads_back_exactly_as_written(backend):
    await backend.put("tenant/doc/file.pdf", b"%PDF-1.7 body")

    assert await backend.get("tenant/doc/file.pdf") == b"%PDF-1.7 body"


async def test_writing_the_same_key_twice_overwrites(backend):
    await backend.put("k", b"first")
    await backend.put("k", b"second")

    assert await backend.get("k") == b"second"


async def test_exists_reflects_what_is_stored(backend):
    assert await backend.exists("k") is False

    await backend.put("k", b"data")

    assert await backend.exists("k") is True


async def test_reading_a_missing_key_raises_object_not_found(backend):
    with pytest.raises(ObjectNotFoundError):
        await backend.get("nothing-here")


async def test_delete_removes_the_object(backend):
    await backend.put("k", b"data")

    await backend.delete("k")

    assert await backend.exists("k") is False


async def test_deleting_a_missing_key_is_not_an_error(backend):
    await backend.delete("never-existed")


async def test_binary_content_survives_a_round_trip(backend):
    payload = bytes(range(256))

    await backend.put("bin", payload)

    assert await backend.get("bin") == payload


async def test_local_backend_refuses_a_key_that_escapes_its_root(local_backend):
    """A key is untrusted input, whatever produced it."""
    with pytest.raises(ValueError, match="escapes the storage root"):
        await local_backend.put("../outside.txt", b"nope")
    with pytest.raises(ValueError, match="escapes the storage root"):
        await local_backend.get("../../etc/passwd")


async def test_local_backend_creates_nested_directories(local_backend, tmp_path):
    await local_backend.put("a/b/c/file.txt", b"data")

    assert (tmp_path / "a" / "b" / "c" / "file.txt").read_bytes() == b"data"


async def test_local_backend_leaves_no_partial_file_behind(local_backend, tmp_path):
    await local_backend.put("k.txt", b"data")

    assert [p.name for p in tmp_path.iterdir()] == ["k.txt"]


async def test_s3_backend_passes_the_content_type_through(s3_backend, s3_client):
    await s3_backend.put("k", b"data", content_type="application/pdf")

    assert s3_client.content_types["k"] == "application/pdf"


async def test_s3_backend_does_not_swallow_a_real_failure(s3_backend, s3_client):
    def _explode(**kwargs):
        raise _FakeS3Error("AccessDenied", 403)

    s3_client.get_object = _explode

    # A permissions problem must not be reported as a missing object.
    with pytest.raises(_FakeS3Error):
        await s3_backend.get("k")
