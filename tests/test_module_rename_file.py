"""Tests for the file rename module."""

# IMPORTS
from threading import Event

import pytest

from marathon.modules import load_catalog
from marathon.modules._job import JobStopped, ModuleJob
from marathon.modules.rename_file import MODULE, run, validate_settings


# FUNC
def _job(source, settings=None, stop=None):
    return ModuleJob(str(source), settings or {}, stop or Event())


def test_catalog_entry():
    spec = next(item for item in load_catalog() if item["id"] == "rename_file")

    assert spec == {key: MODULE[key] for key in (
        "id", "name", "category", "icon", "description", "has_input",
        "outputs", "settings", "sort_order",
    )}
    assert spec["has_input"] is True
    assert [item["id"] for item in spec["outputs"]] == ["success", "error"]
    assert [item["key"] for item in spec["settings"]] == ["prefix", "suffix", "numbered"]


def test_validate_settings():
    assert validate_settings({}) == {"prefix": "Präfix oder Suffix angeben"}
    assert validate_settings({"prefix": "P_"}) == {}
    assert validate_settings({"prefix": "a/"}) == {"prefix": "Ungültiges Zeichen: /"}
    assert validate_settings({"suffix": "x\x01"}) == {"suffix": "Steuerzeichen nicht erlaubt"}
    assert validate_settings({"prefix": 1}) == {"prefix": "Text erwartet"}
    assert validate_settings({"prefix": "P_", "numbered": "x"}) == {
        "numbered": "Ja oder Nein erwartet"
    }


def test_validate_settings_returns_all_errors_in_setting_order():
    assert validate_settings({"prefix": "a/", "suffix": "x\x01", "numbered": "x"}) == {
        "prefix": "Ungültiges Zeichen: /",
        "suffix": "Steuerzeichen nicht erlaubt",
        "numbered": "Ja oder Nein erwartet",
    }


def test_renames_with_prefix_and_suffix(tmp_path):
    source = tmp_path / "clip.mxf"
    source.write_text("video", encoding="utf-8")

    result = run(_job(source, {"prefix": "P_", "suffix": "_v2"}))
    target = tmp_path / "P_clip_v2.mxf"

    assert result.output == "success"
    assert result.asset_path == str(target)
    assert result.log == ("Umbenannt in P_clip_v2.mxf",)
    assert target.read_text(encoding="utf-8") == "video"
    assert not source.exists()


def test_conflict_without_numbering_preserves_both_files(tmp_path):
    source = tmp_path / "clip.mxf"
    target = tmp_path / "P_clip.mxf"
    source.write_text("source", encoding="utf-8")
    target.write_text("existing", encoding="utf-8")

    result = run(_job(source, {"prefix": "P_"}))

    assert result.output == "error"
    assert result.log[0].startswith("Datei existiert bereits:")
    assert source.read_text(encoding="utf-8") == "source"
    assert target.read_text(encoding="utf-8") == "existing"


def test_conflict_with_numbering_uses_first_free_name(tmp_path):
    source = tmp_path / "clip.mxf"
    (tmp_path / "P_clip.mxf").write_text("existing", encoding="utf-8")
    source.write_text("source", encoding="utf-8")

    result = run(_job(source, {"prefix": "P_", "numbered": True}))

    assert result.output == "success"
    assert result.asset_path == str(tmp_path / "P_clip_1.mxf")
    assert (tmp_path / "P_clip_1.mxf").read_text(encoding="utf-8") == "source"
    assert not source.exists()


def test_missing_source_returns_error(tmp_path):
    source = tmp_path / "missing.mxf"

    result = run(_job(source, {"prefix": "P_"}))

    assert result.output == "error"
    assert result.log == (f"Quelldatei nicht gefunden: {source}",)


def test_stopped_job_is_raised_before_rename(tmp_path):
    source = tmp_path / "clip.mxf"
    source.write_text("video", encoding="utf-8")
    stop = Event()
    stop.set()

    with pytest.raises(JobStopped):
        run(_job(source, {"prefix": "P_"}, stop))

    assert source.read_text(encoding="utf-8") == "video"
    assert not (tmp_path / "P_clip.mxf").exists()


def test_invalid_settings_raise_labeled_value_error(tmp_path):
    source = tmp_path / "clip.mxf"

    with pytest.raises(ValueError, match="Präfix: Präfix oder Suffix angeben"):
        run(_job(source))
