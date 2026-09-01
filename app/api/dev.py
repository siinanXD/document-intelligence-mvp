"""Development-only tenant bootstrap.

X-Tenant-Id is identification, not authentication. These routes exist so the
local cockpit can create a demo tenant. They 404 outside local/ci.
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.dependencies import SessionDep
from app.services import dev_tenants

router = APIRouter(prefix="/dev", tags=["dev"])


class DevTenantRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    name: str = Field(min_length=1, max_length=255)


class DevTenantResponse(BaseModel):
    id: str
    slug: str
    name: str
    created: bool


def _require_dev() -> None:
    if not dev_tenants.dev_tenants_enabled():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")


@router.post("/tenants", response_model=DevTenantResponse)
async def create_dev_tenant(session: SessionDep, request: DevTenantRequest) -> DevTenantResponse:
    _require_dev()
    tenant, created = await dev_tenants.get_or_create_tenant(
        session, slug=request.slug, name=request.name
    )
    return DevTenantResponse(id=str(tenant.id), slug=tenant.slug, name=tenant.name, created=created)


@router.get("/tenants/{slug}", response_model=DevTenantResponse)
async def get_dev_tenant(session: SessionDep, slug: str) -> DevTenantResponse:
    _require_dev()
    tenant = await dev_tenants.get_tenant_by_slug(session, slug=slug)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown tenant")
    return DevTenantResponse(id=str(tenant.id), slug=tenant.slug, name=tenant.name, created=False)
