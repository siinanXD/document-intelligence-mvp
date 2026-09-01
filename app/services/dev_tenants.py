"""Local/CI tenant bootstrap for the cockpit demo path.

Production authentication is not this header. These helpers 404 unless
ENVIRONMENT is local or ci.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.models import Tenant

ALLOWED_ENVIRONMENTS = frozenset({"local", "ci"})


def dev_tenants_enabled() -> bool:
    return get_settings().environment in ALLOWED_ENVIRONMENTS


async def get_tenant_by_slug(session: AsyncSession, *, slug: str) -> Tenant | None:
    result = await session.execute(select(Tenant).where(Tenant.slug == slug))
    return result.scalars().first()


async def get_or_create_tenant(
    session: AsyncSession, *, slug: str, name: str
) -> tuple[Tenant, bool]:
    existing = await get_tenant_by_slug(session, slug=slug)
    if existing is not None:
        return existing, False
    tenant = Tenant(slug=slug, name=name)
    session.add(tenant)
    await session.flush()
    return tenant, True
