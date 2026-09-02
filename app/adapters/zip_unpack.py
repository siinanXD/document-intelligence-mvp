"""Safe zip unpack for package intake. Original archive bytes stay immutable."""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass

from app.adapters.base import PackageRejected

MAX_MEMBERS = 256
MAX_UNCOMPRESSED_TOTAL = 80 * 1024 * 1024
MAX_UNCOMPRESSED_MEMBER = 50 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
_RATIO_FLOOR = 256
_MAX_PATH = 240


@dataclass(frozen=True)
class ArchiveMember:
    path_hint: str
    content: bytes
    file_hash: str
    nested_archive: bool


def member_storage_key(*, tenant_id, document_id, path_hint: str) -> str:
    """Tenant-scoped key for an unpacked member. Path is a sanitised hint."""
    return f"{tenant_id}/{document_id}/members/{path_hint}"


def assert_zip_directory_within_limits(archive: zipfile.ZipFile) -> None:
    """Raise ValueError if the zip directory describes a bomb.

    Inspects declared sizes only; members are not decompressed.
    """
    try:
        infos = [info for info in archive.infolist() if not _is_directory(info)]
    except zipfile.BadZipFile as exc:
        raise ValueError("zip archive is malformed") from exc
    if len(infos) > MAX_MEMBERS:
        raise ValueError("zip archive has too many members")
    total = 0
    for info in infos:
        if info.file_size > MAX_UNCOMPRESSED_MEMBER:
            raise ValueError("zip member exceeds size limit")
        compressed = info.compress_size or 1
        if (
            info.file_size >= _RATIO_FLOOR
            and compressed >= 1
            and info.file_size / compressed > MAX_COMPRESSION_RATIO
        ):
            raise ValueError("zip compression ratio exceeds limit")
        total += info.file_size
        if total > MAX_UNCOMPRESSED_TOTAL:
            raise ValueError("zip expanded size exceeds limit")


def safe_unpack(content: bytes) -> tuple[ArchiveMember, ...]:
    """Return archive members after rejecting traversal, bombs and empties."""
    if not content.startswith(b"PK"):
        raise PackageRejected("file contents are not a zip archive")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise PackageRejected("zip archive is malformed") from None

    members: list[ArchiveMember] = []
    total = 0
    try:
        infos = archive.infolist()
    except zipfile.BadZipFile:
        raise PackageRejected("zip archive is malformed") from None

    file_infos = [info for info in infos if not _is_directory(info)]
    if not file_infos:
        raise PackageRejected("zip archive has no files")
    if len(file_infos) > MAX_MEMBERS:
        raise PackageRejected("zip archive has too many members")

    seen: set[str] = set()
    for info in file_infos:
        if info.flag_bits & 0x1:
            raise PackageRejected("encrypted zip members are not accepted")
        path_hint = _safe_path(info.filename)
        if path_hint in seen:
            raise PackageRejected("zip archive has duplicate member paths")
        seen.add(path_hint)
        if info.file_size > MAX_UNCOMPRESSED_MEMBER:
            raise PackageRejected("zip member exceeds size limit")
        compressed = info.compress_size or 1
        if (
            info.file_size >= _RATIO_FLOOR
            and compressed >= 1
            and info.file_size / compressed > MAX_COMPRESSION_RATIO
        ):
            raise PackageRejected("zip compression ratio exceeds limit")
        total += info.file_size
        if total > MAX_UNCOMPRESSED_TOTAL:
            raise PackageRejected("zip expanded size exceeds limit")
        try:
            payload = archive.read(info)
        except zipfile.BadZipFile:
            raise PackageRejected("zip member could not be read") from None
        if len(payload) != info.file_size:
            raise PackageRejected("zip member size does not match directory")
        nested = payload.startswith(b"PK\x03\x04") and path_hint.lower().endswith(".zip")
        members.append(
            ArchiveMember(
                path_hint=path_hint,
                content=payload,
                file_hash=hashlib.sha256(payload).hexdigest(),
                nested_archive=nested,
            )
        )
    return tuple(members)


def _is_directory(info: zipfile.ZipInfo) -> bool:
    name = info.filename.replace("\\", "/")
    return bool(info.is_dir() or name.endswith("/"))


def _safe_path(name: str) -> str:
    raw = name.replace("\\", "/")
    if raw.startswith("/") or raw.startswith("\\"):
        raise PackageRejected("zip member path is absolute")
    parts: list[str] = []
    for part in raw.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise PackageRejected("zip member path escapes the archive")
        if ":" in part:
            raise PackageRejected("zip member path is absolute")
        parts.append(part)
    if not parts:
        raise PackageRejected("zip member path is empty")
    joined = "/".join(parts)
    if len(joined) > _MAX_PATH:
        raise PackageRejected("zip member path is too long")
    return joined
