"""RDF -> IR: value types, key resolution, entity and relationship derivation."""

from __future__ import annotations

import re
from typing import Optional

from rdflib.namespace import RDF, XSD

from .config import Config
from .diagnostics import DiagnosticBag
from .ir import (
    ContextualizationIR,
    EntityIR,
    OntologyIR,
    PropertyIR,
    RelationshipIR,
    TimeSeriesBindingIR,
)
from .rdf_model import GraphModel, PropertyRecord, local_name

_XSD = str(XSD)
_ILLEGAL_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_-]")
_WORD = re.compile(r"[A-Za-z0-9]+")

# Source-system vocabulary (e.g. Amdocs Telco) carries the physical column name here.
PHYSICAL_NAME_ANNOTATION = "physicalDataPropertyName"

VALUE_TYPE_BY_RANGE: dict[str, str] = {
    **{f"{_XSD}{name}": "String" for name in ("string", "anyURI", "token", "normalizedString", "NCName", "Name", "language")},
    str(RDF.langString): "String",
    f"{_XSD}boolean": "Boolean",
    **{f"{_XSD}{name}": "DateTime" for name in ("dateTime", "date", "dateTimeStamp")},
    **{
        f"{_XSD}{name}": "BigInt"
        for name in (
            "integer",
            "int",
            "long",
            "short",
            "byte",
            "nonNegativeInteger",
            "positiveInteger",
            "negativeInteger",
            "nonPositiveInteger",
            "unsignedInt",
            "unsignedLong",
            "unsignedShort",
            "unsignedByte",
        )
    },
    **{f"{_XSD}{name}": "Double" for name in ("double", "float", "decimal")},
}


def value_type_for_range(range_iri: Optional[str]) -> Optional[str]:
    """Return the Fabric valueType for an xsd range, or None when unmapped."""
    if not range_iri:
        return None
    return VALUE_TYPE_BY_RANGE.get(range_iri)


def split_table(reference: str) -> tuple[Optional[str], str]:
    """'dbo.customer' -> ('dbo', 'customer'); 'customer' -> (None, 'customer')."""
    if "." in reference:
        schema, table = reference.split(".", 1)
        return schema.strip() or None, table.strip()
    return None, reference.strip()


def sanitize_name(name: str) -> str:
    """Deterministically coerce a local name into the Fabric identifier regex."""
    cleaned = _ILLEGAL_NAME_CHARS.sub("", name) or "X"
    if not cleaned[0].isalpha():
        cleaned = f"X{cleaned}"
    return cleaned[:128]


def title_to_name(title: str) -> str:
    """'Customer & Address Ontology' -> 'CustomerAddressOntology'."""
    words = _WORD.findall(title)
    return "".join(w[:1].upper() + w[1:] for w in words) or "Ontology"


def _collect_custom_attributes(
    keys: list[str],
    annotations: dict[str, str],
    *,
    label: Optional[str] = None,
    domain: Optional[str] = None,
    range_: Optional[str] = None,
    comment: Optional[str] = None,
) -> dict[str, str]:
    """Reserved names resolve to RDF built-ins; everything else looks up a raw annotation."""
    reserved = {"label": label, "domain": domain, "range": range_, "comment": comment}
    result: dict[str, str] = {}
    for key in keys:
        value = reserved[key] if key in reserved else annotations.get(key)
        if value:
            result[key] = value
    return result


def _finalize_name(
    candidate: str,
    taken: set[str],
    diagnostics: DiagnosticBag,
    raw: str,
    kind: str,
    ontology: OntologyIR,
) -> str:
    """Sanitize + deduplicate a name, recording the substitution instead of blocking on it."""
    sanitized = sanitize_name(candidate)
    if sanitized != candidate:
        diagnostics.warning(
            "L-NAME-REGEX", f"'{candidate}' is not a legal Fabric identifier; renamed to '{sanitized}'", raw
        )
    final = sanitized
    suffix = 2
    while final in taken:
        final = f"{sanitized}{suffix}"
        suffix += 1
    if final != sanitized:
        diagnostics.warning("L-NAME-DUP", f"name '{sanitized}' is already used; renamed to '{final}'", raw)
    taken.add(final)
    if final != raw:
        ontology.renames[f"{kind}:{raw}"] = final
    return final


