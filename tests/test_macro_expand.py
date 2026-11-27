from copy import deepcopy

import pytest

from marathon.server.macros.expand import block_settings, expand_graph, remap_settings, runtime_id


COPY_RENAME = {
    "blocks": [
        {"id": "copy", "type_id": "copy_file", "name": "Copy", "position": {"x": 1, "y": 2}, "settings": {"overwrite": False, "target": "fixed"}},
        {"id": "rename", "type_id": "rename_file", "name": "Rename", "position": {"x": 10, "y": 20}, "settings": {"prefix": "x_"}},
    ],
    "connections": [{"source_block_id": "copy", "source_output_id": "success", "target_block_id": "rename"}],
    "entry_block_id": "copy",
    "outputs": [
        {"id": "success", "sources": [{"block_id": "rename", "output_id": "success"}]},
        {"id": "error", "sources": [{"block_id": "copy", "output_id": "error"}, {"block_id": "rename", "output_id": "error"}]},
    ],
    "settings": [{"key": "ziel", "block_id": "copy", "setting_key": "target"}],
}

COPY_FILE = {
    "blocks": [{"id": "main", "type_id": "copy_file", "name": "Copy", "position": {"x": 0, "y": 0}, "settings": {}}],
    "connections": [],
    "entry_block_id": "main",
    "outputs": [
        {"id": "success", "sources": [{"block_id": "main", "output_id": "success"}]},
        {"id": "error", "sources": [{"block_id": "main", "output_id": "error"}]},
    ],
    "settings": [],
}


def test_expand_macro_and_external_connections_in_contract_order():
    instances = [
        {"id": "A", "name": "A", "macro_id": "copy_rename", "macro_version": 1, "settings": {"ziel": "D:/out", "unbekannt": 1}, "position": {"x": 10, "y": 20}},
        {"id": "B", "name": "B", "macro_id": "copy_file", "macro_version": 1, "position": {"x": 30, "y": 40}},
    ]
    connections = [
        {"source_module_id": "A", "source_output_id": "success", "target_module_id": "B"},
        {"source_module_id": "A", "source_output_id": "error", "target_module_id": "B"},
    ]
    definitions = {("copy_rename", 1): COPY_RENAME, ("copy_file", 1): COPY_FILE}

    modules, runtime_connections = expand_graph(instances, connections, definitions)

    assert modules == [
        {"id": "A.copy", "instance_id": "A", "block_id": "copy", "type_id": "copy_file", "name": "A", "description": "", "position": {"x": 10, "y": 20}, "settings": {"overwrite": False, "target": "D:/out"}, "locked": False, "exits": {"error": "error"}},
        {"id": "A.rename", "instance_id": "A", "block_id": "rename", "type_id": "rename_file", "name": "A", "description": "", "position": {"x": 10, "y": 20}, "settings": {"prefix": "x_"}, "locked": False, "exits": {"success": "success", "error": "error"}},
        {"id": "B.main", "instance_id": "B", "block_id": "main", "type_id": "copy_file", "name": "B", "description": "", "position": {"x": 30, "y": 40}, "settings": {}, "locked": False, "exits": {"success": "success", "error": "error"}},
    ]
    assert runtime_connections == [
        {"source_module_id": "A.copy", "source_output_id": "success", "target_module_id": "A.rename"},
        {"source_module_id": "A.rename", "source_output_id": "success", "target_module_id": "B.main"},
        {"source_module_id": "A.copy", "source_output_id": "error", "target_module_id": "B.main"},
        {"source_module_id": "A.rename", "source_output_id": "error", "target_module_id": "B.main"},
    ]


def test_data_outputs_are_expanded_like_other_outputs():
    priolist = {
        "blocks": [{"id": "main", "type_id": "priolist", "name": "Priority", "position": {"x": 0, "y": 0}, "settings": {}}],
        "connections": [], "entry_block_id": "main",
        "outputs": [{"id": "data", "sources": [{"block_id": "main", "output_id": "data"}]}], "settings": [],
    }
    sink = {"blocks": [{"id": "main", "type_id": "sink", "name": "Sink", "position": {}, "settings": {}}], "connections": [], "entry_block_id": "main", "outputs": [], "settings": []}
    runtime_modules, expanded = expand_graph(
        [{"id": "P", "name": "P", "macro_id": "priolist", "macro_version": 1, "position": {}, "locked": True}, {"id": "S", "name": "S", "macro_id": "sink", "macro_version": 1, "position": {}}],
        [{"source_module_id": "P", "source_output_id": "data", "target_module_id": "S"}],
        {("priolist", 1): priolist, ("sink", 1): sink},
    )
    assert runtime_modules[0]["locked"] is True
    assert expanded == [{"source_module_id": "P.main", "source_output_id": "data", "target_module_id": "S.main"}]


