import hashlib
import threading
from pathlib import Path

import pytest

from marathon.client.tools import ToolError, ToolStore


def make_manifest(name: str, version: str, files: dict[str, bytes]) -> dict:
    return {
        "name": name,
        "version": version,
        "files": [
            {"path": path, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            for path, content in files.items()
        ],
    }


def make_store(base: Path, manifest: list[dict], contents: dict[tuple[str, str], bytes], downloaded=None):
    def download(name: str, relative_path: str, target: Path, stop: threading.Event) -> None:
        if downloaded is not None:
            downloaded.append((name, relative_path))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents[(name, relative_path)])

    return ToolStore(base, lambda: manifest, download)


def test_first_sync_writes_verified_files_and_tool_path(tmp_path: Path) -> None:
    files = {"bin/ffmpeg": b"ffmpeg bytes", "README.txt": b"tool docs"}
    manifest = [make_manifest("ffmpeg", "0123456789abcdef", files)]
    store = make_store(tmp_path, manifest, {("ffmpeg", path): data for path, data in files.items()})

    result = store.sync(threading.Event())

    version_dir = tmp_path / "tools" / "ffmpeg" / "0123456789abcdef"
    assert result.updated == ["ffmpeg"]
    assert result.failed == {}
    assert (version_dir / "bin/ffmpeg").read_bytes() == b"ffmpeg bytes"
    assert (version_dir / "README.txt").read_bytes() == b"tool docs"
    assert (tmp_path / "tools" / "ffmpeg" / "current.txt").read_text() == "0123456789abcdef"
    assert store.tool_path("ffmpeg") == version_dir


def test_second_sync_skips_matching_tool(tmp_path: Path) -> None:
    files = {"bin/ffmpeg": b"ffmpeg bytes"}
    manifest = [make_manifest("ffmpeg", "0123456789abcdef", files)]
    downloaded = []
    store = make_store(tmp_path, manifest, {("ffmpeg", path): data for path, data in files.items()}, downloaded)

    store.sync(threading.Event())
    downloaded.clear()
    result = store.sync(threading.Event())

    assert result.updated == []
    assert result.failed == {}
    assert downloaded == []


def test_same_size_corruption_is_reverified_and_repaired(tmp_path: Path) -> None:
    files = {"bin/ffmpeg": b"verified"}
    manifest = [make_manifest("ffmpeg", "0123456789abcdef", files)]
    downloaded = []
    store = make_store(tmp_path, manifest, {("ffmpeg", path): data for path, data in files.items()}, downloaded)
    store.sync(threading.Event())
    target = tmp_path / "tools" / "ffmpeg" / "0123456789abcdef" / "bin/ffmpeg"
    target.write_bytes(b"tampered")
    downloaded.clear()

    result = store.sync(threading.Event())

    assert result.updated == ["ffmpeg"]
    assert result.failed == {}
    assert downloaded == [("ffmpeg", "bin/ffmpeg")]
    assert target.read_bytes() == b"verified"


def test_new_version_activates_and_removes_old_version(tmp_path: Path) -> None:
    store = make_store(tmp_path, [], {})
    old_files = {"ffmpeg": b"old"}
    store.fetch_manifest = lambda: [make_manifest("ffmpeg", "0123456789abcdef", old_files)]
    store.download = lambda name, path, target, stop: (target.parent.mkdir(parents=True, exist_ok=True), target.write_bytes(b"old"))
    store.sync(threading.Event())

    new_files = {"ffmpeg": b"new"}
    store.fetch_manifest = lambda: [make_manifest("ffmpeg", "fedcba9876543210", new_files)]
    store.download = lambda name, path, target, stop: (target.parent.mkdir(parents=True, exist_ok=True), target.write_bytes(b"new"))
    result = store.sync(threading.Event())

    tool_dir = tmp_path / "tools" / "ffmpeg"
    assert result.updated == ["ffmpeg"]
    assert (tool_dir / "current.txt").read_text() == "fedcba9876543210"
    assert (tool_dir / "fedcba9876543210" / "ffmpeg").read_bytes() == b"new"
    assert not (tool_dir / "0123456789abcdef").exists()


