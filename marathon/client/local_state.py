# IMPORTS
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


# CONFIG
client_dir_name = "MarathonWorker"
exe_name = "MarathonWorker.exe"
credentials_file = "worker.json"
state_file = "state.json"
bootstrap_file = "client_bootstrap.json"
_worker_id_pattern = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_version_pattern = re.compile(r"^\d+\.\d+\.\d+$")
_missing = object()


# FUNC
class LocalStateError(ValueError):
    """Raised when local client state is missing or invalid."""


@dataclass(frozen=True)
class Bootstrap:
    """Client bootstrap data provisioned during installation."""

    server_url: str
    root_ca_pem: str
    client_version: str


@dataclass(frozen=True)
class WorkerCredentials:
    """Credentials used to authenticate this worker with its server."""

    server_url: str
    worker_id: str
    worker_token: str


@dataclass
class VersionState:
    """Installed and pending client-version information."""

    current: str
    previous: str | None = None
    pending: bool = False
    failed: list[str] = field(default_factory=list)


def client_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Return the platform-appropriate client directory without creating it."""
    values = os.environ if environ is None else environ
    local_app_data = values.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / client_dir_name
    if os.name == "nt":
        return Path.home() / "AppData" / "Local" / client_dir_name
    data_home = values.get("XDG_DATA_HOME")
    return (Path(data_home) if data_home else Path.home() / ".local" / "share") / client_dir_name


def _normalize_server_url(value: object) -> str:
    if not isinstance(value, str) or not value.casefold().startswith("https://"):
        raise ValueError("Server-Adresse muss mit https:// beginnen")
    return value.removesuffix("/")


def _validate_version(value: object) -> str:
    if not isinstance(value, str) or not _version_pattern.fullmatch(value):
        raise ValueError(f"Ungültige Version: {value}")
    return value


def _read_json(path: Path, *, missing_message: str | None = None) -> object:
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        if missing_message is not None:
            raise LocalStateError(missing_message) from error
        return _missing
    except (OSError, UnicodeError) as error:
        raise LocalStateError(f"{path.name} ist beschädigt: {error}") from error
    try:
        return json.loads(content)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise LocalStateError(f"{path.name} ist beschädigt: {error}") from error


def _require_object(data: object, required: set[str], optional: set[str] | None = None) -> dict[str, object]:
    if not isinstance(data, dict):
        raise ValueError("JSON muss ein Objekt enthalten")
    allowed = required | (optional or set())
    missing = required - data.keys()
    unexpected = data.keys() - allowed
    if missing:
        raise ValueError(f"Felder fehlen: {', '.join(sorted(missing))}")
    if unexpected:
        raise ValueError(f"Unbekannte Felder: {', '.join(sorted(unexpected))}")
    return data


def _load_error(path: Path, error: ValueError) -> LocalStateError:
    return LocalStateError(f"{path.name} ist beschädigt: {error}")


def read_bootstrap(path: Path) -> Bootstrap:
    """Read and validate the bootstrap JSON file at ``path``."""
    data = _read_json(path, missing_message=f"Client-Paket unvollständig: {path.name} fehlt")
    try:
        values = _require_object(data, {"server_url", "root_ca_pem", "client_version"})
        server_url = _normalize_server_url(values["server_url"])
        root_ca_pem = values["root_ca_pem"]
        if not isinstance(root_ca_pem, str):
            raise ValueError("root_ca_pem muss Text sein")
        client_version = _validate_version(values["client_version"])
        return Bootstrap(server_url, root_ca_pem, client_version)
    except ValueError as error:
        raise _load_error(path, error) from error


def load_credentials(base: Path) -> WorkerCredentials | None:
    """Load and validate saved worker credentials, if present."""
    path = base / credentials_file
    data = _read_json(path)
    if data is _missing:
        return None
    try:
        values = _require_object(data, {"server_url", "worker_id", "worker_token"})
        server_url = _normalize_server_url(values["server_url"])
        worker_id = values["worker_id"]
        if not isinstance(worker_id, str) or not _worker_id_pattern.fullmatch(worker_id):
            raise ValueError("Ungültige Worker-ID")
        worker_token = values["worker_token"]
        if not isinstance(worker_token, str) or not worker_token:
            raise ValueError("Worker-Token fehlt")
        return WorkerCredentials(server_url, worker_id, worker_token)
    except ValueError as error:
        raise _load_error(path, error) from error


def _write_json(base: Path, filename: str, data: dict[str, object], *, private: bool = False) -> None:
    base.mkdir(parents=True, exist_ok=True)
    path = base / filename
    temporary_path = base / f"{filename}.tmp"
    try:
        if private and os.name == "posix":
            descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                json.dump(data, output, ensure_ascii=False, indent=2)
                output.write("\n")
        else:
            with temporary_path.open("w", encoding="utf-8", newline="\n") as output:
                json.dump(data, output, ensure_ascii=False, indent=2)
                output.write("\n")
        os.replace(temporary_path, path)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def save_credentials(base: Path, credentials: WorkerCredentials) -> None:
    """Validate and atomically save worker credentials."""
    try:
        server_url = _normalize_server_url(credentials.server_url)
        if not isinstance(credentials.worker_id, str) or not _worker_id_pattern.fullmatch(credentials.worker_id):
            raise ValueError("Ungültige Worker-ID")
        if not isinstance(credentials.worker_token, str) or not credentials.worker_token:
            raise ValueError("Worker-Token fehlt")
    except ValueError as error:
        raise LocalStateError(str(error)) from error
    _write_json(
        base,
        credentials_file,
        {"server_url": server_url, "worker_id": credentials.worker_id, "worker_token": credentials.worker_token},
        private=True,
    )


def delete_credentials(base: Path) -> None:
    """Delete saved worker credentials, if present."""
    try:
        (base / credentials_file).unlink()
    except FileNotFoundError:
        pass


def load_version_state(base: Path) -> VersionState | None:
    """Load and validate installed client-version state, if present."""
    path = base / state_file
    data = _read_json(path)
    if data is _missing:
        return None
    try:
        values = _require_object(data, {"current"}, {"previous", "pending", "failed"})
        current = _validate_version(values["current"])
        previous_value = values.get("previous")
        previous = None if previous_value is None else _validate_version(previous_value)
        pending = values.get("pending", False)
        if type(pending) is not bool:
            raise ValueError("pending muss boolesch sein")
        failed_value = values.get("failed", [])
        if not isinstance(failed_value, list):
            raise ValueError("failed muss eine Liste sein")
        failed = [_validate_version(version) for version in failed_value]
        return VersionState(current, previous, pending, failed)
    except ValueError as error:
        raise _load_error(path, error) from error


def save_version_state(base: Path, state: VersionState) -> None:
    """Validate and atomically save installed client-version state."""
    try:
        current = _validate_version(state.current)
        previous = None if state.previous is None else _validate_version(state.previous)
        if type(state.pending) is not bool:
            raise ValueError("pending muss boolesch sein")
        if not isinstance(state.failed, list):
            raise ValueError("failed muss eine Liste sein")
        failed = [_validate_version(version) for version in state.failed]
    except ValueError as error:
        raise LocalStateError(str(error)) from error
    _write_json(
        base,
        state_file,
        {"current": current, "previous": previous, "pending": state.pending, "failed": failed},
    )


def version_exe(base: Path, version: str) -> Path:
    """Return the executable path for a validated semantic version."""
    _validate_version(version)
    return base / "versions" / version / exe_name