def test_output_without_sources_creates_no_runtime_connection():
    empty_output = {
        "blocks": [{"id": "main", "type_id": "empty", "name": "Empty", "position": {}, "settings": {}}],
        "connections": [], "entry_block_id": "main",
        "outputs": [{"id": "unused", "sources": []}], "settings": [],
    }
    sink = {"blocks": [{"id": "main", "type_id": "sink", "name": "Sink", "position": {}, "settings": {}}], "connections": [], "entry_block_id": "main", "outputs": [], "settings": []}
    _, expanded = expand_graph(
        [{"id": "E", "name": "E", "macro_id": "empty", "macro_version": 1, "position": {}}, {"id": "S", "name": "S", "macro_id": "sink", "macro_version": 1, "position": {}}],
        [{"source_module_id": "E", "source_output_id": "unused", "target_module_id": "S"}],
        {("empty", 1): empty_output, ("sink", 1): sink},
    )
    assert expanded == []


def test_block_settings_keep_fixed_values_and_ignore_unknown_instance_keys():
    assert block_settings(COPY_RENAME, "copy", {"ziel": "D:/out", "unknown": 1}) == {"overwrite": False, "target": "D:/out"}
    assert block_settings(COPY_RENAME, "copy", {}) == {"overwrite": False, "target": "fixed"}


def test_errors_for_missing_definition_unknown_instance_output_and_entry():
    with pytest.raises(KeyError):
        expand_graph([{"id": "M", "macro_id": "missing", "macro_version": 9}], [], {})

    with pytest.raises(ValueError, match="^Verbindung zu unbekanntem Modul: ghost$"):
        expand_graph([], [{"source_module_id": "ghost", "source_output_id": "success", "target_module_id": "X"}], {})

    with pytest.raises(ValueError, match="^Verbindung zu unbekanntem Modul: ghost$"):
        expand_graph(
            [{"id": "A", "name": "A", "macro_id": "copy_file", "macro_version": 1, "position": {}}],
            [{"source_module_id": "A", "source_output_id": "success", "target_module_id": "ghost"}],
            {("copy_file", 1): COPY_FILE},
        )

    with pytest.raises(ValueError, match="^Unbekannter Ausgang absent an Modul A$"):
        expand_graph(
            [{"id": "A", "name": "A", "macro_id": "copy_rename", "macro_version": 1, "position": {}}, {"id": "B", "name": "B", "macro_id": "copy_file", "macro_version": 1, "position": {}}],
            [{"source_module_id": "A", "source_output_id": "absent", "target_module_id": "B"}],
            {("copy_rename", 1): COPY_RENAME, ("copy_file", 1): COPY_FILE},
        )

    no_entry = {**COPY_FILE, "entry_block_id": None}
    with pytest.raises(ValueError, match="^Modul B hat keinen Eingang$"):
        expand_graph(
            [{"id": "A", "name": "A", "macro_id": "copy_file", "macro_version": 1, "position": {}}, {"id": "B", "name": "B", "macro_id": "no_entry", "macro_version": 1, "position": {}}],
            [{"source_module_id": "A", "source_output_id": "success", "target_module_id": "B"}],
            {("copy_file", 1): COPY_FILE, ("no_entry", 1): no_entry},
        )


def test_remap_settings_preserves_input_order_and_sorts_removed_keys():
    assert remap_settings({"z": 1, "gone_b": 2, "a": 3, "gone_a": 4}, {"settings": [{"key": "z"}, {"key": "a"}]}) == ({"z": 1, "a": 3}, ["gone_a", "gone_b"])


def test_inputs_are_not_mutated_and_returned_values_are_independent():
    instances = [{"id": "A", "name": "A", "macro_id": "copy_rename", "macro_version": 1, "settings": {"ziel": "D:/out"}, "position": {"x": 8, "y": 9}}]
    connections = []
    definitions = {("copy_rename", 1): deepcopy(COPY_RENAME)}
    before = deepcopy((instances, connections, definitions))
    modules, _ = expand_graph(instances, connections, definitions)
    assert (instances, connections, definitions) == before
    modules[0]["position"]["x"] = -1
    modules[0]["settings"]["target"] = "changed"
    assert instances[0]["position"]["x"] == 8
    assert definitions[("copy_rename", 1)]["blocks"][0]["settings"]["target"] == "fixed"
    assert runtime_id("A", "copy") == "A.copy"
