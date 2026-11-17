"""Token storage and authentication for worker API requests."""

# IMPORTS
import os
import secrets
from pathlib import Path
from threading import Lock

from fastapi import HTTPException, Request

from marathon.server.config import data_dir


# CONFIG
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


def require_worker_token(request: Request) -> None:
    """Require the bearer token configured for this app."""
    expected = request.app.state.worker_token
    if expected is None:
        with _app_token_lock:
            expected = request.app.state.worker_token
            if expected is None:
                expected = load_or_create_token()
                request.app.state.worker_token = expected

    authorization = request.headers.get("Authorization", "")
    scheme, separator, supplied = authorization.partition(" ")
    valid = (
        separator == " "
        and scheme.casefold() == "bearer"
        and secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))
    )
    if not valid:
        raise HTTPException(status_code=401, detail="Worker-Token ungültig")