def _derived_property_name(prop: PropertyRecord, entity_name: str, diagnostics: DiagnosticBag) -> str:
    """Prefer the source system's physical column name over the RDF local name."""
    physical = (prop.raw_annotations.get(PHYSICAL_NAME_ANNOTATION) or "").strip()
    if physical:
        return physical
    diagnostics.warning(
        "W11",
        f"no {PHYSICAL_NAME_ANNOTATION} annotation; falling back to the RDF local name",
        f"{entity_name}.{prop.name}",
    )
    return prop.name


def _resolve_value_type(
    prop: PropertyRecord,
    entity_name: str,
    name: str,
    config: Config,
    unmapped_default: str,
    diagnostics: Optional[DiagnosticBag] = None,
) -> str:
    override = config.value_type_override(entity_name, prop.name) or config.value_type_override(entity_name, name)
    if override:
        return override
    value_type = value_type_for_range(prop.ranges[0] if prop.ranges else None)
    if value_type is None:
        value_type = unmapped_default
        if diagnostics is not None:
            diagnostics.warning(
                "W2",
                f"range {local_name(prop.ranges[0]) if prop.ranges else 'missing'} is unmapped; defaulted to {unmapped_default}",
                f"{entity_name}.{name}",
            )
    return value_type


def _conflicting_derived_names(model: GraphModel, config: Config, unmapped_default: str) -> set[str]:
    """Derived names that would land on two entity types with different valueTypes; Fabric rejects those."""
    emit_unbound = bool(config.flag("emitUnboundEntities"))
    emit_fk = bool(config.flag("emitForeignKeyProperties"))
    quiet = DiagnosticBag()
    types_by_name: dict[str, set[str]] = {}
    for class_iri in sorted(model.classes):
        record = model.classes[class_iri]
        if config.is_class_excluded(record.name):
            continue
        _, _, table = _entity_source(model, config, class_iri)
        if table is None and not emit_unbound:
            continue
        entity_name = sanitize_name(config.rename(record.name))
        for prop in model.properties_of(class_iri, "data"):
            if config.is_property_excluded(entity_name, prop.name):
                continue
            derived = _derived_property_name(prop, entity_name, quiet)
            value_type = _resolve_value_type(prop, entity_name, derived, config, unmapped_default)
            types_by_name.setdefault(derived, set()).add(value_type)
        if emit_fk:
            for prop in model.properties_of(class_iri, "object"):
                column = prop.annotations.get("column")
                if not column:
                    continue
                candidate = sanitize_name(config.rename(column))
                if config.is_property_excluded(entity_name, candidate):
                    continue
                value_type = config.value_type_override(entity_name, candidate) or "String"
                types_by_name.setdefault(candidate, set()).add(value_type)
    return {name for name, types in types_by_name.items() if len(types) > 1}


def resolve_key(model: GraphModel, config: Config, class_iri: str) -> tuple[list[str], str, list[str]]:
    """Key resolution precedence (plan section 5.5). Returns (key, rule, candidates)."""
    class_name = model.class_name(class_iri)
    data_properties = model.properties_of(class_iri, "data")
    candidates = sorted(p.name for p in data_properties)

    configured = config.entity(class_name).get("key")
    if configured:
        return list(configured), "config", candidates

    key_value = str(config.rdf.get("keyValue", "true")).lower()
    annotated = [p.name for p in data_properties if str(p.annotations.get("key", "")).lower() == key_value]
    if annotated:
        return sorted(annotated), "annotation", candidates

    by_iri = {p.iri: p for p in data_properties}
    restricted = [
        by_iri[r.on_property].name
        for r in model.classes[class_iri].restrictions
        if r.kind == "cardinality" and r.value == 1 and r.on_property in by_iri
    ]
    if restricted:
        return sorted(set(restricted)), "restriction", candidates

    for suffix in ("Key", "Id"):
        match = [p.name for p in data_properties if p.name == f"{class_name}{suffix}"]
        if match:
            return match, "naming-convention", candidates

    return [], "none", candidates


def resolve_display_name(entity: EntityIR, config: Config, raw_name: Optional[str] = None) -> Optional[str]:
    configured = config.entity(raw_name or entity.name).get("displayNameProperty") or config.entity(entity.name).get(
        "displayNameProperty"
    )
    if configured:
        return configured
    strings = [p.name for p in entity.properties.values() if p.value_type == "String" and not p.timeseries]
    for suffix in ("DisplayText", "LegalName", "Name"):
        for name in sorted(strings):
            if name.endswith(suffix) and name not in entity.key:
                return name
    return None


