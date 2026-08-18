"""Raw RDF graph reader: classes, properties, domains/ranges, restrictions, annotations."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import DCTERMS, OWL, RDF, RDFS, SKOS

from .config import Config

_SERIALIZATION_BY_SUFFIX = {
    ".ttl": "turtle",
    ".turtle": "turtle",
    ".n3": "n3",
    ".nt": "nt",
    ".rdf": "xml",
    ".owl": "xml",
    ".xml": "xml",
    ".jsonld": "json-ld",
    ".json": "json-ld",
    ".trig": "trig",
}

_UNSUPPORTED_CLASS_EXPRESSIONS = (OWL.intersectionOf, OWL.complementOf, OWL.oneOf)


class RdfParseError(Exception):
    """Raised when the input file cannot be parsed as RDF."""


def local_name(iri: str) -> str:
    text = str(iri)
    for sep in ("#", "/"):
        if sep in text:
            text = text.rsplit(sep, 1)[-1]
    return text


@dataclass
class Restriction:
    on_property: str
    kind: str
    value: Optional[int]


@dataclass
class ClassRecord:
    iri: str
    name: str
    label: Optional[str] = None
    comment: Optional[str] = None
    annotations: dict[str, str] = field(default_factory=dict)
    restrictions: list[Restriction] = field(default_factory=list)
    synonyms: list[str] = field(default_factory=list)
    raw_annotations: dict[str, str] = field(default_factory=dict)


@dataclass
class PropertyRecord:
    iri: str
    name: str
    kind: str  # "data" | "object"
    label: Optional[str] = None
    comment: Optional[str] = None
    annotations: dict[str, str] = field(default_factory=dict)
    synonyms: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    ranges: list[str] = field(default_factory=list)
    declared_domain: bool = False
    declared_range: bool = False
    unsupported_expression: bool = False
    functional: bool = False
    inverse_of: Optional[str] = None
    alt_label: Optional[str] = None
    raw_annotations: dict[str, str] = field(default_factory=dict)


class GraphModel:
    """Everything the linter and the mapper need, extracted once from the graph."""

    def __init__(self, graph: Graph, config: Config) -> None:
        self.graph = graph
        self.config = config
        self.classes: dict[str, ClassRecord] = {}
        self.properties: dict[str, PropertyRecord] = {}
        self.annotation_properties: set[str] = set()
        self.punned: set[str] = set()
        self.ontology_title: Optional[str] = None
        self._ann_names = {
            "table": config.rdf["sourceTableProperty"],
            "column": config.rdf["sourceColumnProperty"],
            "lakehouse": config.rdf["sourceLakehouseProperty"],
            "key": config.rdf["keyProperty"],
        }
        self._ann_namespace = config.rdf.get("annotationNamespace")
        self._join_condition_name = config.rdf.get("joinConditionProperty")
        self._synonyms_name = config.rdf.get("synonymsProperty")
        self._build()

    # -- construction ------------------------------------------------------
    def _build(self) -> None:
        g = self.graph
        for subject in set(g.subjects(RDF.type, OWL.AnnotationProperty)):
            if isinstance(subject, URIRef):
                self.annotation_properties.add(str(subject))

        for subject in g.subjects(RDF.type, OWL.Ontology):
            title = self._literal(subject, DCTERMS.title) or self._literal(subject, RDFS.label)
            if title:
                self.ontology_title = title
                break

        class_iris = {s for s in g.subjects(RDF.type, OWL.Class) if isinstance(s, URIRef)}
        class_iris |= {s for s in g.subjects(RDF.type, RDFS.Class) if isinstance(s, URIRef)}
        for iri in class_iris:
            self.classes[str(iri)] = ClassRecord(
                iri=str(iri),
                name=local_name(iri),
                label=self._literal(iri, RDFS.label),
                comment=self._literal(iri, RDFS.comment),
                annotations=self._annotations(iri),
                restrictions=self._restrictions(iri),
                synonyms=self._synonyms(iri),
                raw_annotations=self._raw_annotations(iri),
            )

        data_iris = {s for s in g.subjects(RDF.type, OWL.DatatypeProperty) if isinstance(s, URIRef)}
        object_iris = {s for s in g.subjects(RDF.type, OWL.ObjectProperty) if isinstance(s, URIRef)}
        functional = {str(s) for s in g.subjects(RDF.type, OWL.FunctionalProperty)}

        for iri in sorted(data_iris | object_iris, key=str):
            kind = "object" if iri in object_iris else "data"
            domains, unsupported_domain = self._class_expression(list(g.objects(iri, RDFS.domain)))
            ranges, unsupported_range = self._class_expression(list(g.objects(iri, RDFS.range)))
            inverse = next(iter(g.objects(iri, OWL.inverseOf)), None)
            record = PropertyRecord(
                iri=str(iri),
                name=local_name(iri),
                kind=kind,
                label=self._literal(iri, RDFS.label),
                comment=self._literal(iri, RDFS.comment),
                annotations=self._annotations(iri),
                synonyms=self._synonyms(iri),
                domains=domains,
                ranges=ranges,
                declared_domain=bool(list(g.objects(iri, RDFS.domain))),
                declared_range=bool(list(g.objects(iri, RDFS.range))),
                unsupported_expression=unsupported_domain or unsupported_range,
                functional=str(iri) in functional,
                alt_label=self._alt_label(iri),
                inverse_of=str(inverse) if isinstance(inverse, URIRef) else None,
                raw_annotations=self._raw_annotations(iri),
            )
            self.properties[str(iri)] = record
            if str(iri) in self.classes:
                self.punned.add(str(iri))

    def _literal(self, subject: URIRef, predicate: URIRef) -> Optional[str]:
        for value in self.graph.objects(subject, predicate):
            if isinstance(value, Literal):
                return str(value)
        return None

    def _synonyms(self, subject: URIRef) -> list[str]:
        """Reads the configured `rdf.synonymsProperty` local name (semicolon/comma-separated)."""
        if not self._synonyms_name:
            return []
        values: list[str] = []
        seen: set[str] = set()
        for predicate, value in self.graph.predicate_objects(subject):
            if not isinstance(predicate, URIRef) or not isinstance(value, Literal):
                continue
            if self._ann_namespace and not str(predicate).startswith(self._ann_namespace):
                continue
            if local_name(predicate) != self._synonyms_name:
                continue
            for raw in re.split(r"[;,]", str(value)):
                part = raw.strip()
                key = part.lower()
                if part and key not in seen:
                    seen.add(key)
                    values.append(part)
        return values

    def _alt_label(self, subject: URIRef) -> Optional[str]:
        return self._literal(subject, SKOS.altLabel)

    def _raw_annotations(self, subject: URIRef) -> dict[str, str]:
        """Every literal-valued annotation on the subject, keyed by local name (first value wins)."""
        found: dict[str, str] = {}
        for predicate, value in self.graph.predicate_objects(subject):
            if not isinstance(predicate, URIRef) or not isinstance(value, Literal):
                continue
            if self._ann_namespace and not str(predicate).startswith(self._ann_namespace):
                continue
            found.setdefault(local_name(predicate), str(value))
        return found

    def _annotations(self, subject: URIRef) -> dict[str, str]:
        """Annotation values keyed by the logical role (table / column / lakehouse / key)."""
        found: dict[str, str] = {}
        join_condition: Optional[str] = None
        for predicate, value in self.graph.predicate_objects(subject):
            if not isinstance(predicate, URIRef) or not isinstance(value, Literal):
                continue
            predicate_local = local_name(predicate)
            if self._ann_namespace and not str(predicate).startswith(self._ann_namespace):
                continue
            for role, expected in self._ann_names.items():
                if predicate_local == expected:
                    found[role] = str(value)
            if self._join_condition_name and predicate_local == self._join_condition_name:
                join_condition = str(value)
        if "column" not in found and join_condition:
            # e.g. "producttable__t.fkColumn = othertable__t.pkColumn" -> "fkColumn"
            lhs = join_condition.split("=", 1)[0].strip()
            column = lhs.rsplit(".", 1)[-1].strip()
            if column:
                found["column"] = column
        return found

    def _restrictions(self, subject: URIRef) -> list[Restriction]:
        out: list[Restriction] = []
        for node in self.graph.objects(subject, RDFS.subClassOf):
            if not isinstance(node, BNode):
                continue
            if (node, RDF.type, OWL.Restriction) not in self.graph:
                continue
            on_property = next(iter(self.graph.objects(node, OWL.onProperty)), None)
            if not isinstance(on_property, URIRef):
                continue
            for kind, predicate in (
                ("cardinality", OWL.cardinality),
                ("minCardinality", OWL.minCardinality),
                ("maxCardinality", OWL.maxCardinality),
            ):
                for value in self.graph.objects(node, predicate):
                    try:
                        parsed = int(str(value))
                    except (TypeError, ValueError):
                        parsed = None
                    out.append(Restriction(str(on_property), kind, parsed))
        return out

    def _class_expression(self, nodes: list) -> tuple[list[str], bool]:
        """Flatten rdfs:domain / rdfs:range values, expanding owl:unionOf."""
        resolved: list[str] = []
        unsupported = False
        for node in nodes:
            if isinstance(node, URIRef):
                resolved.append(str(node))
                continue
            if isinstance(node, BNode):
                union = next(iter(self.graph.objects(node, OWL.unionOf)), None)
                if union is not None:
                    for member in self.graph.items(union):
                        if isinstance(member, URIRef):
                            resolved.append(str(member))
                        else:
                            unsupported = True
                    continue
                if any((node, predicate, None) in self.graph for predicate in _UNSUPPORTED_CLASS_EXPRESSIONS):
                    unsupported = True
                    continue
                unsupported = True
        seen: set[str] = set()
        ordered = [iri for iri in resolved if not (iri in seen or seen.add(iri))]
        return ordered, unsupported

    # -- queries -----------------------------------------------------------
    def data_properties(self) -> list[PropertyRecord]:
        return [p for p in self.properties.values() if p.kind == "data"]

    def object_properties(self) -> list[PropertyRecord]:
        return [p for p in self.properties.values() if p.kind == "object"]

    def properties_of(self, class_iri: str, kind: str = "data") -> list[PropertyRecord]:
        return [p for p in self.properties.values() if p.kind == kind and class_iri in p.domains]

    def class_name(self, iri: str) -> str:
        record = self.classes.get(iri)
        return record.name if record else local_name(iri)


def load_graph(path: Path, config: Config) -> tuple[Graph, GraphModel]:
    path = Path(path)
    if not path.is_file():
        raise RdfParseError(f"input file not found: {path}")
    fmt = _SERIALIZATION_BY_SUFFIX.get(path.suffix.lower())
    graph = Graph()
    try:
        graph.parse(source=str(path), format=fmt)
    except Exception as exc:  # rdflib raises a wide range of parser errors
        raise RdfParseError(f"{path.name}: {exc}") from exc
    return graph, GraphModel(graph, config)
