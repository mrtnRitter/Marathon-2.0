"""Versioned API routers with centralized browser-session protection."""

from fastapi import APIRouter, Depends

from marathon.server.api import assets, auth, detail, events, fs, read, report, settings, system, users, worker, workers, workflows
from marathon.server.api.deps import current_user

api_prefix = "/api/v1"

protected_router = APIRouter(dependencies=[Depends(current_user)])
for router in (assets.router, detail.router, events.router, fs.router, read.router, report.router, settings.router, workers.router, workflows.router, users.router):
    protected_router.include_router(router)

# Authentication and worker-token endpoints retain their own independent guards.
api_routers = (protected_router, system.router, worker.router, auth.router)