def _entity_source(model: GraphModel, config: Config, class_iri: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (lakehouse, schema, table) for a class, config taking precedence."""
    record = model.classes[class_iri]
    entry = config.entity(record.name)
    table_ref = entry.get("sourceTable") or record.annotations.get("table")
    lakehouse = entry.get("sourceLakehouse") or record.annotations.get("lakehouse")
    if not table_ref:
        return lakehouse, None, None
    schema, table = split_table(table_ref)
    if not schema:
        schema = config.lakehouse(lakehouse).default_schema
    return lakehouse, schema, table


def build_ontology(model: GraphModel, config: Config, name: str, diagnostics: DiagnosticBag) -> OntologyIR:
    ontology = OntologyIR(name=name, logical_id=config.logical_id)
    emit_unbound = bool(config.flag("emitUnboundEntities"))
    unmapped_default = config.flag("unmappedRangeValueType") or "String"
    conflicting_names = _conflicting_derived_names(model, config, unmapped_default)

    class_by_iri: dict[str, str] = {}
    taken_entity_names: set[str] = set()
    for class_iri in sorted(model.classes):
        record = model.classes[class_iri]
        raw_name = record.name
        if config.is_class_excluded(raw_name):
            diagnostics.warning("W3b", "class excluded by configuration", raw_name)
            continue

        lakehouse, schema, table = _entity_source(model, config, class_iri)
        if table is None and not emit_unbound:
            diagnostics.warning("W3b", "class has no source table and emitUnboundEntities is false", raw_name)
            continue

        entity_name = _finalize_name(config.rename(raw_name), taken_entity_names, diagnostics, raw_name, "entity", ontology)
        entity = EntityIR(
            name=entity_name,
            iri=class_iri,
            description=record.comment,
            lakehouse=lakehouse,
            schema=schema,
            table=table,
            synonyms=record.synonyms,
            custom_attributes=_collect_custom_attributes(
                config.custom_attribute_keys("entities"), record.raw_annotations, label=record.label
            ),
        )
        taken_prop_names: set[str] = set()
        raw_to_final_prop: dict[str, str] = {}
        derived_names = {
            prop.iri: _derived_property_name(prop, entity_name, diagnostics)
            for prop in model.properties_of(class_iri, "data")
            if not config.is_property_excluded(entity_name, prop.name)
        }
        for prop_iri in sorted(derived_names, key=lambda iri: (derived_names[iri], iri)):
            prop = model.properties[prop_iri]
            derived = derived_names[prop_iri]
            candidate = prop.name if derived in conflicting_names else derived
            if derived in conflicting_names:
                diagnostics.warning(
                    "W12",
                    f"'{derived}' has conflicting valueTypes across entity types; falling back to the RDF local name",
                    f"{entity_name}.{prop.name}",
                )
            prop_name = _finalize_name(
                config.rename(candidate), taken_prop_names, diagnostics, f"{entity_name}.{prop.name}", "property", ontology
            )
            entity.properties[prop_name] = _build_property(
                prop, prop_name, entity_name, config, diagnostics, unmapped_default
            )
            raw_to_final_prop[prop.name] = prop_name
            raw_to_final_prop.setdefault(derived, prop_name)
        if config.flag("emitForeignKeyProperties"):
            _add_foreign_key_properties(
                model, config, entity, class_iri, taken_prop_names, diagnostics, ontology, conflicting_names
            )
        ontology.entities[entity_name] = entity
        class_by_iri[class_iri] = entity_name

        key, rule, candidates = resolve_key(model, config, class_iri)
        entity.key = [raw_to_final_prop.get(part, config.rename(part)) for part in key]
        entity.key_rule = rule
        if not entity.key:
            if entity.bound:
                diagnostics.error(
                    "E3",
                    f"bound entity type has no resolvable key; candidates: {', '.join(candidates) or 'none'}",
                    entity_name,
                )
            else:
                diagnostics.warning("W9", "entity type emitted keyless (entityIdParts: [])", entity_name)
        entity.display_name_property = resolve_display_name(entity, config, raw_name)

    _apply_timeseries(ontology, config, diagnostics)
    _build_relationships(model, config, ontology, class_by_iri, diagnostics)
    _report_unbound(ontology, diagnostics)
    return ontology


def _add_foreign_key_properties(
    model: GraphModel,
    config: Config,
    entity: EntityIR,
    class_iri: str,
    taken: set[str],
    diagnostics: DiagnosticBag,
    ontology: OntologyIR,
    conflicting_names: set[str],
) -> None:
    """An object property with a source column is also a real column: keep it queryable."""
    for prop in sorted(model.properties_of(class_iri, "object"), key=lambda p: p.name):
        column = prop.annotations.get("column")
        if not column:
            continue
        candidate = sanitize_name(config.rename(column))
        if config.is_property_excluded(entity.name, candidate):
            continue
        if candidate in entity.properties:
            continue  # already an explicit scalar property for this column
        raw_candidate = column
        if candidate in conflicting_names:
            diagnostics.warning(
                "W12",
                f"'{candidate}' has conflicting valueTypes across entity types; falling back to the RDF local name",
                f"{entity.name}.{prop.name}",
            )
            raw_candidate = prop.name
        name = _finalize_name(config.rename(raw_candidate), taken, diagnostics, f"{entity.name}.{column}", "property", ontology)
        value_type = config.value_type_override(entity.name, name) or "String"
        entity.properties[name] = PropertyIR(
            name=name,
            iri=prop.iri,
            value_type=value_type,
            source_column=column,
            description=prop.comment,
            synonyms=prop.synonyms,
            alt_label=prop.alt_label,
            custom_attributes=_collect_custom_attributes(
                config.custom_attribute_keys("objectProperties"),
                prop.raw_annotations,
                label=prop.label,
                domain=entity.name,
                range_=local_name(prop.ranges[0]) if prop.ranges else None,
            ),
        )
        diagnostics.info(
            "W10",
            f"foreign-key column of '{prop.name}' also emitted as a scalar property",
            f"{entity.name}.{name}",
        )


def _build_property(
    prop: PropertyRecord,
    name: str,
    entity_name: str,
    config: Config,
    diagnostics: DiagnosticBag,
    unmapped_default: str,
) -> PropertyIR:
    value_type = _resolve_value_type(prop, entity_name, name, config, unmapped_default, diagnostics)
    if value_type == "Boolean" and prop.comment and "integer" in prop.comment.lower():
        diagnostics.warning(
            "W6",
            "declared Boolean but the comment says the column stores 0/1 integers; consider overrides.valueTypes",
            f"{entity_name}.{name}",
        )
    return PropertyIR(
        name=name,
        iri=prop.iri,
        value_type=value_type,
        source_column=prop.annotations.get("column") or prop.name,
        description=prop.comment,
        synonyms=prop.synonyms,
        alt_label=prop.alt_label,
        custom_attributes=_collect_custom_attributes(
            config.custom_attribute_keys("dataProperties"), prop.raw_annotations, label=prop.label, domain=entity_name
        ),
    )


def _apply_timeseries(ontology: OntologyIR, config: Config, diagnostics: DiagnosticBag) -> None:
    for entity_name, entry in config.entities.items():
        blocks = entry.get("timeseries") or []
        if not blocks:
            continue
        entity = ontology.entities.get(entity_name)
        if entity is None:
            diagnostics.error("E15", "timeseries configured for an unknown entity type", entity_name)
            continue
        for block in blocks:
            schema, table = split_table(block["table"])
            names: list[str] = []
            for prop_name in block.get("properties") or []:
                prop = entity.properties.get(prop_name)
                if prop is None:
                    diagnostics.error("E15", f"timeseries property '{prop_name}' is not on this entity type", entity_name)
                    continue
                if prop.name in entity.key:
                    diagnostics.error("E5", f"key property '{prop_name}' cannot be a timeseries property", entity_name)
                    continue
                prop.timeseries = True
                names.append(prop.name)
            entity.timeseries.append(
                TimeSeriesBindingIR(
                    schema=schema or config.lakehouse(entity.lakehouse).default_schema,
                    table=table,
                    timestamp_column=block["timestampColumn"],
                    property_names=names,
                    lakehouse=block.get("lakehouse") or entity.lakehouse,
                )
            )


def _build_relationships(
    model: GraphModel,
    config: Config,
    ontology: OntologyIR,
    class_by_iri: dict[str, str],
    diagnostics: DiagnosticBag,
) -> None:
    emit_inverses = bool(config.flag("emitInverseRelationships"))
    emit_unbound_rels = bool(config.flag("emitUnboundRelationships"))
    object_properties = sorted(model.object_properties(), key=lambda p: p.name)
    known_iris = {p.iri for p in object_properties}
    taken_relationship_names: set[str] = set()

    for prop in object_properties:
        entry = config.relationship(prop.name)
        if entry.get("exclude"):
            diagnostics.info("W4", "relationship excluded by configuration", prop.name)
            continue
        if not emit_inverses and prop.inverse_of and prop.inverse_of in known_iris:
            diagnostics.info("L-INVERSE", f"inverse of {local_name(prop.inverse_of)}; not emitted", prop.name)
            continue

        pairs: list[tuple[str, str]] = []
        for domain_iri in prop.domains:
            for range_iri in prop.ranges:
                if domain_iri == range_iri:
                    diagnostics.warning("W4", "self-referencing relationship dropped (Fabric requires distinct ends)", prop.name)
                    continue
                source = class_by_iri.get(domain_iri)
                target = class_by_iri.get(range_iri)
                if source is None or target is None:
                    missing = model.class_name(domain_iri if source is None else range_iri)
                    diagnostics.warning("W3b", f"dropped: end '{missing}' is not an emitted entity type", prop.name)
                    continue
                pairs.append((source, target))

        for source, target in pairs:
            base_raw = entry.get("name") or prop.name
            base = config.rename(base_raw)
            candidate = base if len(pairs) == 1 else f"{base}{source}{target}"
            label = base_raw if len(pairs) == 1 else f"{base_raw}:{source}->{target}"
            name = _finalize_name(candidate, taken_relationship_names, diagnostics, label, "relationship", ontology)
            custom_attributes = _collect_custom_attributes(
                config.custom_attribute_keys("objectProperties"),
                prop.raw_annotations,
                label=prop.label,
                domain=source,
                range_=target,
            )
            if prop.alt_label:
                custom_attributes["altLabel"] = prop.alt_label
            relationship = RelationshipIR(
                name=name,
                iri=prop.iri,
                source_entity=source,
                target_entity=target,
                description=prop.comment,
                custom_attributes=custom_attributes,
            )
            relationship.contextualization = _build_contextualization(
                relationship, prop, entry, config, ontology, diagnostics
            )
            if relationship.contextualization is None and not emit_unbound_rels:
                diagnostics.warning("W5", "relationship dropped: no contextualization and emitUnboundRelationships is false", name)
                continue
            ontology.relationships.append(relationship)


def _build_contextualization(
    relationship: RelationshipIR,
    prop: PropertyRecord,
    entry: dict,
    config: Config,
    ontology: OntologyIR,
    diagnostics: DiagnosticBag,
) -> Optional[ContextualizationIR]:
    source = ontology.entities[relationship.source_entity]
    target = ontology.entities[relationship.target_entity]

    if not source.key or not target.key:
        diagnostics.warning("W5", "no contextualization: source or target has no key", relationship.name)
        return None

    link_ref = entry.get("linkTable")
    if link_ref:
        schema, table = split_table(link_ref)
        lakehouse = source.lakehouse
    elif source.bound:
        schema, table, lakehouse = source.schema, source.table, source.lakehouse
    else:
        diagnostics.warning("W5", "no contextualization: link table is unknown (source entity is unbound)", relationship.name)
        return None
    if not schema:
        schema = config.lakehouse(lakehouse).default_schema

    source_columns = list(entry.get("sourceKeyColumns") or source.key_columns())
    target_columns = list(entry.get("targetKeyColumns") or [])
    if not target_columns:
        foreign_key = prop.annotations.get("column")
        if not foreign_key:
            diagnostics.warning("W5", "no contextualization: object property has no source-column annotation", relationship.name)
            return None
        target_columns = [foreign_key]

    if len(source_columns) != len(source.key) or len(target_columns) != len(target.key):
        diagnostics.error(
            "E12",
            f"key column count mismatch (source {len(source_columns)}/{len(source.key)}, target {len(target_columns)}/{len(target.key)})",
            relationship.name,
        )
        return None

    return ContextualizationIR(
        lakehouse=lakehouse,
        schema=schema,
        table=table,
        source_columns=source_columns,
        source_properties=list(source.key),
        target_columns=target_columns,
        target_properties=list(target.key),
    )


def _report_unbound(ontology: OntologyIR, diagnostics: DiagnosticBag) -> None:
    for entity in ontology.unbound_entities():
        diagnostics.warning("W3", "entity type emitted without a data binding (zero instances)", entity.name)
