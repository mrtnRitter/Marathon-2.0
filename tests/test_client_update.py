import hashlib
import threading
import time

from marathon.client.local_state import VersionState, load_version_state, save_version_state, version_exe
from marathon.client.update import UpdateManager


def _offer(version, content=b"client executable"):
    return {"version": version, "url": f"/api/v1/client/{version}", "sha256": hashlib.sha256(content).hexdigest()}


def _wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_valid_offer_downloads_verifies_waits_idle_and_requests_restart(tmp_path):
    save_version_state(tmp_path, VersionState("1.0.0"))
    content = b"new client"
    idle = threading.Event()
    downloaded = threading.Event()
    release_download = threading.Event()
    restart = threading.Event()
    requests = []

    def download(url, path, stop):
        requests.append(url)
        path.write_bytes(content)
        downloaded.set()
        release_download.wait(1)

    manager = UpdateManager(tmp_path, "1.0.0", download, idle.is_set, restart.set, idle_poll_s=0.01)
    manager.offer(_offer("2.0.0", content))
    assert downloaded.wait(1)
    assert manager.blocks_jobs() is False
    assert load_version_state(tmp_path) == VersionState("1.0.0")
    release_download.set()
    assert _wait_for(manager.blocks_jobs)
    idle.set()
    assert restart.wait(1)
    manager.stop()
    assert version_exe(tmp_path, "2.0.0").read_bytes() == content
    assert load_version_state(tmp_path) == VersionState("2.0.0", "1.0.0", True)
    assert requests == ["/api/v1/client/2.0.0"]
    assert manager.blocks_jobs() is False


def test_bad_checksum_cleans_file_and_retries_only_after_cooldown(tmp_path):
    save_version_state(tmp_path, VersionState("1.0.0"))
    clock = [0.0]
    downloads = []
    restart = threading.Event()

    def download(url, path, stop):
        downloads.append(clock[0])
        path.write_bytes(b"wrong content")

    manager = UpdateManager(tmp_path, "1.0.0", download, lambda: True, restart.set,
                            monotonic=lambda: clock[0], retry_after_s=10, idle_poll_s=0.01)
    offer = _offer("2.0.0", b"expected")
    manager.offer(offer)
    assert _wait_for(lambda: not manager._thread.is_alive())
    assert not version_exe(tmp_path, "2.0.0").with_suffix(".exe.part").exists()
    assert not restart.is_set()
    manager.offer(offer)
    assert len(downloads) == 1
    clock[0] = 10
    manager.offer(offer)
    assert _wait_for(lambda: len(downloads) == 2)
    manager.stop()


def test_failed_and_current_versions_are_ignored(tmp_path):
    save_version_state(tmp_path, VersionState("1.0.0", failed=["2.0.0"]))
    calls = []
    manager = UpdateManager(tmp_path, "1.0.0", lambda *args: calls.append(args), lambda: True,
                            lambda: None)
    manager.offer(_offer("2.0.0"))
    manager.offer(_offer("1.0.0"))
    manager.offer({**_offer("3.0.0"), "url": "https://example.invalid/file"})
    assert calls == []
    assert manager.blocks_jobs() is False
    manager.stop()


def test_blocks_jobs_only_during_idle_wait_and_confirm_healthy(tmp_path):
    save_version_state(tmp_path, VersionState("1.0.0"))
    downloaded = threading.Event()
    idle = threading.Event()
    restart = threading.Event()

    def download(url, path, stop):
        path.write_bytes(b"ready")
        downloaded.set()

    manager = UpdateManager(tmp_path, "1.0.0", download, idle.is_set, restart.set, idle_poll_s=0.01)
    manager.offer(_offer("2.0.0", b"ready"))
    assert downloaded.wait(1)
    assert _wait_for(manager.blocks_jobs)
    idle.set()
    assert restart.wait(1)
    manager.stop()
    healthy_manager = UpdateManager(tmp_path, "2.0.0", download, idle.is_set, restart.set)
    healthy_manager.confirm_healthy()
    assert load_version_state(tmp_path) == VersionState("2.0.0", "1.0.0", False)


def test_stop_during_idle_wait_leaves_state_unchanged_and_removes_part(tmp_path):
    original = VersionState("1.0.0")
    save_version_state(tmp_path, original)
    downloaded = threading.Event()
    restart = threading.Event()

    def download(url, path, stop):
        path.write_bytes(b"ready")
        downloaded.set()

    manager = UpdateManager(tmp_path, "1.0.0", download, lambda: False, restart.set, idle_poll_s=0.01)
    manager.offer(_offer("2.0.0", b"ready"))
    assert downloaded.wait(1)
    assert _wait_for(manager.blocks_jobs)
    manager.stop(timeout_s=1)
    assert not restart.is_set()
    assert load_version_state(tmp_path) == original
    assert not version_exe(tmp_path, "2.0.0").with_suffix(".exe.part").exists()


def test_stop_cancels_download_and_removes_partial_file(tmp_path):
    original = VersionState("1.0.0")
    save_version_state(tmp_path, original)
    started = threading.Event()

    def download(url, path, stop):
        started.set()
        stop.wait(1)
        path.write_bytes(b"partial")

    manager = UpdateManager(tmp_path, "1.0.0", download, lambda: False, lambda: None)
    manager.offer(_offer("2.0.0"))
    assert started.wait(1)
    manager.stop(timeout_s=1)
    assert load_version_state(tmp_path) == original
    assert not version_exe(tmp_path, "2.0.0").with_suffix(".exe.part").exists()
