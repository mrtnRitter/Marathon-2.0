"""Discover and validate Marathon module descriptors."""

# IMPORTS
from collections.abc import Iterable
from copy import deepcopy
from importlib import import_module
from pkgutil import iter_modules
from types import ModuleType
from typing import Any


# CONFIG
required_keys = (
    "id", "name", "category", "icon", "description", "has_input", "outputs", "settings", "sort_order",
)


# EXCEPTIONS
class CatalogError(Exception):
    """Raised when a module catalog descriptor is invalid."""


# FUNC
def _load_modules() -> list[ModuleType]:
    """Import direct module packages and modules, excluding private names."""
    return [
        import_module(f"{__name__}.{module_info.name}")
        for module_info in iter_modules(__path__)
        if not module_info.name.startswith("_")
    ]


def build_catalog(modules: Iterable[ModuleType]) -> list[dict[str, Any]]:
    """Validate module descriptors and return independent, sorted catalog specs."""
    specs = []
    seen_ids = set()
    for module in modules:
        if not hasattr(module, "MODULE"):
            continue
        descriptor = module.MODULE
        for key in required_keys:
            if key not in descriptor:
                raise CatalogError(f"Modul {module.__name__}: Schlüssel fehlt: {key}")
        type_id = descriptor["id"]
        if type_id in seen_ids:
            raise CatalogError(f"Modultyp-ID doppelt: {type_id}")
        seen_ids.add(type_id)
        specs.append(deepcopy({key: descriptor[key] for key in required_keys}))
    return sorted(specs, key=lambda spec: (spec["sort_order"], spec["id"]))


def load_catalog() -> list[dict[str, Any]]:
    """Import direct child modules and return their validated catalog descriptors."""
    return build_catalog(_load_modules())


def catalog_module(type_id: str) -> ModuleType | None:
    """Return the imported module matching a module type id, if present."""
    return next(
        (
            module for module in _load_modules()
            if hasattr(module, "MODULE") and module.MODULE.get("id") == type_id
        ),
        None,
    )
