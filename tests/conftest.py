from pathlib import Path

import pytest
from rdflib import Graph

from rdf2ontology.config import Config, load_config
from rdf2ontology.diagnostics import DiagnosticBag
from rdf2ontology.ids import IdMap
from rdf2ontology.lint import lint
from rdf2ontology.mapping import build_ontology
from rdf2ontology.rdf_model import GraphModel

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_TTL = REPO_ROOT / "ontology-items" / "rdf" / "customer_address.ttl"
DEFAULTS_CONFIG = REPO_ROOT / "rdf2ontology" / "config" / "defaults.yaml"
SAMPLE_OVERRIDES = REPO_ROOT / "rdf2ontology" / "config" / "customer_address.overrides.yaml"
SAMPLE_CONFIG = [DEFAULTS_CONFIG, SAMPLE_OVERRIDES]  # generic defaults + per-ontology overrides, merged
REFERENCE_ITEM = REPO_ROOT / "ontology-ci-cd" / "Ontology_1.Ontology"

PREFIXES = """
@prefix ex:   <http://example.org/o#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
"""

CLEAN_TTL = PREFIXES + """
ex:sourceTable  a owl:AnnotationProperty .
ex:sourceColumn a owl:AnnotationProperty .

ex:Customer a owl:Class ; ex:sourceTable "dbo.customer" .
ex:Address  a owl:Class ; ex:sourceTable "dbo.address" .

ex:CustomerKey a owl:DatatypeProperty , owl:FunctionalProperty ;
    ex:sourceColumn "CustomerKey" ; rdfs:domain ex:Customer ; rdfs:range xsd:string .
ex:CustomerName a owl:DatatypeProperty ;
    ex:sourceColumn "CustomerName" ; rdfs:domain ex:Customer ; rdfs:range xsd:string .
ex:AddressKey a owl:DatatypeProperty , owl:FunctionalProperty ;
    ex:sourceColumn "AddressKey" ; rdfs:domain ex:Address ; rdfs:range xsd:string .
ex:AddressName a owl:DatatypeProperty ;
    ex:sourceColumn "AddressName" ; rdfs:domain ex:Address ; rdfs:range xsd:string .

ex:hasAddress a owl:ObjectProperty ;
    ex:sourceColumn "MainAddressKey" ; rdfs:domain ex:Customer ; rdfs:range ex:Address .

ex:Customer rdfs:subClassOf
    [ a owl:Restriction ; owl:onProperty ex:CustomerKey ; owl:cardinality "1"^^xsd:nonNegativeInteger ] .
ex:Address rdfs:subClassOf
    [ a owl:Restriction ; owl:onProperty ex:AddressKey ; owl:cardinality "1"^^xsd:nonNegativeInteger ] .
"""

# CLEAN_TTL plus :synonyms on a class and a property (semicolon+comma separated) and
# skos:altLabel on an object property, mirroring the Telco ontology's annotation vocabulary.
ENRICHED_TTL = PREFIXES + """
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .

ex:synonyms a owl:AnnotationProperty .
ex:sourceTable  a owl:AnnotationProperty .
ex:sourceColumn a owl:AnnotationProperty .

ex:Customer a owl:Class ; ex:sourceTable "dbo.customer" ;
    ex:synonyms "Customer; Client; Account Holder" .
ex:Address  a owl:Class ; ex:sourceTable "dbo.address" .

ex:CustomerKey a owl:DatatypeProperty , owl:FunctionalProperty ;
    ex:sourceColumn "CustomerKey" ; rdfs:domain ex:Customer ; rdfs:range xsd:string ;
    ex:synonyms "Customer Id, Customer Key" .
ex:AddressKey a owl:DatatypeProperty , owl:FunctionalProperty ;
    ex:sourceColumn "AddressKey" ; rdfs:domain ex:Address ; rdfs:range xsd:string .

ex:hasAddress a owl:ObjectProperty ;
    ex:sourceColumn "MainAddressKey" ; rdfs:domain ex:Customer ; rdfs:range ex:Address ;
    skos:altLabel "IsAddressOfCustomer" .

ex:Customer rdfs:subClassOf
    [ a owl:Restriction ; owl:onProperty ex:CustomerKey ; owl:cardinality "1"^^xsd:nonNegativeInteger ] .
ex:Address rdfs:subClassOf
    [ a owl:Restriction ; owl:onProperty ex:AddressKey ; owl:cardinality "1"^^xsd:nonNegativeInteger ] .
"""


def make_model(ttl: str, config: Config = None) -> GraphModel:
    config = config or Config()
    graph = Graph()
    graph.parse(data=ttl, format="turtle")
    return GraphModel(graph, config)


def lint_rules(ttl: str, config: Config = None) -> set[str]:
    config = config or Config()
    bag = DiagnosticBag()
    lint(make_model(ttl, config), config, bag)
    return {diag.rule for diag in bag}


def build(ttl: str, config: Config = None, name: str = "TestOntology"):
    config = config or Config()
    bag = DiagnosticBag()
    ontology = build_ontology(make_model(ttl, config), config, name, bag)
    id_map = IdMap(ontology_name=name, allow_new=True)
    id_map.assign(ontology)
    return ontology, id_map, bag


@pytest.fixture(scope="session")
def sample_config() -> Config:
    return load_config(SAMPLE_CONFIG)


@pytest.fixture(scope="session")
def sample_model(sample_config: Config) -> GraphModel:
    graph = Graph()
    graph.parse(source=str(SAMPLE_TTL), format="turtle")
    return GraphModel(graph, sample_config)


@pytest.fixture(scope="session")
def sample_ontology(sample_model, sample_config):
    bag = DiagnosticBag(ignore=sample_config.lint_ignore)
    ontology = build_ontology(sample_model, sample_config, "Customer_Address", bag)
    id_map = IdMap(ontology_name="Customer_Address", allow_new=True)
    id_map.assign(ontology, source_rdf=str(SAMPLE_TTL))
    return ontology, id_map, bag
