"""Tests for the move-file module."""

# IMPORTS
import errno
import threading
from pathlib import Path

import pytest

from marathon.modules import load_catalog
from marathon.modules._job import JobStopped, ModuleJob
from marathon.modules.move_file import MODULE, run, validate_settings


# FUNC
def _job(asset_path: Path, target: Path, stop: threading.Event | None = None) -> ModuleJob:
    return ModuleJob(str(asset_path), {"target": str(target)}, stop or threading.Event())


def test_catalog_contains_move_file_module():
    spec = next(spec for spec in load_catalog() if spec["id"] == "move_file")

    assert spec == {key: MODULE[key] for key in spec}
    assert spec["has_input"] is True
    assert [output["id"] for output in spec["outputs"]] == ["success", "error"]
    assert [setting["key"] for setting in spec["settings"]] == ["target"]


def test_validate_settings_requires_absolute_target(tmp_path):
    assert validate_settings({"target": str(tmp_path)}) == {}
    assert validate_settings({}) == {"target": "Ordner fehlt"}


def test_run_moves_file_on_same_filesystem(tmp_path):
    source = tmp_path / "source.txt"
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    source.write_text("content", encoding="utf-8")
    target = target_dir / source.name

    result = run(_job(source, target_dir))

    assert result.output == "success"
    assert result.asset_path == str(target)
    assert result.log == (f"Verschoben nach {target}",)
    assert not source.exists()
    assert target.read_text(encoding="utf-8") == "content"


def test_run_copies_and_deletes_source_on_cross_device(tmp_path, monkeypatch):
    from marathon.modules import _files

    source = tmp_path / "source.txt"
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    source.write_text("content", encoding="utf-8")
    target = target_dir / source.name

    def raise_cross_device(*args, **kwargs):
        raise OSError(errno.EXDEV, "cross")

    monkeypatch.setattr(_files.os, "rename", raise_cross_device)
    result = run(_job(source, target_dir))

    assert result.output == "success"
    assert result.asset_path == str(target)
    assert result.log == (
        f"Verschoben nach {target}",
        "Quelle und Ziel liegen nicht auf demselben Laufwerk: Datei wurde kopiert und die Quelle gelöscht (ohne Checksumme)",
    )
    assert not source.exists()
    assert target.read_text(encoding="utf-8") == "content"


def test_run_reports_name_conflict_without_changing_files(tmp_path):
    source = tmp_path / "source.txt"
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    source.write_text("source", encoding="utf-8")
    target = target_dir / source.name
    target.write_text("target", encoding="utf-8")

    result = run(_job(source, target_dir))

    assert result.output == "error"
    assert result.log == (f"Datei existiert bereits: {target}",)
    assert result.asset_path is None
    assert source.read_text(encoding="utf-8") == "source"
    assert target.read_text(encoding="utf-8") == "target"


def test_run_reports_os_error_and_keeps_source(tmp_path, monkeypatch):
    from marathon.modules import _files

    source = tmp_path / "source.txt"
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    source.write_text("content", encoding="utf-8")

    def raise_access_denied(*args, **kwargs):
        raise OSError(errno.EACCES, "denied")

    monkeypatch.setattr(_files.os, "rename", raise_access_denied)
    result = run(_job(source, target_dir))

    assert result.output == "error"
    assert result.log[0].startswith("Verschieben fehlgeschlagen:")
    assert result.asset_path is None
    assert source.read_text(encoding="utf-8") == "content"


def test_run_reports_missing_source(tmp_path):
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    source = tmp_path / "missing.txt"

    result = run(_job(source, target_dir))

    assert result.output == "error"
    assert result.log == (f"Quelldatei nicht gefunden: {source}",)
    assert result.asset_path is None


def test_run_reports_missing_target_directory(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("content", encoding="utf-8")
    target_dir = tmp_path / "missing"

    result = run(_job(source, target_dir))

    assert result.output == "error"
    assert result.log == (f"Zielordner nicht gefunden: {target_dir}",)
    assert result.asset_path is None
    assert source.read_text(encoding="utf-8") == "content"


def test_run_raises_job_stopped_before_moving_source(tmp_path):
    source = tmp_path / "source.txt"
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    source.write_text("content", encoding="utf-8")
    stop = threading.Event()
    stop.set()

    with pytest.raises(JobStopped):
        run(_job(source, target_dir, stop))

    assert source.read_text(encoding="utf-8") == "content"
    assert not (target_dir / source.name).exists()


def test_run_raises_value_error_for_invalid_target(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("content", encoding="utf-8")

    with pytest.raises(ValueError, match="Zielordner: Ordner fehlt"):
        run(ModuleJob(str(source), {"target": ""}, threading.Event()))

    assert source.read_text(encoding="utf-8") == "content"


def test_run_strips_whitespace_from_target_setting(tmp_path):
    source = tmp_path / "source.txt"
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    source.write_text("content", encoding="utf-8")
    target = target_dir / source.name
    job = ModuleJob(str(source), {"target": f"  {target_dir}  "}, threading.Event())

    result = run(job)

    assert result.output == "success"
    assert result.asset_path == str(target)
    assert not source.exists()
    assert target.read_text(encoding="utf-8") == "content"
