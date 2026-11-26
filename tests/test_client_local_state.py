import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from marathon.client import local_state
from marathon.client.local_state import (
    Bootstrap,
    LocalStateError,
    VersionState,
    WorkerCredentials,
    client_dir,
    delete_credentials,
    load_credentials,
    load_version_state,
    read_bootstrap,
    save_credentials,
    save_version_state,
    version_exe,
)


def test_client_dir_uses_localappdata_when_present(tmp_path: Path) -> None:
    result = client_dir({"LOCALAPPDATA": str(tmp_path / "local"), "XDG_DATA_HOME": "/xdg"})
    assert result == tmp_path / "local" / "MarathonWorker"
    assert not result.exists()


def test_client_dir_windows_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_state, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert client_dir({}) == tmp_path / "AppData" / "Local" / "MarathonWorker"


def test_client_dir_xdg_and_default_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_state.os, "name", "posix")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert client_dir({"XDG_DATA_HOME": str(tmp_path / "data")}) == tmp_path / "data" / "MarathonWorker"
    assert client_dir({}) == tmp_path / ".local" / "share" / "MarathonWorker"


def test_credentials_round_trip_and_delete(tmp_path: Path) -> None:
    credentials = WorkerCredentials("https://server.example", "worker_1", "secret")
    save_credentials(tmp_path, credentials)
    assert load_credentials(tmp_path) == credentials
    assert (tmp_path / "worker.json").exists()
    if os.name == "posix":
        assert stat.S_IMODE((tmp_path / "worker.json").stat().st_mode) == 0o600
    delete_credentials(tmp_path)
    delete_credentials(tmp_path)
    assert load_credentials(tmp_path) is None


def test_version_state_round_trip(tmp_path: Path) -> None:
    state = VersionState("2.0.0", "1.9.0", True, ["1.8.0"])
    save_version_state(tmp_path, state)
    assert load_version_state(tmp_path) == state


def test_missing_state_files_return_none(tmp_path: Path) -> None:
    assert load_credentials(tmp_path) is None
    assert load_version_state(tmp_path) is None


def test_corrupt_json_raises_local_state_error(tmp_path: Path) -> None:
    (tmp_path / "worker.json").write_text("{", encoding="utf-8")
    with pytest.raises(LocalStateError, match="worker.json ist beschädigt"):
        load_credentials(tmp_path)
    (tmp_path / "state.json").write_text("{", encoding="utf-8")
    with pytest.raises(LocalStateError, match="state.json ist beschädigt"):
        load_version_state(tmp_path)


@pytest.mark.parametrize(
    ("credentials", "message"),
    [
        (WorkerCredentials("http://server.example", "worker", "token"), "https://"),
        (WorkerCredentials("https://server.example", "worker/name", "token"), "Worker-ID"),
        (WorkerCredentials("https://server.example", "worker", ""), "Worker-Token"),
    ],
)
def test_invalid_credentials_raise_local_state_error(
    tmp_path: Path, credentials: WorkerCredentials, message: str
) -> None:
    with pytest.raises(LocalStateError, match=message):
        save_credentials(tmp_path, credentials)


def test_invalid_loaded_credentials_raise_local_state_error(tmp_path: Path) -> None:
    (tmp_path / "worker.json").write_text(
        json.dumps({"server_url": "https://server.example/", "worker_id": "bad/id", "worker_token": "token"}),
        encoding="utf-8",
    )
    with pytest.raises(LocalStateError, match="Ungültige Worker-ID"):
        load_credentials(tmp_path)


def test_invalid_version_state_raises_local_state_error(tmp_path: Path) -> None:
    with pytest.raises(LocalStateError, match=r"Ungültige Version: \.\./1"):
        save_version_state(tmp_path, VersionState("../1"))
    (tmp_path / "state.json").write_text(json.dumps({"current": "../1"}), encoding="utf-8")
    with pytest.raises(LocalStateError, match=r"Ungültige Version: \.\./1"):
        load_version_state(tmp_path)


def test_version_exe_validates_version(tmp_path: Path) -> None:
    assert version_exe(tmp_path, "2.0.0") == tmp_path / "versions" / "2.0.0" / "MarathonWorker.exe"
    with pytest.raises(ValueError, match=r"Ungültige Version: \.\."):
        version_exe(tmp_path, "..")


def test_atomic_writes_leave_no_temporary_files(tmp_path: Path) -> None:
    save_credentials(tmp_path, WorkerCredentials("https://server.example/", "worker", "token"))
    save_version_state(tmp_path, VersionState("1.2.3"))
    assert not list(tmp_path.glob("*.tmp"))
    assert load_credentials(tmp_path).server_url == "https://server.example"


def test_bootstrap_read_and_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "client_bootstrap.json"
    path.write_text(
        json.dumps({"server_url": "https://server.example/", "root_ca_pem": "", "client_version": "1.2.3"}),
        encoding="utf-8",
    )
    assert read_bootstrap(path) == Bootstrap("https://server.example", "", "1.2.3")
    path.unlink()
    with pytest.raises(LocalStateError, match="Client-Paket unvollständig: client_bootstrap.json fehlt"):
        read_bootstrap(path)


def test_bootstrap_invalid_fields_raise_local_state_error(tmp_path: Path) -> None:
    path = tmp_path / "client_bootstrap.json"
    path.write_text(
        json.dumps({"server_url": "http://server.example", "root_ca_pem": "", "client_version": "1.2.3"}),
        encoding="utf-8",
    )
    with pytest.raises(LocalStateError, match="Server-Adresse muss mit https:// beginnen"):
        read_bootstrap(path)
    path.write_text(
        json.dumps({"server_url": "https://server.example", "root_ca_pem": "", "client_version": "../1"}),
        encoding="utf-8",
    )
    with pytest.raises(LocalStateError, match=r"Ungültige Version: \.\./1"):
        read_bootstrap(path)
