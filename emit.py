"""IR -> Fabric Ontology definition tree (fabric-cicd source layout)."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .config import Config
from .ids import IdMap
from .ir import EntityIR, OntologyIR, PropertyIR, RelationshipIR

SCHEMA_BASE = "https://developer.microsoft.com/json-schemas/fabric/item/ontology"
PLATFORM_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json"
ENTITY_SCHEMA = f"{SCHEMA_BASE}/entityType/1.0.0/schema.json"
BINDING_SCHEMA = f"{SCHEMA_BASE}/dataBinding/1.0.0/schema.json"
RELATIONSHIP_SCHEMA = f"{SCHEMA_BASE}/relationshipType/1.0.0/schema.json"
CONTEXTUALIZATION_SCHEMA = f"{SCHEMA_BASE}/contextualization/1.0.0/schema.json"

NAMESPACE = "usertypes"
NAMESPACE_TYPE = "Custom"


@dataclass
class EmitResult:
    item_dir: Path
    files: list[Path] = field(default_factory=list)
    parameter_file: Optional[Path] = None


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return path


def _semantic_enrichment(description: Optional[str], synonyms: list[str], extra: Optional[dict] = None) -> Optional[dict]:
    attributes = dict(extra or {})
    if synonyms:
        attributes["synonyms"] = ",".join(synonyms)
    if not description and not attributes:
        return None
    payload: dict[str, Any] = {"description": description}
    if attributes:
        payload["customAttributes"] = attributes
    return payload


def _entity_semantic_enrichment(
    description: Optional[str], synonyms: list[str], custom_attributes: Optional[dict] = None
) -> Optional[dict]:
    """Entity-level enrichment: synonyms is a JSON array, a sibling of description (not a customAttribute)."""
    if not description and not synonyms and not custom_attributes:
        return None
    payload: dict[str, Any] = {"description": description}
    if synonyms:
        payload["synonyms"] = list(synonyms)
    if custom_attributes:
        payload["customAttributes"] = dict(custom_attributes)
    return payload


def _ordered_properties(entity: EntityIR, timeseries: bool) -> list[PropertyIR]:
    pool = [p for p in entity.properties.values() if p.timeseries == timeseries]
    keys = [p for p in pool if p.name in entity.key]
    keys.sort(key=lambda p: entity.key.index(p.name))
    rest = sorted((p for p in pool if p.name not in entity.key), key=lambda p: p.name)
    return keys + rest


def _property_json(entity: EntityIR, prop: PropertyIR, id_map: IdMap) -> dict:
    payload: dict[str, Any] = {
        "id": id_map.property_id(entity.name, prop.name),
        "name": prop.name,
        "redefines": None,
        "baseTypeNamespaceType": None,
        "valueType": prop.value_type,
    }
    extra = dict(prop.custom_attributes)
    if prop.alt_label:
        extra["altLabel"] = prop.alt_label
    enrichment = _semantic_enrichment(prop.description, prop.synonyms, extra or None)
    if enrichment:
        payload["semanticEnrichment"] = enrichment
    return payload


def _entity_json(entity: EntityIR, id_map: IdMap) -> dict:
    payload: dict[str, Any] = {
        "$schema": ENTITY_SCHEMA,
        "id": id_map.entity_id(entity.name),
        "namespace": NAMESPACE,
        "baseEntityTypeId": None,
        "name": entity.name,
        "entityIdParts": [id_map.property_id(entity.name, part) for part in entity.key],
        "displayNamePropertyId": (
            id_map.property_id(entity.name, entity.display_name_property) if entity.display_name_property else None
        ),
        "namespaceType": NAMESPACE_TYPE,
        "visibility": "Visible",
        "properties": [_property_json(entity, p, id_map) for p in _ordered_properties(entity, timeseries=False)],
    }
    timeseries = _ordered_properties(entity, timeseries=True)
    if timeseries:
        payload["timeseriesProperties"] = [_property_json(entity, p, id_map) for p in timeseries]
    enrichment = _entity_semantic_enrichment(entity.description, entity.synonyms, entity.custom_attributes)
    if enrichment:
        payload["semanticEnrichment"] = enrichment
    return payload


def _source_table_properties(config: Config, lakehouse: Optional[str], schema: str, table: str) -> dict:
    ref = config.lakehouse(lakehouse)
    return {
        "sourceType": "LakehouseTable",
        "workspaceId": ref.workspace_id or config.workspace_id,
        "itemId": ref.item_id,
        "sourceTableName": table,
        "sourceSchema": schema,
    }


def _binding_json(entity: EntityIR, id_map: IdMap, config: Config) -> dict:
    bindings = [
        {"sourceColumnName": p.source_column or p.name, "targetPropertyId": id_map.property_id(entity.name, p.name)}
        for p in entity.properties.values()
        if not p.timeseries and not p.unbound_reason
    ]
    bindings.sort(key=lambda item: item["sourceColumnName"])
    return {
        "$schema": BINDING_SCHEMA,
        "id": id_map.binding_id(entity.name, "static"),
        "dataBindingConfiguration": {
            "dataBindingType": "NonTimeSeries",
            "propertyBindings": bindings,
            "sourceTableProperties": _source_table_properties(config, entity.lakehouse, entity.schema, entity.table),
        },
    }


def _timeseries_binding_json(entity: EntityIR, index: int, id_map: IdMap, config: Config) -> dict:
    block = entity.timeseries[index]
    bindings = [
        {
            "sourceColumnName": entity.properties[name].source_column or name,
            "targetPropertyId": id_map.property_id(entity.name, name),
        }
        for name in block.property_names
    ]
    bindings.sort(key=lambda item: item["sourceColumnName"])
    return {
        "$schema": BINDING_SCHEMA,
        "id": id_map.binding_id(entity.name, f"timeseries[{index}]"),
        "dataBindingConfiguration": {
            "dataBindingType": "TimeSeries",
            "timestampColumnName": block.timestamp_column,
            "propertyBindings": bindings,
            "sourceTableProperties": _source_table_properties(config, block.lakehouse, block.schema, block.table),
        },
    }


def _relationship_json(relationship: RelationshipIR, id_map: IdMap) -> dict:
    payload: dict[str, Any] = {
        "$schema": RELATIONSHIP_SCHEMA,
        "namespace": NAMESPACE,
        "id": id_map.relationship_id(relationship.name),
        "name": relationship.name,
        "namespaceType": NAMESPACE_TYPE,
        "source": {"entityTypeId": id_map.entity_id(relationship.source_entity)},
        "target": {"entityTypeId": id_map.entity_id(relationship.target_entity)},
    }
    enrichment = _semantic_enrichment(relationship.description, [], relationship.custom_attributes)
    if enrichment:
        payload["semanticEnrichment"] = enrichment
    return payload


def _contextualization_json(relationship: RelationshipIR, ontology: OntologyIR, id_map: IdMap, config: Config) -> dict:
    ctx = relationship.contextualization
    source = ontology.entities[relationship.source_entity]
    target = ontology.entities[relationship.target_entity]
    table_properties = _source_table_properties(config, ctx.lakehouse, ctx.schema, ctx.table)
    return {
        "$schema": CONTEXTUALIZATION_SCHEMA,
        "id": id_map.contextualization_id(relationship.name, f"{ctx.schema}.{ctx.table}"),
        "dataBindingTable": {
            "workspaceId": table_properties["workspaceId"],
            "itemId": table_properties["itemId"],
            "sourceTableName": table_properties["sourceTableName"],
            "sourceSchema": table_properties["sourceSchema"],
            "sourceType": "LakehouseTable",
        },
        "sourceKeyRefBindings": [
            {"sourceColumnName": column, "targetPropertyId": id_map.property_id(source.name, prop)}
            for column, prop in zip(ctx.source_columns, ctx.source_properties)
        ],
        "targetKeyRefBindings": [
            {"sourceColumnName": column, "targetPropertyId": id_map.property_id(target.name, prop)}
            for column, prop in zip(ctx.target_columns, ctx.target_properties)
        ],
    }


def _platform_json(ontology: OntologyIR) -> dict:
    return {
        "$schema": PLATFORM_SCHEMA,
        "metadata": {"type": "Ontology", "displayName": ontology.name},
        "config": {"version": "2.0", "logicalId": ontology.logical_id},
    }


def _clean_item_dir(item_dir: Path) -> None:
    """Remove a previously generated tree so omitted parts really disappear."""
    if not item_dir.exists():
        return
    if not (item_dir / ".platform").is_file():
        raise ValueError(f"refusing to overwrite '{item_dir}': it does not look like a Fabric item folder")
    shutil.rmtree(item_dir)


def emit(ontology: OntologyIR, id_map: IdMap, config: Config, output_dir: Path) -> EmitResult:
    item_dir = Path(output_dir) / f"{ontology.name}.Ontology"
    _clean_item_dir(item_dir)
    result = EmitResult(item_dir=item_dir)

    result.files.append(_write_json(item_dir / ".platform", _platform_json(ontology)))
    result.files.append(_write_json(item_dir / "definition.json", {}))

    for name in sorted(ontology.entities):
        entity = ontology.entities[name]
        entity_dir = item_dir / "EntityTypes" / id_map.entity_id(name)
        result.files.append(_write_json(entity_dir / "definition.json", _entity_json(entity, id_map)))
        if entity.bound:
            binding = _binding_json(entity, id_map, config)
            result.files.append(_write_json(entity_dir / "DataBindings" / f"{binding['id']}.json", binding))
            for index in range(len(entity.timeseries)):
                ts = _timeseries_binding_json(entity, index, id_map, config)
                result.files.append(_write_json(entity_dir / "DataBindings" / f"{ts['id']}.json", ts))

    for relationship in sorted(ontology.relationships, key=lambda r: r.name):
        rel_dir = item_dir / "RelationshipTypes" / id_map.relationship_id(relationship.name)
        result.files.append(_write_json(rel_dir / "definition.json", _relationship_json(relationship, id_map)))
        if relationship.contextualization is not None:
            ctx = _contextualization_json(relationship, ontology, id_map, config)
            result.files.append(_write_json(rel_dir / "Contextualizations" / f"{ctx['id']}.json", ctx))

    parameter_file = write_parameter_file(ontology, config, Path(output_dir))
    result.parameter_file = parameter_file
    return result


def write_parameter_file(ontology: OntologyIR, config: Config, output_dir: Path) -> Optional[Path]:
    """Emit fabric-cicd find_replace entries for the placeholder workspace / lakehouse GUIDs."""
    if not config.environments:
        return None
    entries: list[dict[str, Any]] = []
    workspace_values = {
        env: values.get("workspaceId")
        for env, values in config.environments.items()
        if isinstance(values, dict) and values.get("workspaceId")
    }
    if workspace_values:
        entries.append({"find_value": config.workspace_id, "replace_value": workspace_values})
    for lakehouse_name, ref in config.lakehouses.items():
        values = {
            env: (env_values.get("lakehouses") or {}).get(lakehouse_name)
            for env, env_values in config.environments.items()
            if isinstance(env_values, dict) and (env_values.get("lakehouses") or {}).get(lakehouse_name)
        }
        if values:
            entries.append({"find_value": ref.item_id, "replace_value": values})
    if not entries:
        return None

    import yaml

    path = Path(output_dir) / "parameter.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        yaml.safe_dump({"find_replace": entries}, handle, sort_keys=False, default_flow_style=False)
    return path
