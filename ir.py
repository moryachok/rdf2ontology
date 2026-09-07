"""Intermediate representation sitting between RDF and the Fabric definition tree."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

VALUE_TYPES = ("String", "Boolean", "DateTime", "Object", "BigInt", "Double")
KEY_VALUE_TYPES = ("String", "BigInt")


@dataclass
class PropertyIR:
    name: str
    iri: str
    value_type: str
    source_column: Optional[str] = None
    description: Optional[str] = None
    synonyms: list[str] = field(default_factory=list)
    alt_label: Optional[str] = None
    custom_attributes: dict[str, str] = field(default_factory=dict)
    timeseries: bool = False


@dataclass
class TimeSeriesBindingIR:
    schema: str
    table: str
    timestamp_column: str
    property_names: list[str]
    lakehouse: Optional[str] = None


@dataclass
class EntityIR:
    name: str
    iri: str
    description: Optional[str] = None
    lakehouse: Optional[str] = None
    schema: Optional[str] = None
    table: Optional[str] = None
    key: list[str] = field(default_factory=list)
    key_rule: str = "none"
    display_name_property: Optional[str] = None
    properties: dict[str, PropertyIR] = field(default_factory=dict)
    timeseries: list[TimeSeriesBindingIR] = field(default_factory=list)
    synonyms: list[str] = field(default_factory=list)
    custom_attributes: dict[str, str] = field(default_factory=dict)

    @property
    def bound(self) -> bool:
        return self.table is not None

    @property
    def static_properties(self) -> list[PropertyIR]:
        return [p for p in self.properties.values() if not p.timeseries]

    @property
    def timeseries_properties(self) -> list[PropertyIR]:
        return [p for p in self.properties.values() if p.timeseries]

    def key_columns(self) -> list[str]:
        return [self.properties[name].source_column or name for name in self.key]


@dataclass
class ContextualizationIR:
    lakehouse: Optional[str]
    schema: str
    table: str
    source_columns: list[str]
    source_properties: list[str]
    target_columns: list[str]
    target_properties: list[str]


@dataclass
class RelationshipIR:
    name: str
    iri: str
    source_entity: str
    target_entity: str
    description: Optional[str] = None
    custom_attributes: dict[str, str] = field(default_factory=dict)
    contextualization: Optional[ContextualizationIR] = None


@dataclass
class OntologyIR:
    name: str
    logical_id: Optional[str] = None
    entities: dict[str, EntityIR] = field(default_factory=dict)
    relationships: list[RelationshipIR] = field(default_factory=list)
    # "kind:original-rdf-local-name" -> emitted name, for every auto-resolved rename.
    renames: dict[str, str] = field(default_factory=dict)
    # raw RDF class name -> "schema.table" reason, for entities dropped by --require-physical-tables.
    skipped_entities: dict[str, str] = field(default_factory=dict)

    def bound_entities(self) -> list[EntityIR]:
        return [e for e in self.entities.values() if e.bound]

    def unbound_entities(self) -> list[EntityIR]:
        return [e for e in self.entities.values() if not e.bound]
