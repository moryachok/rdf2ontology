"""YAML configuration: strict schema validation, defaults and lookup helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

PLACEHOLDER_GUID = "00000000-0000-0000-0000-000000000000"


class ConfigError(Exception):
    """Raised for a malformed or unknown configuration key."""


_SECTION_KEYS: dict[str, set[str]] = {
    "ontology": {"displayName", "logicalId"},
    "rdf": {
        "annotationNamespace",
        "sourceTableProperty",
        "sourceColumnProperty",
        "sourceLakehouseProperty",
        "keyProperty",
        "keyValue",
        "joinConditionProperty",
        "synonymsProperty",
    },
    "fabric": {"workspaceId", "lakehouses", "environments"},
    "defaults": {
        "emitUnboundEntities",
        "emitUnboundRelationships",
        "emitInverseRelationships",
        "emitForeignKeyProperties",
        "maxPortalNameLength",
        "unmappedRangeValueType",
        "autoPrefixTimeseries",
    },
    "lint": {"failOn", "ignore"},
    "overrides": {"valueTypes", "rename"},
    "exclude": {"classes", "properties"},
}
_FREEFORM_SECTIONS = {"entities", "relationships"}
_ENTITY_KEYS = {"key", "displayNameProperty", "sourceTable", "sourceLakehouse", "timeseries", "exclude"}
_TIMESERIES_KEYS = {"table", "timestampColumn", "properties", "lakehouse"}
_RELATIONSHIP_KEYS = {"name", "linkTable", "sourceKeyColumns", "targetKeyColumns", "exclude"}
_LAKEHOUSE_KEYS = {"itemId", "defaultSchema", "workspaceId"}

_DEFAULTS: dict[str, Any] = {
    "emitUnboundEntities": True,
    "emitUnboundRelationships": True,
    "emitInverseRelationships": False,
    "emitForeignKeyProperties": True,
    "maxPortalNameLength": 26,
    "unmappedRangeValueType": "String",
    "autoPrefixTimeseries": True,
}

_RDF_DEFAULTS: dict[str, Any] = {
    "annotationNamespace": None,
    "sourceTableProperty": "sourceTable",
    "sourceColumnProperty": "sourceColumn",
    "sourceLakehouseProperty": "sourceLakehouse",
    "keyProperty": "isKey",
    "keyValue": "true",
    "joinConditionProperty": None,
    "synonymsProperty": "synonyms",
}


@dataclass
class LakehouseRef:
    name: str
    item_id: str = PLACEHOLDER_GUID
    workspace_id: Optional[str] = None
    default_schema: str = "dbo"


@dataclass
class Config:
    display_name: Optional[str] = None
    logical_id: Optional[str] = None
    rdf: dict[str, Any] = field(default_factory=lambda: dict(_RDF_DEFAULTS))
    workspace_id: str = PLACEHOLDER_GUID
    lakehouses: dict[str, LakehouseRef] = field(default_factory=dict)
    environments: dict[str, Any] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=lambda: dict(_DEFAULTS))
    lint_fail_on: str = "error"
    lint_ignore: list[str] = field(default_factory=list)
    entities: dict[str, Any] = field(default_factory=dict)
    relationships: dict[str, Any] = field(default_factory=dict)
    value_type_overrides: dict[str, str] = field(default_factory=dict)
    renames: dict[str, str] = field(default_factory=dict)
    excluded_classes: set[str] = field(default_factory=set)
    excluded_properties: set[str] = field(default_factory=set)
    path: Optional[Path] = None
    # Deployment identifiers supplied on the CLI, used when the RDF/config carries none.
    cli_workspace_id: Optional[str] = None
    cli_lakehouse_id: Optional[str] = None
    cli_lakehouse_ids: dict[str, str] = field(default_factory=dict)
    cli_schema: str = "dbo"

    # -- lookups -----------------------------------------------------------
    def entity(self, name: str) -> dict[str, Any]:
        return self.entities.get(name, {})

    def relationship(self, name: str) -> dict[str, Any]:
        return self.relationships.get(name, {})

    def lakehouse(self, name: Optional[str]) -> LakehouseRef:
        """Always resolves: falls back to a synthetic ref filled with CLI overrides / placeholders."""
        if name and name in self.lakehouses:
            return self.lakehouses[name]
        if len(self.lakehouses) == 1:
            return next(iter(self.lakehouses.values()))
        item_id = (self.cli_lakehouse_ids.get(name) if name else None) or self.cli_lakehouse_id or PLACEHOLDER_GUID
        return LakehouseRef(
            name=name or "default",
            item_id=item_id,
            workspace_id=self.cli_workspace_id,
            default_schema=self.cli_schema,
        )

    def value_type_override(self, entity: str, prop: str) -> Optional[str]:
        return self.value_type_overrides.get(f"{entity}.{prop}") or self.value_type_overrides.get(prop)

    def rename(self, name: str) -> str:
        return self.renames.get(name, name)

    def is_class_excluded(self, name: str) -> bool:
        return name in self.excluded_classes or bool(self.entity(name).get("exclude"))

    def is_property_excluded(self, entity: str, prop: str) -> bool:
        return f"{entity}.{prop}" in self.excluded_properties or prop in self.excluded_properties

    def flag(self, name: str) -> Any:
        return self.defaults.get(name, _DEFAULTS.get(name))


def _require_mapping(value: Any, where: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"'{where}' must be a mapping, got {type(value).__name__}")
    return value


def _reject_unknown(section: str, data: dict, allowed: set[str]) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ConfigError(
            f"unknown key(s) in '{section}': {', '.join(unknown)}. Allowed: {', '.join(sorted(allowed))}"
        )


def apply_cli_overrides(
    config: Config,
    workspace_id: Optional[str] = None,
    lakehouse_ids: Optional[list[str]] = None,
    schema: Optional[str] = None,
) -> None:
    """Wire --workspace-id/--lakehouse-id/--schema into the config, config values winning."""
    if workspace_id:
        config.cli_workspace_id = workspace_id
        if config.workspace_id == PLACEHOLDER_GUID:
            config.workspace_id = workspace_id
    if schema:
        config.cli_schema = schema
    for entry in lakehouse_ids or []:
        if "=" in entry:
            key, value = entry.split("=", 1)
            config.cli_lakehouse_ids[key.strip()] = value.strip()
        else:
            config.cli_lakehouse_id = entry.strip()
    for ref in config.lakehouses.values():
        if ref.item_id == PLACEHOLDER_GUID:
            override = config.cli_lakehouse_ids.get(ref.name) or config.cli_lakehouse_id
            if override:
                ref.item_id = override
        if not ref.workspace_id and config.cli_workspace_id:
            ref.workspace_id = config.cli_workspace_id


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge two parsed YAML documents: dicts merge key-by-key, lists concatenate (deduped), scalars replace."""
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(value, dict) and isinstance(existing, dict):
            merged[key] = _deep_merge(existing, value)
        elif isinstance(value, list) and isinstance(existing, list):
            merged[key] = list(dict.fromkeys(existing + value))
        else:
            merged[key] = value
    return merged


