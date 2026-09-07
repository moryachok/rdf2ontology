"""Deterministic, reproducible ID derivation plus the persisted name -> id map."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .ir import OntologyIR

GUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://example.org/rdf2ontology")
_ID_FLOOR = 10**17
_ID_SPAN = 4 * 10**17


class IdMapError(Exception):
    """Raised when a new ID would have to be minted but minting is disabled."""


def derive_id(ontology: str, kind: str, qualified_name: str, salt: int = 0) -> str:
    """Positive 18-digit 64-bit integer, stable for a given (ontology, kind, name)."""
    key = f"{ontology}|{kind}|{qualified_name}"
    if salt:
        key = f"{key}#{salt}"
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
    return str(_ID_FLOOR + (int.from_bytes(digest, "big") % _ID_SPAN))


def derive_guid(ontology: str, kind: str, qualified_name: str) -> str:
    return str(uuid.uuid5(GUID_NAMESPACE, f"{ontology}|{kind}|{qualified_name}"))


@dataclass
class IdMap:
    ontology_name: str
    path: Optional[Path] = None
    logical_id: Optional[str] = None
    source_rdf: Optional[str] = None
    entity_types: dict[str, dict[str, Any]] = field(default_factory=dict)
    relationship_types: dict[str, dict[str, Any]] = field(default_factory=dict)
    renames: dict[str, str] = field(default_factory=dict)
    allow_new: bool = True
    _used: set[str] = field(default_factory=set, repr=False)
    minted: list[str] = field(default_factory=list, repr=False)

    # -- persistence -------------------------------------------------------
    @classmethod
    def load(cls, path: Optional[Path], ontology_name: str, allow_new: bool = True) -> "IdMap":
        if path is None or not Path(path).is_file():
            return cls(ontology_name=ontology_name, path=Path(path) if path else None, allow_new=True)
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            ontology_name=raw.get("ontologyName") or ontology_name,
            path=Path(path),
            logical_id=raw.get("logicalId"),
            source_rdf=raw.get("sourceRdf"),
            entity_types=raw.get("entityTypes") or {},
            relationship_types=raw.get("relationshipTypes") or {},
            renames=raw.get("renames") or {},
            allow_new=allow_new,
        )

    def save(self, path: Optional[Path] = None) -> Path:
        target = Path(path or self.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ontologyName": self.ontology_name,
            "generatedBy": "rdf2ontology 0.1.0",
            "sourceRdf": self.source_rdf,
            "logicalId": self.logical_id,
            "entityTypes": self.entity_types,
            "relationshipTypes": self.relationship_types,
            "renames": self.renames,
        }
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return target

    # -- allocation --------------------------------------------------------
    def _allocate(self, kind: str, qualified_name: str, existing: Optional[str], guid: bool = False) -> str:
        if existing:
            self._used.add(existing)
            return existing
        if not self.allow_new:
            raise IdMapError(
                f"'{qualified_name}' ({kind}) is not in the id map and --allow-new-ids was not passed"
            )
        if guid:
            value = derive_guid(self.ontology_name, kind, qualified_name)
            salt = 0
            while value in self._used:
                salt += 1
                value = derive_guid(self.ontology_name, kind, f"{qualified_name}#{salt}")
        else:
            salt = 0
            value = derive_id(self.ontology_name, kind, qualified_name)
            while value in self._used:
                salt += 1
                value = derive_id(self.ontology_name, kind, qualified_name, salt)
        self._used.add(value)
        self.minted.append(f"{kind}:{qualified_name}")
        return value

    def assign(self, ontology: OntologyIR, source_rdf: Optional[str] = None) -> None:
        """Fill in (or reuse) every ID the emitter needs.

        IDs are derived from stable RDF IRIs, never from the (renameable) display name, so
        renaming a concept - via config, sanitization, or dedup suffixing - never changes its id.
        """
        self.source_rdf = source_rdf or self.source_rdf
        self.logical_id = ontology.logical_id or self.logical_id or derive_guid(self.ontology_name, "logicalId", self.ontology_name)
        ontology.logical_id = self.logical_id
        self.renames = dict(ontology.renames)

        for name in sorted(ontology.entities):
            entity = ontology.entities[name]
            record = self.entity_types.setdefault(name, {})
            record["id"] = self._allocate("entity", entity.iri, record.get("id"))
            record["bound"] = entity.bound
            record["key"] = list(entity.key)
            record["displayNameProperty"] = entity.display_name_property
            record["sourceTable"] = f"{entity.schema}.{entity.table}" if entity.bound else None
            properties = record.setdefault("properties", {})
            for prop_name in sorted(entity.properties):
                prop = entity.properties[prop_name]
                entry = properties.setdefault(prop_name, {})
                entry["id"] = self._allocate("property", f"{entity.iri}|{prop.iri}", entry.get("id"))
                entry["sourceColumnName"] = prop.source_column
                entry["valueType"] = prop.value_type
                entry["timeseries"] = prop.timeseries
            bindings = record.setdefault("bindings", {})
            if entity.bound:
                bindings["static"] = self._allocate(
                    "binding", f"{entity.iri}|static|{entity.schema}.{entity.table}", bindings.get("static"), guid=True
                )
            else:
                bindings.pop("static", None)
            for index, block in enumerate(entity.timeseries):
                key = f"timeseries[{index}]"
                bindings[key] = self._allocate(
                    "binding", f"{entity.iri}|timeseries|{block.schema}.{block.table}", bindings.get(key), guid=True
                )

        for relationship in sorted(ontology.relationships, key=lambda r: r.name):
            record = self.relationship_types.setdefault(relationship.name, {})
            # Dangling references are a validate_ontology (E6) concern; fall back rather than crash.
            source = ontology.entities.get(relationship.source_entity)
            target = ontology.entities.get(relationship.target_entity)
            source_iri = source.iri if source else relationship.source_entity
            target_iri = target.iri if target else relationship.target_entity
            record["id"] = self._allocate(
                "relationship", f"{relationship.iri}|{source_iri}|{target_iri}", record.get("id")
            )
            record["source"] = relationship.source_entity
            record["target"] = relationship.target_entity
            contextualizations = record.setdefault("contextualizations", {})
            if relationship.contextualization is not None:
                ctx = relationship.contextualization
                key = f"{ctx.schema}.{ctx.table}"
                contextualizations[key] = self._allocate(
                    "contextualization",
                    f"{relationship.iri}|{source_iri}|{target_iri}|{key}",
                    contextualizations.get(key),
                    guid=True,
                )
            else:
                contextualizations.clear()

        self.prune(ontology)

    def prune(self, ontology: OntologyIR) -> None:
        """Drop map entries for concepts that no longer exist, so the file stays honest.

        Entities skipped by --require-physical-tables are kept in the map (not pruned), so
        the id is reused once the backing table shows up, without needing --allow-new-ids.
        """
        for name in list(self.entity_types):
            if name in ontology.skipped_entities:
                continue
            if name not in ontology.entities:
                del self.entity_types[name]
                continue
            entity = ontology.entities[name]
            properties = self.entity_types[name].get("properties", {})
            for prop_name in list(properties):
                if prop_name not in entity.properties:
                    del properties[prop_name]
        live = {r.name for r in ontology.relationships}
        for name in list(self.relationship_types):
            if name not in live:
                del self.relationship_types[name]

    # -- reads -------------------------------------------------------------
    def entity_id(self, entity: str) -> str:
        return self.entity_types[entity]["id"]

    def property_id(self, entity: str, prop: str) -> str:
        return self.entity_types[entity]["properties"][prop]["id"]

    def binding_id(self, entity: str, key: str) -> str:
        return self.entity_types[entity]["bindings"][key]

    def relationship_id(self, relationship: str) -> str:
        return self.relationship_types[relationship]["id"]

    def contextualization_id(self, relationship: str, table_key: str) -> str:
        return self.relationship_types[relationship]["contextualizations"][table_key]

    def all_ids(self) -> list[str]:
        out: list[str] = []
        for record in self.entity_types.values():
            out.append(record["id"])
            out.extend(p["id"] for p in record.get("properties", {}).values())
            out.extend(record.get("bindings", {}).values())
        for record in self.relationship_types.values():
            out.append(record["id"])
            out.extend(record.get("contextualizations", {}).values())
        return out
