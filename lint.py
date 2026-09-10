"""Step 1 of the pipeline: lint the RDF graph before any mapping happens."""

from __future__ import annotations

import re
from typing import Optional

from rdflib.namespace import OWL, RDF, RDFS, XSD

from .config import Config
from .diagnostics import DiagnosticBag
from .fabric_tables import TableIndex
from .mapping import _entity_source, _missing_column_reason, _missing_table_reason, resolve_key, sanitize_name, value_type_for_range
from .rdf_model import GraphModel, local_name

NAME_REGEX = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,127}$")
UNSAFE_COLUMN_CHARS = set(" ,;{}()=\n\t")

_RESERVED_NAMESPACES = (str(XSD), str(RDF), str(RDFS), str(OWL))

RULES: dict[str, str] = {
    "L-PARSE": "File parses and all prefixes resolve",
    "L-PUN": "An IRI is declared as both a class and a property",
    "L-UNDECLARED": "Domain/range/inverse target is never declared",
    "L-DOMAIN-MISSING": "Property has no rdfs:domain",
    "L-DOMAIN-MULTI": "Property has multiple / union domains",
    "L-RANGE-MISSING": "Property has no rdfs:range",
    "L-RANGE-UNMAPPED": "Range has no Fabric valueType mapping",
    "L-RANGE-CONFLICT": "Same local name declared with two different ranges",
    "L-NAME-REGEX": "Local name violates the Fabric identifier regex",
    "L-NAME-LENGTH": "Local name exceeds the portal name length",
    "L-NAME-DUP": "Two IRIs collapse to the same local name",
    "L-SELFLOOP": "Object property whose domain and range intersect",
    "L-INVERSE": "owl:inverseOf pair detected",
    "L-CLASSEXPR": "Unsupported class expression in domain/range",
    "L-SRC-TABLE": "Class has no source-table annotation",
    "L-SRC-TABLE-FORMAT": "Source-table annotation is not schema.table",
    "L-SRC-TABLE-MISSING": "Source table does not exist in the lakehouse (--require-physical-tables)",
    "L-SRC-TABLE-UNVERIFIED": "Source table existence could not be verified (unresolved lakehouse id)",
    "L-SRC-LAKEHOUSE": "sourceLakehouse has no matching config entry",
    "L-SRC-COLUMN": "Property on a bound class has no source-column annotation",
    "L-SRC-COLUMN-UNSAFE": "Source column name risks delta column mapping",
    "L-SRC-COLUMN-MISSING": "Source column does not exist in the lakehouse table (--require-physical-tables)",
    "L-KEY": "No key candidate resolvable for a class",
    "L-FK-MISSING": "Object property has no source-column annotation",
    "L-ORPHAN": "Class with no properties and no relationships",
    "L-RESTRICTION": "Restriction references a property outside the class domain",
}


def _is_reserved(iri: str) -> bool:
    return any(iri.startswith(prefix) for prefix in _RESERVED_NAMESPACES)


def lint(model: GraphModel, config: Config, bag: Optional[DiagnosticBag] = None, table_index: Optional[TableIndex] = None) -> DiagnosticBag:
    bag = bag if bag is not None else DiagnosticBag()
    max_length = int(config.flag("maxPortalNameLength") or 26)

    _lint_punning(model, bag)
    _lint_names(model, bag, max_length)
    _lint_property_shape(model, bag)
    _lint_classes(model, config, bag, table_index)
    _lint_object_properties(model, bag)
    return bag


def _lint_punning(model: GraphModel, bag: DiagnosticBag) -> None:
    for iri in sorted(model.punned):
        bag.warning("L-PUN", "declared as both a class and a property; both are emitted (separate namespaces)", local_name(iri))


