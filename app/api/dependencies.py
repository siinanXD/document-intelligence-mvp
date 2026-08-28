"""Shared request dependencies."""

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.models import Tenant
from app.providers.registry import get_storage_backend
from app.providers.storage import StorageBackend


async def get_session() -> AsyncIterator[AsyncSession]:
    """A session per request, committed on success and rolled back on error."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_tenant(
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
) -> Tenant:
    """Resolve the calling tenant from the X-Tenant-Id header.

    This is identification, not authentication: the header is trusted because
    nothing yet issues credentials. Real authentication arrives with the
    hardening work, and this is the single place that has to change.
    """
    if not x_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="X-Tenant-Id header is required"
        )
    try:
        tenant_id = uuid.UUID(x_tenant_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="X-Tenant-Id must be a UUID"
        ) from exc

    result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalars().first()
    if tenant is None:
        # Deliberately the same shape as any other unknown tenant, so this
        # cannot be used to probe which tenant ids exist.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown tenant")
    return tenant


def get_storage() -> StorageBackend:
    return get_storage_backend()


SessionDep = Annotated[AsyncSession, Depends(get_session)]
TenantDep = Annotated[Tenant, Depends(get_tenant)]
StorageDep = Annotated[StorageBackend, Depends(get_storage)]
