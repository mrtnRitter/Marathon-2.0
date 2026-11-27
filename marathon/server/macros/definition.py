"""Validate macro definitions and derive standard definitions and hulls."""

# IMPORTS
import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any


# CONFIG
block_id_pattern = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
output_id_pattern = re.compile(r"^[a-z0-9_]{1,64}$")
setting_key_pattern = re.compile(r"^[a-z0-9_]{1,64}$")


# EXCEPTIONS
class MacroValidationError(ValueError):
    """Raised when a macro definition violates its validation contract."""


# FUNC
def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _definition_error(field: str) -> None:
    raise MacroValidationError(f"Definition ungültig: {field}")


def _block_output(module_type: Mapping[str, Any], output_id: Any) -> Mapping[str, Any] | None:
    return next(
        (
            output for output in module_type.get("outputs", [])
            if isinstance(output, dict) and "id" in output and output["id"] == output_id
        ),
        None,
    )


def _block_setting(module_type: Mapping[str, Any], setting_key: Any) -> Mapping[str, Any] | None:
    return next(
        (
            setting for setting in module_type.get("settings", [])
            if isinstance(setting, dict) and setting.get("key") == setting_key
        ),
        None,
    )


def validate_definition(
    definition: Mapping[str, Any],
    module_types: Mapping[str, Mapping[str, Any]],
) -> None:
    """Raise the first contract-defined validation error, if any."""
    if not isinstance(definition, dict):
        _definition_error("definition")

    list_fields = ("blocks", "connections", "outputs", "settings")
    values = {}
    for field in list_fields:
        value = definition.get(field, [])
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            _definition_error(field)
        values[field] = value
    for output in values["outputs"]:
        sources = output.get("sources", [])
        if not isinstance(sources, list) or any(not isinstance(source, dict) for source in sources):
            _definition_error("sources")

    name = _text(definition.get("name"))
    if not name:
        raise MacroValidationError("Name fehlt")
    if len(name) > 100:
        raise MacroValidationError("Name ist zu lang (max. 100 Zeichen)")

    category = _text(definition.get("category"))
    if not category:
        raise MacroValidationError("Kategorie fehlt")
    if len(category) > 50:
        raise MacroValidationError("Kategorie ist zu lang (max. 50 Zeichen)")

    if not _text(definition.get("icon")):
        raise MacroValidationError("Icon fehlt")
    if len(_text(definition.get("description"))) > 500:
        raise MacroValidationError("Beschreibung ist zu lang (max. 500 Zeichen)")
    sort_order = definition.get("sort_order")
    if type(sort_order) is not int:
        raise MacroValidationError("Sortierung muss eine ganze Zahl sein")

    blocks = values["blocks"]
    if not blocks:
        raise MacroValidationError("Modul braucht mindestens einen Baustein")

    block_by_id = {}
    type_by_block_id = {}
    for block in blocks:
        block_id = block.get("id")
        if not isinstance(block_id, str) or block_id_pattern.fullmatch(block_id) is None:
            raise MacroValidationError(f"Ungültige Baustein-ID: {block_id}")
        if block_id in block_by_id:
            raise MacroValidationError(f"Baustein-ID doppelt: {block_id}")
        block_by_id[block_id] = block

        type_id = block.get("type_id")
        module_type = module_types.get(type_id) if isinstance(type_id, str) else None
        if module_type is None:
            raise MacroValidationError(f"Unbekannter Baustein: {type_id}")
        type_by_block_id[block_id] = module_type

        if not _text(block.get("name")):
            raise MacroValidationError(f"Bausteinname fehlt: {block_id}")
        settings = block.get("settings")
        if not isinstance(settings, dict):
            raise MacroValidationError(f"Einstellungen ungültig: {block_id}")
        for setting_key in settings:
            if _block_setting(module_type, setting_key) is None:
                raise MacroValidationError(f"Unbekannte Einstellung {setting_key} in Baustein {block_id}")

    internal_sources = set()
    for connection in values["connections"]:
        source_block_id = connection.get("source_block_id")
        target_block_id = connection.get("target_block_id")
        source_block = block_by_id.get(source_block_id)
        target_block = block_by_id.get(target_block_id)
        if source_block is None:
            raise MacroValidationError(f"Verbindung zu unbekanntem Baustein: {source_block_id}")
        if target_block is None:
            raise MacroValidationError(f"Verbindung zu unbekanntem Baustein: {target_block_id}")
        if source_block_id == target_block_id:
            raise MacroValidationError(f"Baustein {source_block_id} kann nicht mit sich selbst verbunden werden")
        output_id = connection.get("source_output_id")
        if _block_output(type_by_block_id[source_block_id], output_id) is None:
            raise MacroValidationError(f"Unbekannter Ausgang {output_id} an Baustein {source_block_id}")
        if not type_by_block_id[target_block_id].get("has_input"):
            raise MacroValidationError(f"Baustein {target_block_id} hat keinen Eingang")
        source = (source_block_id, output_id)
        if source in internal_sources:
            raise MacroValidationError(f"Ausgang {output_id} an Baustein {source_block_id} ist mehrfach verbunden")
        internal_sources.add(source)

    entry_block_id = definition.get("entry_block_id")
    if entry_block_id is not None:
        if entry_block_id not in block_by_id:
            raise MacroValidationError(f"Unbekannter Eingangs-Baustein: {entry_block_id}")
        if not type_by_block_id[entry_block_id].get("has_input"):
            raise MacroValidationError(f"Eingangs-Baustein hat keinen Eingang: {entry_block_id}")

    seen_output_ids = set()
    seen_sources = set()
    for output in values["outputs"]:
        output_id = output.get("id")
        if not isinstance(output_id, str) or output_id_pattern.fullmatch(output_id) is None:
            raise MacroValidationError(f"Ungültige Ausgangs-ID: {output_id}")
        if output_id in seen_output_ids:
            raise MacroValidationError(f"Ausgangs-ID doppelt: {output_id}")
        seen_output_ids.add(output_id)
        if not _text(output.get("label")):
            raise MacroValidationError(f"Ausgangsname fehlt: {output_id}")
        if len(_text(output.get("label"))) > 50:
            raise MacroValidationError(f"Ausgangsname fehlt: {output_id}")

        sources = output.get("sources", [])
        for source in sources:
            block_id = source.get("block_id")
            source_output_id = source.get("output_id")
            module_type = type_by_block_id.get(block_id)
            if module_type is None or _block_output(module_type, source_output_id) is None:
                raise MacroValidationError(f"Ungültige Ausgangsquelle: {block_id}.{source_output_id}")
            source_key = (block_id, source_output_id)
            if source_key in internal_sources:
                raise MacroValidationError(f"Ausgang ist bereits intern verbunden: {block_id}.{source_output_id}")
            if source_key in seen_sources:
                raise MacroValidationError(f"Ausgangsquelle doppelt: {block_id}.{source_output_id}")
            seen_sources.add(source_key)

        if any(_block_output(type_by_block_id[source["block_id"]], source["output_id"]).get("data") is True for source in sources) and len(sources) != 1:
            raise MacroValidationError(f"Daten-Ausgang braucht genau eine Quelle: {output_id}")

    seen_setting_keys = set()
    granted_settings = set()
    for setting in values["settings"]:
        key = setting.get("key")
        if not isinstance(key, str) or setting_key_pattern.fullmatch(key) is None:
            raise MacroValidationError(f"Ungültiger Einstellungs-Schlüssel: {key}")
        if key in seen_setting_keys:
            raise MacroValidationError(f"Einstellungs-Schlüssel doppelt: {key}")
        seen_setting_keys.add(key)

        block_id = setting.get("block_id")
        if block_id not in block_by_id:
            raise MacroValidationError(f"Einstellung {key}: unbekannter Baustein {block_id}")
        setting_key = setting.get("setting_key")
        if _block_setting(type_by_block_id[block_id], setting_key) is None:
            raise MacroValidationError(f"Einstellung {key}: unbekannte Einstellung {setting_key}")
        granted_key = (block_id, setting_key)
        if granted_key in granted_settings:
            raise MacroValidationError(f"Einstellung mehrfach freigegeben: {block_id}.{setting_key}")
        granted_settings.add(granted_key)
        if not _text(setting.get("label")):
            raise MacroValidationError(f"Einstellung {key}: Beschriftung fehlt")
        if len(_text(setting.get("label"))) > 100:
            raise MacroValidationError(f"Einstellung {key}: Beschriftung fehlt")
        if len(_text(setting.get("help"))) > 300:
            raise MacroValidationError(f"Einstellung {key}: Hilfetext ist zu lang (max. 300 Zeichen)")


