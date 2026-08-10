"""Deterministic ids, reuse from the persisted map, and the new-id gate."""

import json

import pytest

from rdf2ontology.config import Config
from rdf2ontology.ids import IdMap, IdMapError, derive_guid, derive_id

from conftest import CLEAN_TTL, build


def test_derive_id_is_deterministic_and_in_range():
    first = derive_id("Demo", "entity", "Customer")
    second = derive_id("Demo", "entity", "Customer")
    assert first == second
    assert len(first) == 18
    assert 0 < int(first) < 2**63


def test_derive_id_is_scoped_by_ontology_kind_and_name():
    assert derive_id("A", "entity", "Customer") != derive_id("B", "entity", "Customer")
    assert derive_id("A", "entity", "Customer") != derive_id("A", "property", "Customer")
    assert derive_id("A", "entity", "Customer") != derive_id("A", "entity", "Address")


def test_derive_guid_is_deterministic():
    assert derive_guid("Demo", "binding", "Customer|static") == derive_guid("Demo", "binding", "Customer|static")


def test_two_builds_produce_identical_ids():
    _o1, first, _b1 = build(CLEAN_TTL)
    _o2, second, _b2 = build(CLEAN_TTL)
    assert first.all_ids() == second.all_ids()


def test_ids_are_unique_across_the_ontology(sample_ontology):
    _ontology, id_map, _bag = sample_ontology
    ids = id_map.all_ids()
    assert len(ids) == len(set(ids))


def test_binding_a_previously_unbound_class_does_not_change_ids(tmp_path):
    unbound = """
@prefix ex:   <http://example.org/o#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
ex:sourceTable a owl:AnnotationProperty .
ex:City a owl:Class .
ex:CityKey a owl:DatatypeProperty ; rdfs:domain ex:City ; rdfs:range xsd:string .
"""
    bound = unbound + '\nex:City ex:sourceTable "dbo.city" .\n'

    _o1, before, _b1 = build(unbound, name="Demo")
    _o2, after, _b2 = build(bound, name="Demo")
    assert before.entity_id("City") == after.entity_id("City")
    assert before.property_id("City", "CityKey") == after.property_id("City", "CityKey")
    assert "static" not in before.entity_types["City"]["bindings"]
    assert "static" in after.entity_types["City"]["bindings"]


def test_id_map_round_trip(tmp_path):
    ontology, id_map, _bag = build(CLEAN_TTL, name="Demo")
    path = tmp_path / "id-map.json"
    id_map.path = path
    id_map.save()

    reloaded = IdMap.load(path, "Demo", allow_new=False)
    ontology2, _ids, _bag2 = build(CLEAN_TTL, name="Demo")
    reloaded.assign(ontology2)
    assert reloaded.entity_id("Customer") == id_map.entity_id("Customer")
    assert json.loads(path.read_text())["ontologyName"] == "Demo"


def test_new_concept_is_rejected_when_minting_is_disabled(tmp_path):
    ontology, id_map, _bag = build(CLEAN_TTL, name="Demo")
    path = tmp_path / "id-map.json"
    id_map.path = path
    id_map.save()

    extended = CLEAN_TTL + """
ex:Nickname a owl:DatatypeProperty ; ex:sourceColumn "Nickname" ;
    rdfs:domain ex:Customer ; rdfs:range xsd:string .
"""
    ontology2, _ids, _bag2 = build(extended, name="Demo")
    locked = IdMap.load(path, "Demo", allow_new=False)
    with pytest.raises(IdMapError):
        locked.assign(ontology2)


def test_removed_concepts_are_pruned_from_the_map():
    extended = CLEAN_TTL + """
ex:Nickname a owl:DatatypeProperty ; ex:sourceColumn "Nickname" ;
    rdfs:domain ex:Customer ; rdfs:range xsd:string .
"""
    ontology, id_map, _bag = build(extended, name="Demo")
    assert "Nickname" in id_map.entity_types["Customer"]["properties"]

    ontology2, _ids, _bag2 = build(CLEAN_TTL, name="Demo")
    id_map.assign(ontology2)
    assert "Nickname" not in id_map.entity_types["Customer"]["properties"]


def test_reference_ids_can_be_seeded_and_are_reused():
    """Brownfield: a pre-seeded map keeps the ids already deployed to Fabric."""
    ontology, _generated, _bag = build(CLEAN_TTL, name="Demo")
    seeded = IdMap(ontology_name="Demo", allow_new=True)
    seeded.entity_types = {
        "Customer": {
            "id": "5202887405100123318",
            "properties": {"CustomerKey": {"id": "3127136010913050252"}},
            "bindings": {"static": "1d7a8db9-b7f3-4c9a-a0f0-56680983466e"},
        }
    }
    seeded.assign(ontology)
    assert seeded.entity_id("Customer") == "5202887405100123318"
    assert seeded.property_id("Customer", "CustomerKey") == "3127136010913050252"
    assert seeded.binding_id("Customer", "static") == "1d7a8db9-b7f3-4c9a-a0f0-56680983466e"
