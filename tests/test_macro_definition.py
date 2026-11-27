from copy import deepcopy

import pytest

from marathon.modules import load_catalog
from marathon.server.macros.definition import (
    MacroValidationError,
    macro_hull,
    standard_definition,
    validate_definition,
)


def _type(**overrides):
    module_type = {
        "id": "test",
        "name": "Testblock",
        "category": "Test",
        "icon": "test-icon",
        "description": "Beschreibung",
        "has_input": True,
        "outputs": [{"id": "ok", "label": "OK", "description": "fertig"}, {"id": "data", "data": True, "label": "Daten", "description": ""}],
        "settings": [{"key": "value", "label": "Wert", "type": "text"}],
        "sort_order": 1,
    }
    module_type.update(overrides)
    return module_type


def _definition(**overrides):
    definition = {
        "name": "Testmodul",
        "category": "Test",
        "icon": "test-icon",
        "description": "Beschreibung",
        "sort_order": 1,
        "blocks": [{"id": "main", "type_id": "test", "name": "Testblock", "settings": {}}],
        "connections": [],
        "entry_block_id": "main",
        "outputs": [],
        "settings": [],
    }
    definition.update(overrides)
    return definition


def _assert_error(definition, expected, types=None):
    with pytest.raises(MacroValidationError) as error:
        validate_definition(definition, types or {"test": _type()})
    assert str(error.value) == expected


@pytest.mark.parametrize(
    ("definition", "expected"),
    [
        ([], "Definition ungültig: definition"),
        (_definition(blocks=None), "Definition ungültig: blocks"),
        (_definition(connections=None), "Definition ungültig: connections"),
        (_definition(outputs=None), "Definition ungültig: outputs"),
        (_definition(settings=None), "Definition ungültig: settings"),
        (_definition(blocks=[None]), "Definition ungültig: blocks"),
        (_definition(connections=[None]), "Definition ungültig: connections"),
        (_definition(outputs=[{"id": "ok", "label": "OK", "sources": [None]}]), "Definition ungültig: sources"),
        (_definition(settings=[None]), "Definition ungültig: settings"),
        (_definition(name="  "), "Name fehlt"),
        (_definition(name="x" * 101), "Name ist zu lang (max. 100 Zeichen)"),
        (_definition(category="  "), "Kategorie fehlt"),
        (_definition(category="x" * 51), "Kategorie ist zu lang (max. 50 Zeichen)"),
        (_definition(icon=""), "Icon fehlt"),
        (_definition(description="x" * 501), "Beschreibung ist zu lang (max. 500 Zeichen)"),
        (_definition(sort_order=True), "Sortierung muss eine ganze Zahl sein"),
        (_definition(blocks=[]), "Modul braucht mindestens einen Baustein"),
        (_definition(blocks=[{"id": "bad.id", "type_id": "test", "name": "x", "settings": {}}]), "Ungültige Baustein-ID: bad.id"),
        (_definition(blocks=[{"id": "main", "type_id": "test", "name": "x", "settings": {}}, {"id": "main", "type_id": "test", "name": "x", "settings": {}}]), "Baustein-ID doppelt: main"),
        (_definition(blocks=[{"id": "main", "type_id": "missing", "name": "x", "settings": {}}]), "Unbekannter Baustein: missing"),
        (_definition(blocks=[{"id": "main", "type_id": "test", "name": "  ", "settings": {}}]), "Bausteinname fehlt: main"),
        (_definition(blocks=[{"id": "main", "type_id": "test", "name": "x", "settings": []}]), "Einstellungen ungültig: main"),
        (_definition(blocks=[{"id": "main", "type_id": "test", "name": "x", "settings": {"unknown": 1}}]), "Unbekannte Einstellung unknown in Baustein main"),
        (_definition(connections=[{"source_block_id": "missing", "source_output_id": "ok", "target_block_id": "main"}]), "Verbindung zu unbekanntem Baustein: missing"),
        (_definition(connections=[{"source_block_id": "main", "source_output_id": "ok", "target_block_id": "main"}]), "Baustein main kann nicht mit sich selbst verbunden werden"),
        (_definition(connections=[{"source_block_id": "main", "source_output_id": "missing", "target_block_id": "main2"}], blocks=[{"id": "main", "type_id": "test", "name": "x", "settings": {}}, {"id": "main2", "type_id": "test", "name": "y", "settings": {}}]), "Unbekannter Ausgang missing an Baustein main"),
        (_definition(connections=[{"source_block_id": "main", "source_output_id": "ok", "target_block_id": "missing"}]), "Verbindung zu unbekanntem Baustein: missing"),
        (_definition(connections=[{"source_block_id": "main", "source_output_id": "ok", "target_block_id": "second"}, {"source_block_id": "main", "source_output_id": "ok", "target_block_id": "third"}], blocks=[{"id": "main", "type_id": "test", "name": "x", "settings": {}}, {"id": "second", "type_id": "test", "name": "y", "settings": {}}, {"id": "third", "type_id": "test", "name": "z", "settings": {}}]), "Ausgang ok an Baustein main ist mehrfach verbunden"),
        (_definition(entry_block_id="missing"), "Unbekannter Eingangs-Baustein: missing"),
        (_definition(outputs=[{"id": "Bad", "label": "Bad", "sources": []}]), "Ungültige Ausgangs-ID: Bad"),
        (_definition(outputs=[{"id": "ok", "label": "OK", "sources": []}, {"id": "ok", "label": "OK", "sources": []}]), "Ausgangs-ID doppelt: ok"),
        (_definition(outputs=[{"id": "ok", "label": "  ", "sources": []}]), "Ausgangsname fehlt: ok"),
        (_definition(outputs=[{"id": "ok", "label": "OK", "sources": [{"block_id": "missing", "output_id": "ok"}]}]), "Ungültige Ausgangsquelle: missing.ok"),
        (_definition(connections=[{"source_block_id": "main", "source_output_id": "ok", "target_block_id": "main2"}], blocks=[{"id": "main", "type_id": "test", "name": "x", "settings": {}}, {"id": "main2", "type_id": "test", "name": "y", "settings": {}}], outputs=[{"id": "out", "label": "Out", "sources": [{"block_id": "main", "output_id": "ok"}]}]), "Ausgang ist bereits intern verbunden: main.ok"),
        (_definition(outputs=[{"id": "one", "label": "One", "sources": [{"block_id": "main", "output_id": "ok"}]}, {"id": "two", "label": "Two", "sources": [{"block_id": "main", "output_id": "ok"}]}]), "Ausgangsquelle doppelt: main.ok"),
        (_definition(settings=[{"key": "Bad", "block_id": "main", "setting_key": "value", "label": "Value"}]), "Ungültiger Einstellungs-Schlüssel: Bad"),
        (_definition(settings=[{"key": "value", "block_id": "main", "setting_key": "value", "label": "Value"}, {"key": "value", "block_id": "main", "setting_key": "value", "label": "Value"}]), "Einstellungs-Schlüssel doppelt: value"),
        (_definition(settings=[{"key": "external", "block_id": "missing", "setting_key": "value", "label": "Value"}]), "Einstellung external: unbekannter Baustein missing"),
        (_definition(settings=[{"key": "external", "block_id": "main", "setting_key": "missing", "label": "Value"}]), "Einstellung external: unbekannte Einstellung missing"),
        (_definition(settings=[{"key": "one", "block_id": "main", "setting_key": "value", "label": "One"}, {"key": "two", "block_id": "main", "setting_key": "value", "label": "Two"}]), "Einstellung mehrfach freigegeben: main.value"),
        (_definition(settings=[{"key": "external", "block_id": "main", "setting_key": "value", "label": "  "}]), "Einstellung external: Beschriftung fehlt"),
        (_definition(settings=[{"key": "external", "block_id": "main", "setting_key": "value", "label": "Value", "help": "x" * 301}]), "Einstellung external: Hilfetext ist zu lang (max. 300 Zeichen)"),
    ],
)
def test_validate_definition_errors(definition, expected):
    _assert_error(definition, expected)


