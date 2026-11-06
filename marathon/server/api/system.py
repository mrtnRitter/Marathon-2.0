"""System endpoints."""

from fastapi import APIRouter

from marathon.common.version import app_name, app_version

router = APIRouter(tags=["system"])


@router.get("/health")
def get_health() -> dict[str, str]:
    return {"status": "ok", "app": app_name, "version": app_version}
