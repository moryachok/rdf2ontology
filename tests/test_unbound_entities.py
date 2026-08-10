"""Entities and relationships must survive when the RDF carries no binding info."""

import json

from rdf2ontology.config import load_config
from rdf2ontology.diagnostics import DiagnosticBag
from rdf2ontology.emit import emit
from rdf2ontology.ids import IdMap
from rdf2ontology.mapping import build_ontology
from rdf2ontology.rdf_model import GraphModel
from rdf2ontology.validate import validate_item_dir

from conftest import SAMPLE_CONFIG, SAMPLE_TTL

UNBOUND_CLASSES = {
    "City", "Contact", "Country", "CreditClass", "CustomerStatus", "CustomerSubType",
    "CustomerType", "Dealer", "Division", "Industry", "LineOfBusiness", "MDUCluster",
    "Market", "Organization", "PayChannel", "Region", "SalesChannel",
    "SalesRepresentative", "ServiceProvider", "State", "ZipCode",
}


def _emit_sample(tmp_path):
    from rdflib import Graph

    config = load_config(SAMPLE_CONFIG)
    graph = Graph()
    graph.parse(source=str(SAMPLE_TTL), format="turtle")
    model = GraphModel(graph, config)
    bag = DiagnosticBag(ignore=config.lint_ignore)
    ontology = build_ontology(model, config, "Customer_Address", bag)
    id_map = IdMap(ontology_name="Customer_Address", allow_new=True)
    id_map.assign(ontology)
    return ontology, id_map, emit(ontology, id_map, config, tmp_path).item_dir, bag


def test_all_unbound_classes_are_emitted(sample_ontology):
    ontology, _id_map, _bag = sample_ontology
    assert {e.name for e in ontology.unbound_entities()} == UNBOUND_CLASSES


def test_unbound_entities_have_no_databindings(tmp_path):
    _ontology, id_map, item_dir, _bag = _emit_sample(tmp_path)
    for name in UNBOUND_CLASSES:
        entity_dir = item_dir / "EntityTypes" / id_map.entity_id(name)
        assert (entity_dir / "definition.json").is_file(), name
        assert not (entity_dir / "DataBindings").exists(), name


def test_bound_entities_have_exactly_one_static_binding(tmp_path):
    _ontology, id_map, item_dir, _bag = _emit_sample(tmp_path)
    for name in ("Customer", "Address"):
        bindings = list((item_dir / "EntityTypes" / id_map.entity_id(name) / "DataBindings").glob("*.json"))
        assert len(bindings) == 1, name
        payload = json.loads(bindings[0].read_text())
        assert payload["dataBindingConfiguration"]["dataBindingType"] == "NonTimeSeries"


def test_relationships_to_unbound_entities_survive(sample_ontology):
    ontology, _id_map, _bag = sample_ontology
    by_name = {r.name: r for r in ontology.relationships}
    # Customer -> City: City is unbound but has a key, so the edge and its link table resolve
    assert by_name["locatedInCityCustomerCity"].target_entity == "City"
    assert by_name["locatedInCityCustomerCity"].contextualization is not None
    # Customer -> Industry: Industry is keyless, so the type is emitted without edges
    assert by_name["hasIndustry"].target_entity == "Industry"
    assert by_name["hasIndustry"].contextualization is None


def test_keyless_entities_are_warnings_not_errors(sample_ontology):
    _ontology, _id_map, bag = sample_ontology
    assert not bag.has_errors(), [str(d) for d in bag.errors]
    assert len([d for d in bag.warnings if d.rule == "W9"]) == 16


def test_the_whole_tree_still_validates(tmp_path):
    _ontology, _id_map, item_dir, _bag = _emit_sample(tmp_path)
    bag = validate_item_dir(item_dir)
    assert not bag.has_errors(), [str(d) for d in bag.errors]


def test_disabling_unbound_emission_restores_the_two_entity_output(tmp_path):
    from rdflib import Graph

    config = load_config(SAMPLE_CONFIG)
    config.defaults["emitUnboundEntities"] = False
    graph = Graph()
    graph.parse(source=str(SAMPLE_TTL), format="turtle")
    bag = DiagnosticBag(ignore=config.lint_ignore)
    ontology = build_ontology(GraphModel(graph, config), config, "Customer_Address", bag)
    assert set(ontology.entities) == {"Customer", "Address"}
    assert {r.name for r in ontology.relationships} == {"hasAddress"}