def test_standard_definition_catalog_entries_and_full_examples():
    catalog = load_catalog()
    types = {item["id"]: item for item in catalog}
    for module_type in catalog:
        validate_definition(standard_definition(module_type), types)

    copy_type = types["copy_file"]
    copy_definition = standard_definition(copy_type)
    assert copy_definition == {
        "name": "Datei kopieren", "category": "Dateien", "icon": "copy",
        "description": "Kopiert die Datei des Assets unter ihrem Originalnamen in den Zielordner. Die Kopie läuft als neues Asset weiter, das Original ist danach beendet.", "sort_order": 30,
        "blocks": [{"id": "main", "type_id": "copy_file", "name": "Datei kopieren", "position": {"x": 0, "y": 0}, "settings": {}}],
        "connections": [], "entry_block_id": "main",
        "outputs": [
            {"id": "success", "label": "Erfolg", "description": "Die Kopie läuft als neues Asset weiter", "sources": [{"block_id": "main", "output_id": "success"}]},
            {"id": "error", "label": "Fehler", "description": "Kopieren fehlgeschlagen oder Checksumme stimmt nicht", "sources": [{"block_id": "main", "output_id": "error"}]},
        ],
        "settings": [
            {"key": "target", "block_id": "main", "setting_key": "target", "label": "Zielordner", "help": "Lokaler Pfad, Laufwerksbuchstabe oder Netzpfad (\\\\server\\freigabe)"},
            {"key": "checksum", "block_id": "main", "setting_key": "checksum", "label": "Checksumme", "help": "Mit Checksumme gilt die Kopie erst als erfolgreich, wenn die Prüfsummen von Quelle und Kopie übereinstimmen"},
            {"key": "numbered", "block_id": "main", "setting_key": "numbered", "label": "Namenskonflikt lösen", "help": "Ist der Name im Ziel belegt, wird _1, _2 … angehängt; sonst Fehler"},
            {"key": "parallel", "block_id": "main", "setting_key": "parallel", "label": "Parallele Verarbeitung", "help": "So viele Assets bearbeitet ein Job gleichzeitig, jedes in einem eigenen Strom"},
        ],
    }

    watch_type = types["watchfolder"]
    assert standard_definition(watch_type) == {
        "name": "Watchfolder", "category": "Quelle", "icon": "folder-input",
        "description": "Überwacht einen Ordner und übernimmt neue Dateien als Assets, sobald sie sich eine Wartezeit lang nicht mehr ändern.", "sort_order": 10,
        "blocks": [{"id": "main", "type_id": "watchfolder", "name": "Watchfolder", "position": {"x": 0, "y": 0}, "settings": {}}],
        "connections": [], "entry_block_id": None,
        "outputs": [{"id": "success", "label": "Erfolg", "description": "Datei ist bereit für die Verarbeitung", "sources": [{"block_id": "main", "output_id": "success"}]}],
        "settings": [
            {"key": "path", "block_id": "main", "setting_key": "path", "label": "Ordner", "help": "Lokaler Pfad, Laufwerksbuchstabe oder Netzpfad (\\\\server\\freigabe)"},
            {"key": "include", "block_id": "main", "setting_key": "include", "label": "Include-Endungen", "help": "z. B. .mxf, .mov · leer = alle Dateien"},
            {"key": "exclude", "block_id": "main", "setting_key": "exclude", "label": "Exclude-Endungen", "help": "Diese Endungen werden nie übernommen"},
            {"key": "wait_s", "block_id": "main", "setting_key": "wait_s", "label": "Wartezeit", "help": "So lange dürfen sich Größe und Zeitstempel nicht ändern; zugleich Abstand der Ordnerprüfung"},
            {"key": "recursive", "block_id": "main", "setting_key": "recursive", "label": "Unterordner einbeziehen", "help": ""},
        ],
    }

    priolist_type = types["priolist"]
    assert standard_definition(priolist_type) == {
        "name": "Prioliste", "category": "Daten", "icon": "list-ordered",
        "description": "Liste von Metadaten-Einträgen; die Position in der Liste ist die Priorität. Mit dem Eingang eines Moduls verbinden, das die Assets danach verarbeitet.", "sort_order": 60,
        "blocks": [{"id": "main", "type_id": "priolist", "name": "Prioliste", "position": {"x": 0, "y": 0}, "settings": {}}],
        "connections": [], "entry_block_id": None,
        "outputs": [{"id": "list", "label": "Prioliste", "description": "Zusätzlich zum Asset-Fluss mit dem Eingang eines Moduls verbinden", "sources": [{"block_id": "main", "output_id": "list"}]}],
        "settings": [{"key": "entries", "block_id": "main", "setting_key": "entries", "label": "Einträge", "help": ""}],
    }


