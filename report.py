"""Human-readable rendering of diagnostics and build summaries."""

from __future__ import annotations

from typing import Optional

from .diagnostics import DiagnosticBag, Severity
from .emit import EmitResult
from .fabric_tables import TableIndex
from .ir import OntologyIR

_ORDER = (Severity.ERROR, Severity.WARNING, Severity.INFO)


def render_diagnostics(bag: DiagnosticBag, title: str, show_info: bool = True) -> str:
    lines = [f"== {title} =="]
    for severity in _ORDER:
        if severity is Severity.INFO and not show_info:
            continue
        for diag in sorted(bag.of(severity), key=lambda d: (d.rule, d.subject or "")):
            lines.append(f"  {diag}")
    counts = bag.counts()
    lines.append(
        f"  -- {counts['error']} error(s), {counts['warning']} warning(s), "
        f"{counts['info']} info, {counts['ignored']} ignored"
    )
    return "\n".join(lines)


def render_summary(ontology: OntologyIR, result: Optional[EmitResult] = None, table_index: Optional[TableIndex] = None) -> str:
    bound = ontology.bound_entities()
    unbound = ontology.unbound_entities()
    with_context = [r for r in ontology.relationships if r.contextualization is not None]
    lines = [
        f"== Ontology '{ontology.name}' ==",
        f"  entity types      : {len(ontology.entities)} ({len(bound)} bound, {len(unbound)} unbound)",
        f"  relationship types: {len(ontology.relationships)} ({len(with_context)} contextualized)",
        f"  properties        : {sum(len(e.properties) for e in ontology.entities.values())}",
    ]
    if table_index is not None:
        stats = table_index.stats
        lines.append(
            f"  physical tables   : {stats.verified_present} present, {stats.verified_missing} missing, "
            f"{stats.unverified} unverified"
        )
        if stats.columns_total:
            lines.append(
                f"  physical columns  : {stats.columns_present} present, {stats.columns_missing} missing, "
                f"{stats.columns_unverified} unverified"
            )
    keyless = sorted(e.name for e in ontology.entities.values() if not e.key)
    if keyless:
        lines.append(f"  keyless           : {', '.join(keyless)}")
    if ontology.renames:
        lines.append("  auto-renamed (illegal/duplicate names resolved automatically):")
        for key, final in sorted(ontology.renames.items()):
            kind, raw = key.split(":", 1)
            lines.append(f"    - {kind} '{raw}' -> '{final}'")
    if unbound:
        lines.append("  bindings still missing (backlog):")
        for entity in sorted(unbound, key=lambda e: e.name):
            lines.append(f"    - {entity.name}")
    if ontology.skipped_entities:
        lines.append("  skipped (physical table not found):")
        for entity_name, reason in sorted(ontology.skipped_entities.items()):
            lines.append(f"    - {entity_name}: {reason}")
    if ontology.unbound_properties:
        lines.append("  unbound (physical column not found):")
        for subject, reason in sorted(ontology.unbound_properties.items()):
            lines.append(f"    - {subject}: {reason}")
    if result is not None:
        lines.append(f"  written           : {len(result.files)} file(s) -> {result.item_dir}")
        if result.parameter_file:
            lines.append(f"  parameter file    : {result.parameter_file}")
    return "\n".join(lines)


def render_preview(ontology: OntologyIR) -> str:
    """Change preview shown before any write to Fabric."""
    lines = [f"== Preview: {ontology.name} =="]
    for name in sorted(ontology.entities):
        entity = ontology.entities[name]
        source = f"{entity.schema}.{entity.table}" if entity.bound else "<no binding>"
        key = ",".join(entity.key) or "<keyless>"
        lines.append(f"  entity  {name:<24} key={key:<24} source={source} props={len(entity.properties)}")
    for relationship in sorted(ontology.relationships, key=lambda r: r.name):
        ctx = relationship.contextualization
        link = f"{ctx.schema}.{ctx.table}" if ctx else "<no contextualization>"
        lines.append(
            f"  rel     {relationship.name:<24} {relationship.source_entity} -> {relationship.target_entity}  link={link}"
        )
    return "\n".join(lines)


def json_report(
    ontology: OntologyIR,
    lint_bag: DiagnosticBag,
    validation_bag: DiagnosticBag,
    result: Optional[EmitResult] = None,
    table_index: Optional[TableIndex] = None,
) -> dict:
    return {
        "ontology": ontology.name,
        "renames": dict(ontology.renames),
        "skippedMissingTable": dict(ontology.skipped_entities),
        "unboundMissingColumn": dict(ontology.unbound_properties),
        "physicalTables": (
            {
                "verifiedPresent": table_index.stats.verified_present,
                "verifiedMissing": table_index.stats.verified_missing,
                "unverified": table_index.stats.unverified,
            }
            if table_index is not None
            else None
        ),
        "physicalColumns": (
            {
                "verifiedPresent": table_index.stats.columns_present,
                "verifiedMissing": table_index.stats.columns_missing,
                "unverified": table_index.stats.columns_unverified,
            }
            if table_index is not None
            else None
        ),
        "entityTypes": {
            name: {
                "bound": entity.bound,
                "sourceTable": f"{entity.schema}.{entity.table}" if entity.bound else None,
                "key": entity.key,
                "keyRule": entity.key_rule,
                "displayNameProperty": entity.display_name_property,
                "propertyCount": len(entity.properties),
            }
            for name, entity in sorted(ontology.entities.items())
        },
        "relationshipTypes": {
            relationship.name: {
                "source": relationship.source_entity,
                "target": relationship.target_entity,
                "contextualized": relationship.contextualization is not None,
            }
            for relationship in sorted(ontology.relationships, key=lambda r: r.name)
        },
        "lint": {"counts": lint_bag.counts(), "diagnostics": lint_bag.to_list()},
        "validation": {"counts": validation_bag.counts(), "diagnostics": validation_bag.to_list()},
        "output": {
            "itemDir": str(result.item_dir) if result else None,
            "files": [str(p) for p in result.files] if result else [],
            "parameterFile": str(result.parameter_file) if result and result.parameter_file else None,
        },
    }
