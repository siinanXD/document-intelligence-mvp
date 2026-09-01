"""Read-only cockpit metadata for the Next.js UI."""

from fastapi import APIRouter

from app.services.cockpit import cockpit_snapshot

router = APIRouter(tags=["system"])


@router.get("/meta/cockpit")
async def get_cockpit() -> dict:
    """Health, named providers and the checked-in evaluation gate.

    No tenant header: this is process metadata, not customer data. Secrets,
    URLs and evaluation passages are not included.
    """
    return await cockpit_snapshot()
