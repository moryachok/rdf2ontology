"""Fabric constraint validation, on the IR and on an emitted definition folder."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Optional

from .config import Config
from .diagnostics import DiagnosticBag
from .ids import IdMap
from .ir import KEY_VALUE_TYPES, VALUE_TYPES, OntologyIR

NAME_REGEX = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,127}$")
UNSAFE_COLUMN_CHARS = set(" ,;{}()=\n\t")
PLACEHOLDER_GUID = "00000000-0000-0000-0000-000000000000"


def _is_guid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def validate_ontology(
    ontology: OntologyIR,
    id_map: IdMap,
    config: Config,
    bag: Optional[DiagnosticBag] = None,
) -> DiagnosticBag:
    bag = bag if bag is not None else DiagnosticBag()
    max_length = int(config.flag("maxPortalNameLength") or 26)

    _check_names(ontology, bag, max_length)
    _check_value_types(ontology, bag)
    _check_keys(ontology, bag)
    _check_bindings(ontology, config, bag)
    _check_relationships(ontology, bag)
    _check_ids(id_map, bag)
    _check_config_references(ontology, config, bag)
    return bag


def _check_names(ontology: OntologyIR, bag: DiagnosticBag, max_length: int) -> None:
    for entity in ontology.entities.values():
        _check_name(entity.name, "entity type", bag, max_length)
        for prop in entity.properties.values():
            _check_name(prop.name, "property", bag, max_length, subject=f"{entity.name}.{prop.name}")
    for relationship in ontology.relationships:
        _check_name(relationship.name, "relationship type", bag, max_length)


def _check_name(name: str, kind: str, bag: DiagnosticBag, max_length: int, subject: Optional[str] = None) -> None:
    subject = subject or name
    if not NAME_REGEX.match(name):
        bag.error("E1", f"{kind} name does not match the Fabric identifier regex", subject)
    elif len(name) > max_length:
        bag.warning("W1", f"{kind} name is {len(name)} characters; the portal limit is {max_length}", subject)


def _check_value_types(ontology: OntologyIR, bag: DiagnosticBag) -> None:
    seen: dict[str, tuple[str, str]] = {}
    for entity in ontology.entities.values():
        for prop in entity.properties.values():
            if prop.value_type not in VALUE_TYPES:
                bag.error("E2", f"invalid valueType '{prop.value_type}'; allowed: {', '.join(VALUE_TYPES)}", f"{entity.name}.{prop.name}")
            previous = seen.get(prop.name)
            if previous and previous[1] != prop.value_type:
                bag.error(
                    "E4",
                    f"property name is used with conflicting valueTypes: {previous[0]} uses {previous[1]}, "
                    f"{entity.name} uses {prop.value_type}",
                    prop.name,
                )
            else:
                seen.setdefault(prop.name, (entity.name, prop.value_type))

        static_names = {p.name for p in entity.static_properties}
        timeseries_names = {p.name for p in entity.timeseries_properties}
        for clash in sorted(static_names & timeseries_names):
            bag.error("E5", "property name appears in both properties[] and timeseriesProperties[]", f"{entity.name}.{clash}")


def _check_keys(ontology: OntologyIR, bag: DiagnosticBag) -> None:
    for entity in ontology.entities.values():
        if entity.bound and not entity.key:
            bag.error("E3", "bound entity type must declare entityIdParts", entity.name)
        for part in entity.key:
            prop = entity.properties.get(part)
            if prop is None:
                bag.error("E3", f"key property '{part}' does not exist on this entity type", entity.name)
                continue
            if prop.value_type not in KEY_VALUE_TYPES:
                bag.error(
                    "E3",
                    f"key property '{part}' has valueType {prop.value_type}; keys must be {' or '.join(KEY_VALUE_TYPES)}",
                    entity.name,
                )
            if prop.timeseries:
                bag.error("E3", f"key property '{part}' cannot be a timeseries property", entity.name)
        if entity.display_name_property and entity.display_name_property not in entity.properties:
            bag.error("E11", f"displayNameProperty '{entity.display_name_property}' does not exist", entity.name)


def _check_bindings(ontology: OntologyIR, config: Config, bag: DiagnosticBag) -> None:
    for entity in ontology.entities.values():
        if entity.timeseries and not entity.bound:
            bag.error("E8", "a TimeSeries binding requires an existing NonTimeSeries binding", entity.name)
        for block in entity.timeseries:
            if not block.timestamp_column:
                bag.error("E9", "TimeSeries binding is missing timestampColumnName", entity.name)
                continue
            matches = [
                p
                for p in entity.properties.values()
                if (p.source_column or p.name) == block.timestamp_column
            ]
            if not matches:
                bag.error("E9", f"timestamp column '{block.timestamp_column}' is not bound to any property", entity.name)
            elif all(p.value_type != "DateTime" for p in matches):
                bag.error("E9", f"timestamp column '{block.timestamp_column}' must map to a DateTime property", entity.name)
        if not entity.bound:
            continue
        ref = config.lakehouse(entity.lakehouse)
        if ref.item_id == PLACEHOLDER_GUID:
            bag.warning(
                "W7",
                "lakehouse id is unresolved (placeholder GUID); pass --lakehouse-id, set fabric.lakehouses, "
                "or replace via parameter.yml before deploying",
                entity.name,
            )
        for prop in entity.properties.values():
            column = prop.source_column or prop.name
            if set(column) & UNSAFE_COLUMN_CHARS:
                bag.warning("W8", f"source column '{column}' enables delta column mapping, which breaks bindings", f"{entity.name}.{prop.name}")
    if config.workspace_id == PLACEHOLDER_GUID and ontology.bound_entities():
        bag.warning("W7", "fabric.workspaceId is still the placeholder GUID; parameter.yml replacement is required", ontology.name)


def _check_relationships(ontology: OntologyIR, bag: DiagnosticBag) -> None:
    seen: set[str] = set()
    for relationship in ontology.relationships:
        if relationship.name in seen:
            bag.error("E7", "relationship type name is not unique within the ontology", relationship.name)
        seen.add(relationship.name)
        if relationship.source_entity == relationship.target_entity:
            bag.error("E6", "source and target entity types must differ", relationship.name)
        for role, name in (("source", relationship.source_entity), ("target", relationship.target_entity)):
            if name not in ontology.entities:
                bag.error("E6", f"{role} entity type '{name}' is not part of the ontology", relationship.name)
        ctx = relationship.contextualization
        if ctx is None:
            continue
        source = ontology.entities.get(relationship.source_entity)
        target = ontology.entities.get(relationship.target_entity)
        if source and not set(ctx.source_properties) <= set(source.key):
            bag.error("E12", "sourceKeyRefBindings must target the source entity's entityIdParts", relationship.name)
        if target and not set(ctx.target_properties) <= set(target.key):
            bag.error("E12", "targetKeyRefBindings must target the target entity's entityIdParts", relationship.name)


def _check_ids(id_map: IdMap, bag: DiagnosticBag) -> None:
    seen: dict[str, int] = {}
    for value in id_map.all_ids():
        seen[value] = seen.get(value, 0) + 1
    for value, count in sorted(seen.items()):
        if count > 1:
            bag.error("E10", f"id '{value}' is used {count} times; ids must be unique across the ontology", value)
    for record in id_map.entity_types.values():
        _check_numeric_id(record["id"], bag)
        for prop in record.get("properties", {}).values():
            _check_numeric_id(prop["id"], bag)
        for guid in record.get("bindings", {}).values():
            if not _is_guid(guid):
                bag.error("E10", "data binding id must be a GUID", str(guid))
    for record in id_map.relationship_types.values():
        _check_numeric_id(record["id"], bag)
        for guid in record.get("contextualizations", {}).values():
            if not _is_guid(guid):
                bag.error("E10", "contextualization id must be a GUID", str(guid))


def _check_numeric_id(value: str, bag: DiagnosticBag) -> None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        bag.error("E10", "id must be a positive 64-bit integer", str(value))
        return
    if number <= 0 or number >= 2**63:
        bag.error("E10", "id must be a positive 64-bit integer", str(value))


def _check_config_references(ontology: OntologyIR, config: Config, bag: DiagnosticBag) -> None:
    known_relationships = {r.name for r in ontology.relationships}
    for name in config.entities:
        if name in ontology.entities or config.rename(name) in ontology.entities:
            continue
        if config.is_class_excluded(name) or not config.flag("emitUnboundEntities"):
            continue
        bag.error("E15", "config references an entity type that is not in the ontology", name)
    for name, entry in config.relationships.items():
        target = entry.get("name") or config.rename(name)
        if entry.get("exclude"):
            continue
        if target in known_relationships or any(r.name.startswith(target) for r in ontology.relationships):
            continue
        bag.error("E15", "config references a relationship that is not in the ontology", name)


# --------------------------------------------------------------------------
# Structural validation of an emitted (or hand-edited) definition folder
# --------------------------------------------------------------------------
def validate_item_dir(item_dir: Path, bag: Optional[DiagnosticBag] = None) -> DiagnosticBag:
    bag = bag if bag is not None else DiagnosticBag()
    item_dir = Path(item_dir)

    platform = item_dir / ".platform"
    if not platform.is_file():
        bag.error("E11", "missing .platform", str(item_dir))
    else:
        data = json.loads(platform.read_text(encoding="utf-8"))
        if data.get("metadata", {}).get("type") != "Ontology":
            bag.error("E11", ".platform metadata.type must be 'Ontology'", str(platform))

    definition = item_dir / "definition.json"
    if not definition.is_file():
        bag.error("E11", "missing definition.json", str(item_dir))
    elif json.loads(definition.read_text(encoding="utf-8")) != {}:
        bag.error("E11", "definition.json must be exactly {}", str(definition))

    entity_ids: dict[str, dict] = {}
    property_owner: dict[str, str] = {}
    all_ids: list[str] = []

    for entity_file in sorted((item_dir / "EntityTypes").glob("*/definition.json")):
        entity = json.loads(entity_file.read_text(encoding="utf-8"))
        entity_ids[entity["id"]] = entity
        all_ids.append(entity["id"])
        properties = list(entity.get("properties") or []) + list(entity.get("timeseriesProperties") or [])
        names: dict[str, str] = {}
        for prop in properties:
            all_ids.append(prop["id"])
            property_owner[prop["id"]] = entity["id"]
            if prop["valueType"] not in VALUE_TYPES:
                bag.error("E2", f"invalid valueType '{prop['valueType']}'", f"{entity['name']}.{prop['name']}")
            if prop["name"] in names:
                bag.error("E5", "duplicate property name within the entity type", f"{entity['name']}.{prop['name']}")
            names[prop["name"]] = prop["id"]
        if entity_file.parent.name != entity["id"]:
            bag.error("E11", f"folder name '{entity_file.parent.name}' does not match the entity id", entity["name"])

        bindings = sorted((entity_file.parent / "DataBindings").glob("*.json"))
        static = 0
        for binding_file in bindings:
            binding = json.loads(binding_file.read_text(encoding="utf-8"))
            all_ids.append(binding["id"])
            configuration = binding["dataBindingConfiguration"]
            source = configuration["sourceTableProperties"]
            if configuration["dataBindingType"] == "NonTimeSeries":
                static += 1
                if source["sourceType"] != "LakehouseTable":
                    bag.error("E13", "NonTimeSeries bindings must use a LakehouseTable source", entity["name"])
            elif not configuration.get("timestampColumnName"):
                bag.error("E9", "TimeSeries binding is missing timestampColumnName", entity["name"])
            for item in configuration.get("propertyBindings") or []:
                if item["targetPropertyId"] not in property_owner:
                    bag.error("E11", f"binding references unknown property id {item['targetPropertyId']}", entity["name"])
        if static > 1:
            bag.error("E8", f"{static} NonTimeSeries bindings; at most one is allowed", entity["name"])
        if len(bindings) > static and static == 0:
            bag.error("E8", "TimeSeries binding without a NonTimeSeries binding", entity["name"])

    relationship_names: set[str] = set()
    for rel_file in sorted((item_dir / "RelationshipTypes").glob("*/definition.json")):
        relationship = json.loads(rel_file.read_text(encoding="utf-8"))
        all_ids.append(relationship["id"])
        if relationship["name"] in relationship_names:
            bag.error("E7", "relationship type name is not unique within the ontology", relationship["name"])
        relationship_names.add(relationship["name"])
        source = relationship["source"]["entityTypeId"]
        target = relationship["target"]["entityTypeId"]
        if source == target:
            bag.error("E6", "source and target entity types must differ", relationship["name"])
        for role, value in (("source", source), ("target", target)):
            if value not in entity_ids:
                bag.error("E6", f"{role} entity type id {value} is not present in the tree", relationship["name"])
        for ctx_file in sorted((rel_file.parent / "Contextualizations").glob("*.json")):
            ctx = json.loads(ctx_file.read_text(encoding="utf-8"))
            all_ids.append(ctx["id"])
            for role, bindings in (("source", ctx["sourceKeyRefBindings"]), ("target", ctx["targetKeyRefBindings"])):
                entity = entity_ids.get(source if role == "source" else target, {})
                key_parts = set(entity.get("entityIdParts") or [])
                for item in bindings:
                    if item["targetPropertyId"] not in key_parts:
                        bag.error(
                            "E12",
                            f"{role}KeyRefBindings must reference the {role} entity's entityIdParts",
                            relationship["name"],
                        )

    duplicates = {value for value in all_ids if all_ids.count(value) > 1}
    for value in sorted(duplicates):
        bag.error("E10", "id is used more than once in the definition tree", value)
    return bag