def load_config(paths: Optional[Any]) -> Config:
    """Load and strictly validate one or more config files, later files overriding earlier ones.

    A single generic, RDF-agnostic file (only `rdf` / `defaults` / `lint`) can be reused across
    every ontology; an optional second, per-ontology file layers `ontology` / `fabric` / `entities`
    / `relationships` / `overrides` / `exclude` on top. A missing/None argument yields defaults.
    """
    if paths is None:
        return Config()
    path_list = [paths] if isinstance(paths, (str, Path)) else list(paths)
    if not path_list:
        return Config()

    merged: dict = {}
    for entry in path_list:
        path = Path(entry)
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ConfigError(f"config root must be a mapping: {path}")
        merged = _deep_merge(merged, raw)

    known_sections = set(_SECTION_KEYS) | _FREEFORM_SECTIONS
    _reject_unknown("<root>", merged, known_sections)

    cfg = Config(path=Path(path_list[-1]))
    raw = merged

    ontology = _require_mapping(raw.get("ontology"), "ontology")
    _reject_unknown("ontology", ontology, _SECTION_KEYS["ontology"])
    cfg.display_name = ontology.get("displayName")
    cfg.logical_id = ontology.get("logicalId")

    rdf = _require_mapping(raw.get("rdf"), "rdf")
    _reject_unknown("rdf", rdf, _SECTION_KEYS["rdf"])
    cfg.rdf = {**_RDF_DEFAULTS, **{k: v for k, v in rdf.items() if v is not None}}

    fabric = _require_mapping(raw.get("fabric"), "fabric")
    _reject_unknown("fabric", fabric, _SECTION_KEYS["fabric"])
    cfg.workspace_id = fabric.get("workspaceId") or PLACEHOLDER_GUID
    for name, entry in _require_mapping(fabric.get("lakehouses"), "fabric.lakehouses").items():
        entry = _require_mapping(entry, f"fabric.lakehouses.{name}")
        _reject_unknown(f"fabric.lakehouses.{name}", entry, _LAKEHOUSE_KEYS)
        cfg.lakehouses[name] = LakehouseRef(
            name=name,
            item_id=entry.get("itemId") or PLACEHOLDER_GUID,
            workspace_id=entry.get("workspaceId"),
            default_schema=entry.get("defaultSchema") or "dbo",
        )
    cfg.environments = _require_mapping(fabric.get("environments"), "fabric.environments")

    defaults = _require_mapping(raw.get("defaults"), "defaults")
    _reject_unknown("defaults", defaults, _SECTION_KEYS["defaults"])
    cfg.defaults = {**_DEFAULTS, **defaults}

    lint = _require_mapping(raw.get("lint"), "lint")
    _reject_unknown("lint", lint, _SECTION_KEYS["lint"])
    cfg.lint_fail_on = (lint.get("failOn") or "error").lower()
    if cfg.lint_fail_on not in ("error", "warning"):
        raise ConfigError("lint.failOn must be 'error' or 'warning'")
    cfg.lint_ignore = list(lint.get("ignore") or [])

    entities = _require_mapping(raw.get("entities"), "entities")
    for name, entry in entities.items():
        entry = _require_mapping(entry, f"entities.{name}")
        _reject_unknown(f"entities.{name}", entry, _ENTITY_KEYS)
        for index, ts in enumerate(entry.get("timeseries") or []):
            ts = _require_mapping(ts, f"entities.{name}.timeseries[{index}]")
            _reject_unknown(f"entities.{name}.timeseries[{index}]", ts, _TIMESERIES_KEYS)
            for required in ("table", "timestampColumn"):
                if not ts.get(required):
                    raise ConfigError(f"entities.{name}.timeseries[{index}] is missing '{required}'")
        cfg.entities[name] = entry

    relationships = _require_mapping(raw.get("relationships"), "relationships")
    for name, entry in relationships.items():
        entry = _require_mapping(entry, f"relationships.{name}")
        _reject_unknown(f"relationships.{name}", entry, _RELATIONSHIP_KEYS)
        cfg.relationships[name] = entry

    overrides = _require_mapping(raw.get("overrides"), "overrides")
    _reject_unknown("overrides", overrides, _SECTION_KEYS["overrides"])
    cfg.value_type_overrides = dict(_require_mapping(overrides.get("valueTypes"), "overrides.valueTypes"))
    cfg.renames = dict(_require_mapping(overrides.get("rename"), "overrides.rename"))

    exclude = _require_mapping(raw.get("exclude"), "exclude")
    _reject_unknown("exclude", exclude, _SECTION_KEYS["exclude"])
    cfg.excluded_classes = set(exclude.get("classes") or [])
    cfg.excluded_properties = set(exclude.get("properties") or [])

    return cfg
