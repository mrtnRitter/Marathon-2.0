import errno
import hashlib
import threading

import pytest

import marathon.modules._files as files
from marathon.modules._files import ChecksumMismatch, NameConflict, copy_file, free_name, move_file
from marathon.modules._job import JobStopped


def test_free_name_returns_available_name(tmp_path):
    assert free_name(tmp_path, "a.mxf", False) == tmp_path / "a.mxf"


def test_free_name_conflict_when_numbering_disabled(tmp_path):
    (tmp_path / "a.mxf").touch()

    with pytest.raises(NameConflict):
        free_name(tmp_path, "a.mxf", False)


def test_free_name_numbers_conflicts(tmp_path):
    (tmp_path / "a.mxf").touch()
    assert free_name(tmp_path, "a.mxf", True) == tmp_path / "a_1.mxf"

    (tmp_path / "a_1.mxf").touch()
    assert free_name(tmp_path, "a.mxf", True) == tmp_path / "a_2.mxf"


def test_free_name_numbers_extensionless_name(tmp_path):
    (tmp_path / "readme").touch()
    assert free_name(tmp_path, "readme", True) == tmp_path / "readme_1"


def test_copy_file_without_checksum(tmp_path):
    source = tmp_path / "source.bin"
    target = tmp_path / "target.bin"
    source.write_bytes(b"file contents")

    assert copy_file(source, target, threading.Event()) is None
    assert target.read_bytes() == source.read_bytes()


@pytest.mark.parametrize("algorithm", ["md5", "sha256"])
def test_copy_file_returns_source_digest(tmp_path, algorithm):
    data = b"file contents"
    source = tmp_path / "source.bin"
    target = tmp_path / "target.bin"
    source.write_bytes(data)

    assert copy_file(source, target, threading.Event(), algorithm) == getattr(hashlib, algorithm)(data).hexdigest()
    assert target.read_bytes() == data


def test_copy_file_does_not_overwrite_target(tmp_path):
    source = tmp_path / "source.bin"
    target = tmp_path / "target.bin"
    source.write_bytes(b"source")
    target.write_bytes(b"keep")

    with pytest.raises(NameConflict):
        copy_file(source, target, threading.Event())

    assert target.read_bytes() == b"keep"


def test_copy_file_checksum_mismatch_removes_target(tmp_path, monkeypatch):
    source = tmp_path / "source.bin"
    target = tmp_path / "target.bin"
    source.write_bytes(b"source")
    monkeypatch.setattr(files, "_file_digest", lambda path, algorithm, stop: "0")

    with pytest.raises(ChecksumMismatch):
        copy_file(source, target, threading.Event(), "md5")

    assert not target.exists()


def test_copy_file_stops_before_creating_target(tmp_path):
    source = tmp_path / "source.bin"
    target = tmp_path / "target.bin"
    source.write_bytes(b"source")
    stop = threading.Event()
    stop.set()

    with pytest.raises(JobStopped):
        copy_file(source, target, stop)

    assert not target.exists()


def test_copy_file_stops_mid_copy_and_removes_target(tmp_path, monkeypatch):
    source = tmp_path / "source.bin"
    target = tmp_path / "target.bin"
    data = bytes(range(64))
    source.write_bytes(data)
    monkeypatch.setattr(files, "chunk_size", 4)

    class StopOnThirdCheck(threading.Event):
        def __init__(self):
            super().__init__()
            self.checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks >= 3

    with pytest.raises(JobStopped):
        copy_file(source, target, StopOnThirdCheck())

    assert not target.exists()
    assert source.read_bytes() == data


def test_move_file_renames_on_same_filesystem(tmp_path):
    source = tmp_path / "source.bin"
    target = tmp_path / "target.bin"
    source.write_bytes(b"contents")

    assert move_file(source, target, threading.Event()) is True
    assert not source.exists()
    assert target.read_bytes() == b"contents"


def test_move_file_rejects_existing_target(tmp_path):
    source = tmp_path / "source.bin"
    target = tmp_path / "target.bin"
    source.write_bytes(b"source")
    target.write_bytes(b"target")

    with pytest.raises(NameConflict):
        move_file(source, target, threading.Event())

    assert source.read_bytes() == b"source"
    assert target.read_bytes() == b"target"


def test_move_file_copies_on_cross_device_error(tmp_path, monkeypatch):
    source = tmp_path / "source.bin"
    target = tmp_path / "target.bin"
    source.write_bytes(b"contents")
    monkeypatch.setattr(files.os, "rename", lambda *args: (_ for _ in ()).throw(OSError(errno.EXDEV, "cross")))

    assert move_file(source, target, threading.Event()) is False
    assert not source.exists()
    assert target.read_bytes() == b"contents"


def test_move_file_propagates_other_os_error(tmp_path, monkeypatch):
    source = tmp_path / "source.bin"
    target = tmp_path / "target.bin"
    source.write_bytes(b"contents")
    monkeypatch.setattr(files.os, "rename", lambda *args: (_ for _ in ()).throw(OSError(errno.EACCES, "denied")))

    with pytest.raises(OSError) as error:
        move_file(source, target, threading.Event())

    assert error.value.errno == errno.EACCES
    assert source.read_bytes() == b"contents"
    assert not target.exists()


def test_move_file_cross_device_stop_keeps_source_and_removes_partial_target(tmp_path, monkeypatch):
    source = tmp_path / "source.bin"
    target = tmp_path / "target.bin"
    data = bytes(range(64))
    source.write_bytes(data)
    monkeypatch.setattr(files.os, "rename", lambda *args: (_ for _ in ()).throw(OSError(errno.EXDEV, "cross")))
    monkeypatch.setattr(files, "chunk_size", 4)

    class StopOnThirdCheck(threading.Event):
        def __init__(self):
            super().__init__()
            self.checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks >= 3

    with pytest.raises(JobStopped):
        move_file(source, target, StopOnThirdCheck())

    assert source.read_bytes() == data
    assert not target.exists()