def test_macro_hull_maps_settings_outputs_and_preserves_inputs():
    module_type = _type(
        outputs=[{"id": "ok", "label": "OK", "description": "fertig"}, {"id": "data", "data": True, "label": "Daten", "description": ""}, None, {"label": "ignored"}],
        settings=[
            {"key": "fixed", "label": "Fest", "type": "text", "default": "type-default", "help": " type-help "},
            {"key": "included", "label": "Freigegeben", "type": "text", "default": "setting-default", "help": "setting-help", "exclusive_with": "excluded"},
            {"key": "excluded", "label": "Ausgeschlossen", "type": "text", "exclusive_with": "included"},
            {"key": "no_default", "label": "Ohne Vorgabe", "type": "text", "help": ""},
        ],
    )
    definition = _definition(
        blocks=[{"id": "main", "type_id": "test", "name": "Testblock", "settings": {"fixed": "fixed-value"}}],
        outputs=[{"id": "ok", "label": "OK", "description": "fertig", "sources": [{"block_id": "main", "output_id": "ok"}]}, {"id": "data_out", "label": "Daten", "description": "", "sources": [{"block_id": "main", "output_id": "data"}]}],
        settings=[{"key": "fixed_key", "block_id": "main", "setting_key": "fixed", "label": "Festwert", "help": ""}, {"key": "included_key", "block_id": "main", "setting_key": "included", "label": "Freigegeben", "help": " custom-help "}, {"key": "excluded_key", "block_id": "main", "setting_key": "excluded", "label": "Ausgeschlossen", "help": ""}, {"key": "alone", "block_id": "main", "setting_key": "no_default", "label": "Allein", "help": ""}],
    )
    original_definition = deepcopy(definition)
    original_type = deepcopy(module_type)
    hull = macro_hull("macro-1", 3, definition, {"test": module_type})
    assert hull == {
        "id": "macro-1", "version": 3, "name": "Testmodul", "category": "Test", "icon": "test-icon", "description": "Beschreibung", "sort_order": 1, "has_input": True,
        "outputs": [{"id": "ok", "label": "OK", "description": "fertig"}, {"id": "data_out", "label": "Daten", "description": "", "data": True}],
        "settings": [
            {"key": "fixed_key", "label": "Festwert", "type": "text", "default": "fixed-value", "help": " type-help "},
            {"key": "included_key", "label": "Freigegeben", "type": "text", "default": "setting-default", "help": " custom-help ", "exclusive_with": "excluded_key"},
            {"key": "excluded_key", "label": "Ausgeschlossen", "type": "text", "exclusive_with": "included_key"},
            {"key": "alone", "label": "Allein", "type": "text"},
        ],
    }
    assert definition == original_definition
    assert module_type == original_type



