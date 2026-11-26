"""Public endpoint for first-time worker enrollment."""

# IMPORTS
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

from marathon.server.api.deps import get_engine
from marathon.server.config import WorkerClientConfig
from marathon.server.worker_auth import hash_token


# MODELS
class EnrollInput(BaseModel):
    """Metadata submitted by a new worker client."""

    hostname: str
    platform: str
    client_version: str


# CONFIG
router = APIRouter(prefix="/worker", tags=["worker"])
_client_version_pattern = re.compile(r"^\d+\.\d+\.\d+$")


# FUNC
@router.post("/enroll")
def enroll_worker(
    payload: EnrollInput, request: Request, engine: Engine = Depends(get_engine)
) -> dict[str, str | bool]:
    """Create a worker identity and return its token once."""
    hostname = payload.hostname.strip()
    if not hostname:
        raise HTTPException(status_code=422, detail="Rechnername fehlt")
    if len(hostname) > 100:
        raise HTTPException(status_code=422, detail="Rechnername ist zu lang (max. 100 Zeichen)")
    if len(payload.platform) > 50:
        raise HTTPException(status_code=422, detail="Plattform ist zu lang (max. 50 Zeichen)")
    if not _client_version_pattern.fullmatch(payload.client_version):
        raise HTTPException(status_code=422, detail="Ungültige Client-Version")

    worker_id = f"w_{secrets.token_hex(8)}"
    worker_token = secrets.token_urlsafe(32)
    ip_address = request.client.host if request.client is not None else None
    worker_client: WorkerClientConfig = request.app.state.worker_client
    statement = text(
        "INSERT INTO workers (id, name, status, capabilities, skills, online, client_version, "
        "ip_address, last_seen, created_at, token_hash, approved) "
        "VALUES (:id, :name, 'stopped', :capabilities, :skills, false, :client_version, "
        ":ip_address, NULL, now(), :token_hash, :approved)"
    ).bindparams(
        bindparam("capabilities", type_=JSONB),
        bindparam("skills", type_=JSONB),
    )
    with engine.begin() as connection:
        connection.execute(
            statement,
            {
                "id": worker_id,
                "name": hostname,
                "capabilities": [],
                "skills": [],
                "client_version": payload.client_version,
                "ip_address": ip_address,
                "token_hash": hash_token(worker_token),
                "approved": not worker_client.require_admin_approval,
            },
        )
    return {
        "worker_id": worker_id,
        "worker_token": worker_token,
        "approved": not worker_client.require_admin_approval,
    }