def _lint_names(model: GraphModel, bag: DiagnosticBag, max_length: int) -> None:
    for label, records in (("class", model.classes.values()), ("property", model.properties.values())):
        by_name: dict[str, list[str]] = {}
        for record in records:
            by_name.setdefault(record.name, []).append(record.iri)
            if not NAME_REGEX.match(record.name):
                bag.warning(
                    "L-NAME-REGEX",
                    f"{label} name does not match ^[a-zA-Z][a-zA-Z0-9_-]{{0,127}}$; will be auto-sanitized to '{sanitize_name(record.name)}'",
                    record.name,
                )
            elif len(record.name) > max_length:
                bag.warning(
                    "L-NAME-LENGTH",
                    f"{label} name is {len(record.name)} characters; the portal limit is {max_length}",
                    record.name,
                )
        for name, iris in sorted(by_name.items()):
            if len(iris) > 1:
                bag.warning(
                    "L-NAME-DUP",
                    f"{label} local name is used by {len(iris)} IRIs: {', '.join(sorted(iris))}; will be auto-suffixed "
                    f"({name}, {name}2, {name}3, ...)",
                    name,
                )


def _lint_property_shape(model: GraphModel, bag: DiagnosticBag) -> None:
    ranges_by_name: dict[str, set[str]] = {}
    for prop in sorted(model.properties.values(), key=lambda p: p.name):
        if prop.unsupported_expression:
            bag.warning("L-CLASSEXPR", "unsupported class expression in domain/range; ignored by the mapper", prop.name)

        if not prop.declared_domain:
            bag.warning("L-DOMAIN-MISSING", "property has no rdfs:domain and will be dropped (cannot attach to an entity type)", prop.name)
        elif len(prop.domains) > 1:
            bag.warning(
                "L-DOMAIN-MULTI",
                f"domain fans out to {len(prop.domains)} classes: {', '.join(model.class_name(d) for d in prop.domains)}",
                prop.name,
            )

        for iri in prop.domains:
            if iri not in model.classes and not _is_reserved(iri):
                bag.warning("L-UNDECLARED", f"domain '{local_name(iri)}' is never declared as a class; property will be dropped", prop.name)

        if prop.kind == "object":
            for iri in prop.ranges:
                if iri not in model.classes and not _is_reserved(iri):
                    bag.warning(
                        "L-UNDECLARED", f"range '{local_name(iri)}' is never declared as a class; relationship will be dropped", prop.name
                    )
            if prop.inverse_of and prop.inverse_of not in model.properties:
                bag.warning("L-UNDECLARED", f"owl:inverseOf target '{local_name(prop.inverse_of)}' is never declared", prop.name)
            continue

        if not prop.declared_range:
            bag.warning("L-RANGE-MISSING", "no rdfs:range; the valueType will default to String", prop.name)
        else:
            for iri in prop.ranges:
                if value_type_for_range(iri) is None:
                    bag.warning("L-RANGE-UNMAPPED", f"range '{local_name(iri)}' has no Fabric valueType mapping", prop.name)
            ranges_by_name.setdefault(prop.name, set()).update(prop.ranges)

    for name, ranges in sorted(ranges_by_name.items()):
        mapped = {value_type_for_range(iri) for iri in ranges}
        if len(mapped) > 1:
            bag.error(
                "L-RANGE-CONFLICT",
                f"declared with conflicting ranges ({', '.join(sorted(local_name(r) for r in ranges))}); "
                "Fabric requires one valueType per property name",
                name,
            )