def test_macro_hull_treats_missing_optional_lists_as_empty():
    module_types = {"test": _type()}
    definition = _definition()
    for field in ("connections", "outputs", "settings"):
        definition.pop(field)

    hull = macro_hull("macro", 1, definition, module_types)
    assert hull["outputs"] == []
    assert hull["settings"] == []

    definition = _definition(
        outputs=[{"id": "ok", "label": "OK"}],
    )
    definition.pop("connections")
    definition.pop("settings")
    hull = macro_hull("macro", 1, definition, module_types)
    assert hull["outputs"] == [{"id": "ok", "label": "OK", "description": ""}]


def test_macro_hull_defaults_missing_output_description():
    definition = _definition(
        outputs=[{"id": "ok", "label": "OK", "sources": [{"block_id": "main", "output_id": "ok"}]}],
    )
    hull = macro_hull("macro", 1, definition, {"test": _type()})
    assert hull["outputs"] == [{"id": "ok", "label": "OK", "description": ""}]


def test_input_errors():
    entry_definition = _definition(entry_block_id="main")
    _assert_error(entry_definition, "Eingangs-Baustein hat keinen Eingang: main", {"test": _type(has_input=False)})

    connection_definition = _definition(
        blocks=[{"id": "main", "type_id": "test", "name": "x", "settings": {}}, {"id": "sink", "type_id": "sink", "name": "y", "settings": {}}],
        connections=[{"source_block_id": "main", "source_output_id": "ok", "target_block_id": "sink"}],
    )
    _assert_error(connection_definition, "Baustein sink hat keinen Eingang", {"test": _type(), "sink": _type(id="sink", has_input=False)})


def test_watchfolder_hull_remaps_exclusive_settings_and_priolist_data():
    catalog = {item["id"]: item for item in load_catalog()}
    watchfolder = standard_definition(catalog["watchfolder"])
    watchfolder["settings"] = [
        {"key": "include_new", "block_id": "main", "setting_key": "include", "label": "Include", "help": ""},
        {"key": "exclude_new", "block_id": "main", "setting_key": "exclude", "label": "Exclude", "help": ""},
    ]
    both = macro_hull("watch", 2, watchfolder, catalog)
    assert both["has_input"] is False
    assert [field["exclusive_with"] for field in both["settings"]] == ["exclude_new", "include_new"]
    watchfolder["settings"].pop()
    only_include = macro_hull("watch", 2, watchfolder, catalog)
    assert "exclusive_with" not in only_include["settings"][0]

    priolist = standard_definition(catalog["priolist"])
    priolist_hull = macro_hull("priority", 1, priolist, catalog)
    assert priolist_hull["outputs"][0]["data"] is True


def test_validate_ignores_malformed_type_outputs():
    definition = _definition(outputs=[{"id": "ok", "label": "OK", "sources": [{"block_id": "main", "output_id": "ok"}]}])
    validate_definition(definition, {"test": _type(outputs=[None, {"label": "missing id"}, {"id": "ok"}])})


def test_functions_return_independent_copies():
    module_type = _type()
    standard = standard_definition(module_type)
    standard["blocks"][0]["settings"]["x"] = 1
    assert "x" not in module_type
    catalog_type = next(item for item in load_catalog() if item["id"] == "copy_file")
    first = standard_definition(catalog_type)
    first["blocks"][0]["position"]["x"] = 99
    assert standard_definition(catalog_type)["blocks"][0]["position"]["x"] == 0
    hull = macro_hull("m", 1, _definition(), {"test": module_type})
    hull["outputs"].append({"id": "extra"})
    assert len(module_type["outputs"]) == 2