def standard_definition(module_type: Mapping[str, Any]) -> dict[str, Any]:
    """Build an independent standard macro definition for one module type."""
    type_id = module_type["id"]
    return deepcopy({
        "name": module_type["name"],
        "category": module_type["category"],
        "icon": module_type["icon"],
        "description": module_type["description"],
        "sort_order": module_type["sort_order"],
        "blocks": [{"id": "main", "type_id": type_id, "name": module_type["name"], "position": {"x": 0, "y": 0}, "settings": {}}],
        "connections": [],
        "entry_block_id": "main" if module_type["has_input"] else None,
        "outputs": [
            {"id": output["id"], "label": output["label"], "description": output.get("description", ""), "sources": [{"block_id": "main", "output_id": output["id"]}]}
            for output in module_type.get("outputs", [])
            if isinstance(output, dict) and "id" in output
        ],
        "settings": [
            {"key": setting["key"], "block_id": "main", "setting_key": setting["key"], "label": setting["label"], "help": setting.get("help", "")}
            for setting in module_type.get("settings", [])
        ],
    })


def macro_hull(
    macro_id: str,
    version: int,
    definition: Mapping[str, Any],
    module_types: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Build an independent module-type-shaped hull from a macro definition."""
    blocks = {block["id"]: block for block in definition["blocks"]}
    types = {block_id: module_types[block["type_id"]] for block_id, block in blocks.items()}
    released = {
        (setting["block_id"], setting["setting_key"]): setting["key"]
        for setting in definition.get("settings", [])
    }
    outputs = []
    for output in definition.get("outputs", []):
        hull_output = {
            "id": deepcopy(output["id"]),
            "label": deepcopy(output["label"]),
            "description": deepcopy(output.get("description", "")),
        }
        if any(
            _block_output(types[source["block_id"]], source["output_id"]).get("data") is True
            for source in output.get("sources", [])
        ):
            hull_output["data"] = True
        outputs.append(hull_output)

    settings = []
    for setting in definition.get("settings", []):
        block_id = setting["block_id"]
        setting_key = setting["setting_key"]
        descriptor = _block_setting(types[block_id], setting_key)
        hull_setting = deepcopy(dict(descriptor))
        hull_setting["key"] = setting["key"]
        hull_setting["label"] = setting["label"]
        help_text = setting.get("help")
        if not isinstance(help_text, str) or not help_text.strip():
            help_text = descriptor.get("help")
        hull_setting.pop("help", None)
        if isinstance(help_text, str) and help_text.strip():
            hull_setting["help"] = deepcopy(help_text)
        block_settings = blocks[block_id].get("settings", {})
        if setting_key in block_settings:
            hull_setting["default"] = deepcopy(block_settings[setting_key])
        elif "default" in descriptor:
            hull_setting["default"] = deepcopy(descriptor["default"])
        else:
            hull_setting.pop("default", None)
        exclusive_with = descriptor.get("exclusive_with")
        partner_key = released.get((block_id, exclusive_with))
        if partner_key is None:
            hull_setting.pop("exclusive_with", None)
        else:
            hull_setting["exclusive_with"] = partner_key
        settings.append(hull_setting)

    return deepcopy({
        "id": macro_id,
        "version": version,
        "name": definition["name"],
        "category": definition["category"],
        "icon": definition["icon"],
        "description": definition["description"],
        "sort_order": definition["sort_order"],
        "has_input": definition.get("entry_block_id") is not None,
        "outputs": outputs,
        "settings": settings,
    })
