"""Token storage and authentication for worker API requests."""

# IMPORTS
import hashlib
import os
import secrets
from pathlib import Path
from threading import Lock

from fastapi import Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.engine import Engine

from marathon.server.api.deps import get_engine
from marathon.server.config import data_dir


# CONFIG
server_worker_id = "server-worker"
_token_file_lock = Lock()
_app_token_lock = Lock()


# FUNC
def load_or_create_token(path: Path = data_dir / "worker.token") -> str:
    """Read a stored worker token or securely create one if absent."""
    with _token_file_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8") as token_file:
                token_file.write(secrets.token_urlsafe(32))
        if os.name == "posix":
            os.chmod(path, 0o600)
        return path.read_text(encoding="utf-8").strip()



def hash_token(token: str) -> str:
    """Return the SHA-256 digest used to store worker credentials."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def require_worker(request: Request, engine: Engine = Depends(get_engine)) -> str:
    """Authenticate a server or enrolled worker token and return its ID."""
    expected = request.app.state.worker_token
    if expected is None:
        with _app_token_lock:
            expected = request.app.state.worker_token
            if expected is None:
                expected = load_or_create_token()
                request.app.state.worker_token = expected

    authorization = request.headers.get("Authorization", "")
    scheme, separator, supplied = authorization.partition(" ")
    if (
        separator == " "
        and scheme.casefold() == "bearer"
        and secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))
    ):
        return server_worker_id

    token_hash = hash_token(supplied) if separator == " " and scheme.casefold() == "bearer" else ""
    with engine.connect() as connection:
        worker_id = connection.execute(
            text("SELECT id FROM workers WHERE token_hash = :token_hash"),
            {"token_hash": token_hash},
        ).scalar_one_or_none()
    if worker_id is None:
        raise HTTPException(status_code=401, detail="Worker-Token ungültig")
    return worker_id


def check_worker_id(authenticated_id: str, claimed_id: str) -> None:
    """Reject requests that claim a worker ID other than the token owner."""
    if authenticated_id != claimed_id:
        raise HTTPException(status_code=403, detail="Worker-Token gehört zu einem anderen Worker")