def _lint_classes(model: GraphModel, config: Config, bag: DiagnosticBag, table_index: Optional[TableIndex] = None) -> None:
    used_in_relationships: set[str] = set()
    for prop in model.object_properties():
        used_in_relationships.update(prop.domains)
        used_in_relationships.update(prop.ranges)

    for class_iri in sorted(model.classes):
        record = model.classes[class_iri]
        entry = config.entity(record.name)
        table_ref = entry.get("sourceTable") or record.annotations.get("table")
        lakehouse = entry.get("sourceLakehouse") or record.annotations.get("lakehouse")

        if not table_ref:
            bag.warning("L-SRC-TABLE", "no source-table annotation; will be emitted as an unbound entity type", record.name)
        elif "." not in table_ref:
            bag.warning("L-SRC-TABLE-FORMAT", f"source table '{table_ref}' has no schema; defaulting to 'dbo.{table_ref}'", record.name)

        if lakehouse and config.lakehouses and lakehouse not in config.lakehouses:
            bag.error(
                "L-SRC-LAKEHOUSE",
                f"sourceLakehouse '{lakehouse}' has no entry under fabric.lakehouses",
                record.name,
            )

        source_lakehouse = source_schema = source_table = ref = None
        table_verifiable = False
        if table_ref and table_index is not None:
            source_lakehouse, source_schema, source_table = _entity_source(model, config, class_iri)
            ref = config.lakehouse(source_lakehouse)
            if table_index.is_unverifiable(ref):
                bag.warning(
                    "L-SRC-TABLE-UNVERIFIED",
                    "source table existence could not be verified (unresolved lakehouse id); entity type kept",
                    record.name,
                )
            elif _missing_table_reason(config, table_index, source_lakehouse, source_schema, source_table):
                bag.warning(
                    "L-SRC-TABLE-MISSING",
                    f"source table '{source_schema}.{source_table}' does not exist in the lakehouse; will be dropped",
                    record.name,
                )
            else:
                table_verifiable = True

        data_properties = model.properties_of(class_iri, "data")
        for prop in sorted(data_properties, key=lambda p: p.name):
            column = prop.annotations.get("column")
            if table_ref and not column:
                bag.warning("L-SRC-COLUMN", "no source-column annotation; falling back to the local name", f"{record.name}.{prop.name}")
            if set(column or prop.name) & UNSAFE_COLUMN_CHARS:
                bag.warning(
                    "L-SRC-COLUMN-UNSAFE",
                    f"column '{column or prop.name}' contains a character that enables delta column mapping",
                    f"{record.name}.{prop.name}",
                )
            if table_verifiable:
                missing_column = _missing_column_reason(
                    config, table_index, source_lakehouse, source_schema, source_table, column or prop.name
                )
                if missing_column:
                    bag.warning(
                        "L-SRC-COLUMN-MISSING",
                        f"column '{missing_column}' does not exist in table '{source_schema}.{source_table}'; "
                        "property will be left unbound",
                        f"{record.name}.{prop.name}",
                    )

        key, _rule, candidates = resolve_key(model, config, class_iri)
        if not key:
            bag.warning(
                "L-KEY",
                f"no key candidate resolvable; candidates: {', '.join(candidates) or 'none'}",
                record.name,
            )

        if not data_properties and class_iri not in used_in_relationships:
            bag.info("L-ORPHAN", "class has no properties and no relationships", record.name)

        domain_iris = {p.iri for p in model.properties.values() if class_iri in p.domains}
        for restriction in record.restrictions:
            if restriction.on_property not in domain_iris:
                bag.warning(
                    "L-RESTRICTION",
                    f"restriction on '{local_name(restriction.on_property)}' which is not in this class's domain closure",
                    record.name,
                )


def _lint_object_properties(model: GraphModel, bag: DiagnosticBag) -> None:
    reported_inverses: set[frozenset[str]] = set()
    for prop in sorted(model.object_properties(), key=lambda p: p.name):
        overlap = set(prop.domains) & set(prop.ranges)
        if overlap:
            names = ", ".join(sorted(model.class_name(iri) for iri in overlap))
            bag.warning("L-SELFLOOP", f"domain and range intersect on {names}; the pair will be dropped", prop.name)

        if prop.inverse_of and prop.inverse_of in model.properties:
            pair = frozenset({prop.iri, prop.inverse_of})
            if pair not in reported_inverses:
                reported_inverses.add(pair)
                bag.info("L-INVERSE", f"inverse of '{local_name(prop.inverse_of)}'; one side will be dropped", prop.name)

        if not prop.annotations.get("column"):
            bag.warning("L-FK-MISSING", "no source-column annotation; no contextualization can be derived", prop.name)
