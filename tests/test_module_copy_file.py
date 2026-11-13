"""Tests for the file-copy module."""

# IMPORTS
import hashlib
import threading

import pytest

import marathon.modules._files as files
from marathon.modules._job import JobStopped, ModuleJob
from marathon.modules.copy_file import MODULE, run, validate_settings


# FUNC
def _job(source, settings, stop=None):
    return ModuleJob(str(source), settings, stop or threading.Event())


def test_module_catalog_entry():
    assert MODULE["has_input"] is True
    assert [output["id"] for output in MODULE["outputs"]] == ["success", "error"]
    assert [setting["key"] for setting in MODULE["settings"]] == ["target", "checksum", "numbered"]
    assert MODULE["settings"][1]["options"] == ["Keine", "MD5", "SHA-256"]


def test_validate_settings(tmp_path):
    assert validate_settings({"target": str(tmp_path)}) == {}
    assert validate_settings({}) == {"target": "Ordner fehlt"}
    assert validate_settings({"target": str(tmp_path), "checksum": "CRC"}) == {
        "checksum": "Ungültige Auswahl",
    }
    assert validate_settings({"target": str(tmp_path), "numbered": "ja"}) == {
        "numbered": "Ja oder Nein erwartet",
    }


def test_run_copies_without_checksum(tmp_path):
    source = tmp_path / "a.mxf"
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    data = b"file contents"
    source.write_bytes(data)

    result = run(_job(source, {"target": str(target_dir)}))
    target = target_dir / "a.mxf"

    assert result.output is None
    assert result.log == (f"Kopiert nach {target}",)
    assert len(result.spawned) == 1
    assert result.spawned[0].path == str(target)
    assert result.spawned[0].output == "success"
    assert result.spawned[0].log == (f"Kopie von {source}",)
    assert result.asset_path is None
    assert source.exists()
    assert target.read_bytes() == data


@pytest.mark.parametrize(("checksum", "algorithm"), [("MD5", "md5"), ("SHA-256", "sha256")])
def test_run_logs_checksum(tmp_path, checksum, algorithm):
    source = tmp_path / "a.mxf"
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    data = b"file contents"
    source.write_bytes(data)

    result = run(_job(source, {"target": str(target_dir), "checksum": checksum}))

    assert result.log == (
        f"Kopiert nach {target_dir / source.name}",
        f"{checksum} geprüft: {getattr(hashlib, algorithm)(data).hexdigest()}",
    )


def test_run_conflict_without_numbering(tmp_path):
    source = tmp_path / "a.mxf"
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = target_dir / source.name
    source.write_bytes(b"source")
    target.write_bytes(b"keep")

    result = run(_job(source, {"target": str(target_dir)}))

    assert result.output == "error"
    assert result.log == (f"Datei existiert bereits: {target}",)
    assert result.spawned == ()
    assert target.read_bytes() == b"keep"


def test_run_conflict_with_numbering(tmp_path):
    source = tmp_path / "a.mxf"
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    source.write_bytes(b"source")
    (target_dir / source.name).write_bytes(b"keep")

    result = run(_job(source, {"target": str(target_dir), "numbered": True}))

    assert result.output is None
    assert result.spawned[0].path.endswith("a_1.mxf")


def test_run_checksum_mismatch_returns_error_and_removes_target(tmp_path, monkeypatch):
    source = tmp_path / "a.mxf"
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    source.write_bytes(b"source")
    monkeypatch.setattr(files, "_file_digest", lambda path, algorithm, stop: "0")

    result = run(_job(source, {"target": str(target_dir), "checksum": "MD5"}))

    assert result.output == "error"
    assert result.log[0].startswith("Checksumme stimmt nicht")
    assert result.spawned == ()
    assert not (target_dir / source.name).exists()


def test_run_missing_source(tmp_path):
    source = tmp_path / "missing.mxf"
    target_dir = tmp_path / "target"
    target_dir.mkdir()

    result = run(_job(source, {"target": str(target_dir)}))

    assert result.output == "error"
    assert result.log == (f"Quelldatei nicht gefunden: {source}",)
    assert result.spawned == ()


def test_run_missing_target_directory(tmp_path):
    source = tmp_path / "a.mxf"
    target_dir = tmp_path / "missing"
    source.write_bytes(b"source")

    result = run(_job(source, {"target": str(target_dir)}))

    assert result.output == "error"
    assert result.log == (f"Zielordner nicht gefunden: {target_dir}",)
    assert result.spawned == ()


def test_run_respects_stop_before_copy(tmp_path):
    source = tmp_path / "a.mxf"
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    source.write_bytes(b"source")
    stop = threading.Event()
    stop.set()

    with pytest.raises(JobStopped):
        run(_job(source, {"target": str(target_dir)}, stop))

    assert not (target_dir / source.name).exists()


def test_run_invalid_target_raises_labeled_value_error(tmp_path):
    source = tmp_path / "a.mxf"
    source.write_bytes(b"source")

    with pytest.raises(ValueError, match="Zielordner: Ordner fehlt"):
        run(_job(source, {"target": ""}))