def test_bad_checksum_keeps_old_version_and_removes_part(tmp_path: Path) -> None:
    old_files = {"ffmpeg": b"old"}
    first_manifest = [make_manifest("ffmpeg", "0123456789abcdef", old_files)]
    store = make_store(tmp_path, first_manifest, {("ffmpeg", "ffmpeg"): b"old"})
    store.sync(threading.Event())
    store.fetch_manifest = lambda: [make_manifest("ffmpeg", "fedcba9876543210", {"ffmpeg": b"expected"})]
    store.download = lambda name, path, target, stop: (target.parent.mkdir(parents=True, exist_ok=True), target.write_bytes(b"wrong"))

    result = store.sync(threading.Event())

    tool_dir = tmp_path / "tools" / "ffmpeg"
    assert result.failed == {"ffmpeg": "Zusatzprogramm ffmpeg: Prüfsumme stimmt nicht (ffmpeg)"}
    assert (tool_dir / "current.txt").read_text() == "0123456789abcdef"
    assert (tool_dir / "0123456789abcdef" / "ffmpeg").read_bytes() == b"old"
    assert not list(tool_dir.rglob("*.part"))


@pytest.mark.parametrize("path", ["../x", "a\\b", "/abs"])
def test_unsafe_paths_fail_per_tool(tmp_path: Path, path: str) -> None:
    manifest = [{"name": "ffmpeg", "version": "0123456789abcdef", "files": [{"path": path, "size": 1, "sha256": "0" * 64}]}]
    store = make_store(tmp_path, manifest, {})

    result = store.sync(threading.Event())

    assert "ffmpeg" in result.failed
    assert not (tmp_path / "tools" / "ffmpeg" / "current.txt").exists()


def test_removed_tool_is_deleted(tmp_path: Path) -> None:
    files = {"ffmpeg": b"old"}
    store = make_store(tmp_path, [make_manifest("ffmpeg", "0123456789abcdef", files)], {("ffmpeg", "ffmpeg"): b"old"})
    store.sync(threading.Event())
    store.fetch_manifest = lambda: []

    result = store.sync(threading.Event())

    assert result.failed == {}
    assert not (tmp_path / "tools" / "ffmpeg").exists()


def test_manifest_failure_is_reported(tmp_path: Path) -> None:
    def fail():
        raise OSError("offline")

    store = ToolStore(tmp_path, fail, lambda *_: None)

    result = store.sync(threading.Event())

    assert result == store_result([], {"*": "Liste der Zusatzprogramme nicht abrufbar: offline"})


def store_result(updated: list[str], failed: dict[str, str]):
    from marathon.client.tools import SyncResult

    return SyncResult(updated, failed)


def test_stop_before_file_does_not_switch_current(tmp_path: Path) -> None:
    files = {"ffmpeg": b"new"}
    old_tool = tmp_path / "tools" / "ffmpeg"
    old_tool.mkdir(parents=True)
    (old_tool / "current.txt").write_text("0123456789abcdef")
    manifest = [make_manifest("ffmpeg", "fedcba9876543210", files)]
    store = make_store(tmp_path, manifest, {("ffmpeg", "ffmpeg"): b"new"})
    stop = threading.Event()
    stop.set()

    result = store.sync(stop)

    assert result == store_result([], {})
    assert (old_tool / "current.txt").read_text() == "0123456789abcdef"
    assert not (old_tool / "fedcba9876543210").exists()


def test_download_exception_does_not_block_other_tools(tmp_path: Path) -> None:
    manifests = [
        make_manifest("broken", "0123456789abcdef", {"x": b"x"}),
        make_manifest("ffmpeg", "fedcba9876543210", {"ffmpeg": b"ok"}),
    ]

    def download(name: str, relative_path: str, target: Path, stop: threading.Event) -> None:
        if name == "broken":
            raise OSError("download failed")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"ok")

    result = ToolStore(tmp_path, lambda: manifests, download).sync(threading.Event())

    assert result.updated == ["ffmpeg"]
    assert result.failed == {"broken": "download failed"}
    assert not list((tmp_path / "tools" / "broken").rglob("*.part"))


def test_missing_tool_path_raises_tool_error(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="Zusatzprogramm fehlt: ffmpeg"):
        ToolStore(tmp_path, lambda: [], lambda *_: None).tool_path("ffmpeg")
