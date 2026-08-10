"""Compare two Fabric Ontology definition folders, optionally ignoring generated ids."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


def load_item(item_dir: Path, ignore_ids: bool = True, ignore_enrichment: bool = False) -> dict:
    """Normalise a definition folder into an id-free, comparable structure."""
    item_dir = Path(item_dir)
    property_names: dict[str, str] = {}
    entity_names: dict[str, str] = {}
    entities: dict[str, Any] = {}

    entity_files = sorted((item_dir / "EntityTypes").glob("*/definition.json"))
    for entity_file in entity_files:
        entity = json.loads(entity_file.read_text(encoding="utf-8"))
        entity_names[entity["id"]] = entity["name"]
        for prop in list(entity.get("properties") or []) + list(entity.get("timeseriesProperties") or []):
            property_names[prop["id"]] = prop["name"]

    for entity_file in entity_files:
        entity = json.loads(entity_file.read_text(encoding="utf-8"))
        record: dict[str, Any] = {
            "key": [property_names.get(part, part) for part in entity.get("entityIdParts") or []],
            "displayNameProperty": property_names.get(entity.get("displayNamePropertyId")),
            "properties": {p["name"]: p["valueType"] for p in entity.get("properties") or []},
            "timeseriesProperties": {p["name"]: p["valueType"] for p in entity.get("timeseriesProperties") or []},
            "bindings": [],
        }
        if not ignore_ids:
            record["id"] = entity["id"]
            record["propertyIds"] = {p["name"]: p["id"] for p in entity.get("properties") or []}
        if not ignore_enrichment:
            record["semanticEnrichment"] = {
                p["name"]: p.get("semanticEnrichment") for p in entity.get("properties") or []
            }

        for binding_file in sorted((entity_file.parent / "DataBindings").glob("*.json")):
            binding = json.loads(binding_file.read_text(encoding="utf-8"))
            configuration = binding["dataBindingConfiguration"]
            source = configuration["sourceTableProperties"]
            entry: dict[str, Any] = {
                "type": configuration["dataBindingType"],
                "sourceType": source.get("sourceType"),
                "table": f"{source.get('sourceSchema')}.{source.get('sourceTableName')}",
                "timestampColumn": configuration.get("timestampColumnName"),
                "columns": {
                    item["sourceColumnName"]: property_names.get(item["targetPropertyId"], item["targetPropertyId"])
                    for item in configuration.get("propertyBindings") or []
                },
            }
            if not ignore_ids:
                entry["id"] = binding["id"]
                entry["itemId"] = source.get("itemId")
                entry["workspaceId"] = source.get("workspaceId")
            record["bindings"].append(entry)
        record["bindings"].sort(key=lambda item: (item["type"], item["table"]))
        entities[entity["name"]] = record

    relationships: dict[str, Any] = {}
    for rel_file in sorted((item_dir / "RelationshipTypes").glob("*/definition.json")):
        relationship = json.loads(rel_file.read_text(encoding="utf-8"))
        record = {
            "source": entity_names.get(relationship["source"]["entityTypeId"], relationship["source"]["entityTypeId"]),
            "target": entity_names.get(relationship["target"]["entityTypeId"], relationship["target"]["entityTypeId"]),
            "contextualizations": [],
        }
        if not ignore_ids:
            record["id"] = relationship["id"]
        if not ignore_enrichment:
            record["semanticEnrichment"] = relationship.get("semanticEnrichment")
        for ctx_file in sorted((rel_file.parent / "Contextualizations").glob("*.json")):
            ctx = json.loads(ctx_file.read_text(encoding="utf-8"))
            table = ctx["dataBindingTable"]
            entry = {
                "table": f"{table.get('sourceSchema')}.{table.get('sourceTableName')}",
                "source": {
                    item["sourceColumnName"]: property_names.get(item["targetPropertyId"], item["targetPropertyId"])
                    for item in ctx["sourceKeyRefBindings"]
                },
                "target": {
                    item["sourceColumnName"]: property_names.get(item["targetPropertyId"], item["targetPropertyId"])
                    for item in ctx["targetKeyRefBindings"]
                },
            }
            if not ignore_ids:
                entry["id"] = ctx["id"]
            record["contextualizations"].append(entry)
        relationships[relationship["name"]] = record

    platform = item_dir / ".platform"
    display_name = None
    if platform.is_file():
        display_name = json.loads(platform.read_text(encoding="utf-8")).get("metadata", {}).get("displayName")

    return {"displayName": display_name, "entityTypes": entities, "relationshipTypes": relationships}


def diff_structures(left: Any, right: Any, path: str = "") -> list[str]:
    """Recursive structural diff producing '- left' / '+ right' lines."""
    lines: list[str] = []
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}" if path else str(key)
            if key not in left:
                lines.append(f"+ {child}: {_short(right[key])}")
            elif key not in right:
                lines.append(f"- {child}: {_short(left[key])}")
            else:
                lines.extend(diff_structures(left[key], right[key], child))
        return lines
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            lines.append(f"~ {path}: {len(left)} item(s) -> {len(right)} item(s)")
        for index, (a, b) in enumerate(zip(left, right)):
            lines.extend(diff_structures(a, b, f"{path}[{index}]"))
        return lines
    if left != right:
        lines.append(f"~ {path}: {_short(left)} -> {_short(right)}")
    return lines


def _short(value: Any, limit: int = 80) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[: limit - 3] + "..."


def diff_items(
    left_dir: Path,
    right_dir: Path,
    ignore_ids: bool = True,
    ignore_enrichment: bool = False,
    subset: Optional[list[str]] = None,
) -> list[str]:
    left = load_item(left_dir, ignore_ids, ignore_enrichment)
    right = load_item(right_dir, ignore_ids, ignore_enrichment)
    if subset:
        keep = set(subset)
        for side in (left, right):
            side["entityTypes"] = {k: v for k, v in side["entityTypes"].items() if k in keep}
            side["relationshipTypes"] = {
                k: v
                for k, v in side["relationshipTypes"].items()
                if v["source"] in keep and v["target"] in keep
            }
    return diff_structures(left, right)
