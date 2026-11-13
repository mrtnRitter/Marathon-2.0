"""Tests for the priolist data module."""

# IMPORTS
import pytest

from marathon.modules import load_catalog
from marathon.modules.priolist import validate_settings


# FUNC
def test_priolist_catalog_entry():
    spec = next(spec for spec in load_catalog() if spec["id"] == "priolist")

    assert spec["has_input"] is False
    assert [output["id"] for output in spec["outputs"]] == ["list"]
    assert spec["settings"][0]["key"] == "entries"
    assert spec["settings"][0]["type"] == "priolist"


def test_priolist_validate_settings_empty_and_valid():
    assert validate_settings({}) == {}
    assert validate_settings({"entries": [{"name": "A", "metadata": {"rank": 1}}, {"name": "B"}]}) == {}


def test_priolist_entries_must_be_list():
    assert validate_settings({"entries": "A"}) == {"entries": "Liste erwartet"}


@pytest.mark.parametrize("entry", [None, {}, {"name": 1}, {"name": "  "}])
def test_priolist_entry_requires_nonempty_string_name(entry):
    assert validate_settings({"entries": [entry]}) == {"entries": "Eintrag 1: Name fehlt"}


def test_priolist_metadata_must_be_dict_when_present():
    assert validate_settings({"entries": [{"name": "A", "metadata": []}]}) == {
        "entries": "Eintrag 1: Metadaten ungültig"
    }


def test_priolist_rejects_duplicate_names_case_insensitively():
    assert validate_settings({"entries": [{"name": "A"}, {"name": "a "}]}) == {
        "entries": "Eintrag 2: Name doppelt: a"
    }
