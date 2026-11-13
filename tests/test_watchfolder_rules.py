"""Tests for watchfolder parsing, path, and settings rules."""

# IMPORTS
import pytest

from marathon.modules.watchfolder.rules import (
    WatchSettings,
    matches_extensions,
    parse_extensions,
    read_settings,
    validate_path,
    validate_settings,
)


# TESTS

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),
        (".MXF, mov ,, .mxf", [".mxf", ".mov"]),
        (".tar.gz", [".tar.gz"]),
    ],
)
def test_parse_extensions(raw, expected):
    assert parse_extensions(raw) == expected


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (".m x f", "Ungültige Endung: .m x f"),
        (".", "Ungültige Endung: ."),
        ("a/b", "Ungültige Endung: a/b"),
    ],
)
def test_parse_extensions_rejects_invalid_values(raw, message):
    with pytest.raises(ValueError) as error:
        parse_extensions(raw)
    assert str(error.value) == message


@pytest.mark.parametrize(
    "path",
    [r"D:\Ingest", "Z:/", r"\\server\share", r"\\server\share\sub", "/mnt/ingest", "  D:\\x  "],
)
def test_validate_path_accepts_absolute_paths(path):
    assert validate_path(path) is None


@pytest.mark.parametrize("path", ["", "   "])
def test_validate_path_requires_a_folder(path):
    assert validate_path(path) == "Ordner fehlt"


@pytest.mark.parametrize("path", [r"\\server", "\\\\server\\"])
def test_validate_path_rejects_incomplete_network_paths(path):
    assert validate_path(path) == "Netzpfad braucht Server und Freigabe: \\\\server\\freigabe"


@pytest.mark.parametrize("path", ["Ingest", r"D:Ingest", r"\Ingest"])
def test_validate_path_rejects_relative_paths(path):
    assert validate_path(path) == "Pfad muss absolut sein, z. B. D:\\Ingest oder \\\\server\\freigabe"


def test_validate_settings_rejects_non_text_extension_values():
    assert validate_settings({"path": r"D:\In", "include": 5}) == {"include": "Text erwartet"}


@pytest.mark.parametrize(
    ("key", "label"),
    [("include", "Include-Endungen"), ("exclude", "Exclude-Endungen")],
)
def test_read_settings_rejects_non_text_extension_values(key, label):
    with pytest.raises(ValueError, match=f"^{label}: Text erwartet$"):
        read_settings({"path": r"D:\In", key: 5})


def test_validate_settings_reports_extension_overlap():
    assert validate_settings({"path": r"D:\In", "include": ".mxf, .mov", "exclude": ".MOV"}) == {
        "exclude": ".mov steht bereits in der Include-Liste"
    }
    assert validate_settings({"path": r"D:\In", "include": ".mxf,.mov", "exclude": ".mov,.mxf"}) == {
        "exclude": ".mov, .mxf stehen bereits in der Include-Liste"
    }


def test_validate_settings_uses_defaults_and_validates_wait_and_recursive():
    assert validate_settings({}) == {"path": "Ordner fehlt"}
    assert validate_settings({"path": r"D:\In", "wait_s": 4}) == {"wait_s": "Mindestens 5 s"}
    assert validate_settings({"path": r"D:\In", "wait_s": 86401}) == {"wait_s": "Höchstens 86400 s"}
    assert validate_settings({"path": r"D:\In", "wait_s": True}) == {"wait_s": "Zahl erwartet"}
    assert validate_settings({"path": r"D:\In", "wait_s": "60"}) == {"wait_s": "Zahl erwartet"}
    assert validate_settings({"path": r"D:\In", "wait_s": 5}) == {}
    assert validate_settings({"path": r"D:\In", "wait_s": 7.5}) == {}
    assert validate_settings({"path": r"D:\In", "recursive": "ja"}) == {
        "recursive": "Ja oder Nein erwartet"
    }


def test_read_settings_normalizes_values_and_reports_first_error():
    assert read_settings({"path": r" D:\In ", "include": "mxf"}) == WatchSettings(
        r"D:\In", (".mxf",), (), 60.0, False
    )
    with pytest.raises(ValueError, match=r"^Ordner: Ordner fehlt$"):
        read_settings({})


@pytest.mark.parametrize(
    ("file_name", "include", "exclude", "expected"),
    [
        ("CLIP.MXF", [".mxf"], [], True),
        ("a.mov", [".mxf"], [], False),
        ("a.mov", [], [], True),
        ("a.tmp.mxf", [], [".tmp.mxf"], False),
        ("a.mxf", [".mxf"], [".mxf"], False),
    ],
)
def test_matches_extensions(file_name, include, exclude, expected):
    assert matches_extensions(file_name, include, exclude) is expected
