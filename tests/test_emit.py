"""Emitter fidelity: layout, ordering, byte-stability and the fabric-cicd contract."""

import json

import pytest

from rdf2ontology.config import Config, LakehouseRef
from rdf2ontology.emit import emit
from rdf2ontology.ids import IdMap

from conftest import CLEAN_TTL, ENRICHED_TTL, PREFIXES, build


def _config() -> Config:
    config = Config()
    config.lakehouses = {"lh": LakehouseRef(name="lh", item_id="11111111-1111-1111-1111-111111111111")}
    config.workspace_id = "22222222-2222-2222-2222-222222222222"
    return config


def _emit(ttl, tmp_path, config=None, name="Demo"):
    ontology, id_map, _bag = build(ttl, config or Config(), name=name)
    return ontology, id_map, emit(ontology, id_map, config or _config(), tmp_path)


def test_layout(tmp_path):
    _ontology, id_map, result = _emit(CLEAN_TTL, tmp_path)
    item_dir = result.item_dir
    assert item_dir.name == "Demo.Ontology"
    assert (item_dir / ".platform").is_file()
    assert (item_dir / "definition.json").is_file()
    customer = item_dir / "EntityTypes" / id_map.entity_id("Customer")
    assert (customer / "definition.json").is_file()
    assert len(list((customer / "DataBindings").glob("*.json"))) == 1
    relationship = item_dir / "RelationshipTypes" / id_map.relationship_id("hasAddress")
    assert (relationship / "definition.json").is_file()
    assert len(list((relationship / "Contextualizations").glob("*.json"))) == 1


def test_definition_and_platform(tmp_path):
    _ontology, _ids, result = _emit(CLEAN_TTL, tmp_path)
    assert json.loads((result.item_dir / "definition.json").read_text()) == {}
    platform = json.loads((result.item_dir / ".platform").read_text())
    assert platform["metadata"] == {"type": "Ontology", "displayName": "Demo"}
    assert platform["config"]["version"] == "2.0"
    assert platform["config"]["logicalId"]


def test_unbound_entity_has_no_databindings_folder(tmp_path):
    ttl = CLEAN_TTL + """
ex:Region a owl:Class .
ex:RegionKey a owl:DatatypeProperty ; rdfs:domain ex:Region ; rdfs:range xsd:string .
"""
    _ontology, id_map, result = _emit(ttl, tmp_path)
    region = result.item_dir / "EntityTypes" / id_map.entity_id("Region")
    assert (region / "definition.json").is_file()
    assert not (region / "DataBindings").exists()


def test_keyless_entity_emits_empty_entity_id_parts(tmp_path):
    ttl = CLEAN_TTL + "\nex:Region a owl:Class .\n"
    _ontology, id_map, result = _emit(ttl, tmp_path)
    entity = json.loads(
        (result.item_dir / "EntityTypes" / id_map.entity_id("Region") / "definition.json").read_text()
    )
    assert entity["entityIdParts"] == []
    assert entity["properties"] == []
    assert entity["displayNamePropertyId"] is None


def test_property_ordering_is_key_first_then_ascii(tmp_path):
    _ontology, id_map, result = _emit(CLEAN_TTL, tmp_path)
    entity = json.loads(
        (result.item_dir / "EntityTypes" / id_map.entity_id("Customer") / "definition.json").read_text()
    )
    names = [p["name"] for p in entity["properties"]]
    assert names[0] == "CustomerKey"
    assert names[1:] == sorted(names[1:])


def test_property_bindings_sorted_by_source_column(tmp_path):
    _ontology, id_map, result = _emit(CLEAN_TTL, tmp_path)
    binding_file = next(
        (result.item_dir / "EntityTypes" / id_map.entity_id("Customer") / "DataBindings").glob("*.json")
    )
    binding = json.loads(binding_file.read_text())
    columns = [b["sourceColumnName"] for b in binding["dataBindingConfiguration"]["propertyBindings"]]
    assert columns == sorted(columns)
    source = binding["dataBindingConfiguration"]["sourceTableProperties"]
    assert source["sourceType"] == "LakehouseTable"
    assert source["sourceTableName"] == "customer"
    assert source["sourceSchema"] == "dbo"
    assert source["itemId"] == "11111111-1111-1111-1111-111111111111"


def test_contextualization_shape(tmp_path):
    _ontology, id_map, result = _emit(CLEAN_TTL, tmp_path)
    ctx_file = next(
        (result.item_dir / "RelationshipTypes" / id_map.relationship_id("hasAddress") / "Contextualizations").glob("*.json")
    )
    ctx = json.loads(ctx_file.read_text())
    assert ctx["dataBindingTable"]["sourceTableName"] == "customer"
    assert ctx["sourceKeyRefBindings"][0]["sourceColumnName"] == "CustomerKey"
    assert ctx["targetKeyRefBindings"][0]["sourceColumnName"] == "MainAddressKey"
    assert ctx["targetKeyRefBindings"][0]["targetPropertyId"] == id_map.property_id("Address", "AddressKey")


def test_rebuild_is_byte_identical(tmp_path):
    first_dir = tmp_path / "a"
    second_dir = tmp_path / "b"
    _o1, _i1, first = _emit(CLEAN_TTL, first_dir)
    _o2, _i2, second = _emit(CLEAN_TTL, second_dir)
    for left in sorted(first.item_dir.rglob("*")):
        if left.is_dir():
            continue
        right = second.item_dir / left.relative_to(first.item_dir)
        assert right.read_bytes() == left.read_bytes(), left


def test_emit_cleans_the_previous_tree(tmp_path):
    ttl = CLEAN_TTL + "\nex:Region a owl:Class .\n"
    _o1, id_map, _first = _emit(ttl, tmp_path)
    region_dir = tmp_path / "Demo.Ontology" / "EntityTypes" / id_map.entity_id("Region")
    assert region_dir.exists()
    _o2, _i2, _second = _emit(CLEAN_TTL, tmp_path)
    assert not region_dir.exists()


def test_emit_refuses_to_overwrite_a_foreign_folder(tmp_path):
    target = tmp_path / "Demo.Ontology"
    target.mkdir()
    (target / "important.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(ValueError):
        _emit(CLEAN_TTL, tmp_path)
    assert (target / "important.txt").is_file()


def test_parameter_file_generation(tmp_path):
    config = _config()
    config.environments = {
        "dev": {"workspaceId": "33333333-3333-3333-3333-333333333333", "lakehouses": {"lh": "44444444-4444-4444-4444-444444444444"}}
    }
    ontology, id_map, _bag = build(CLEAN_TTL, name="Demo")
    result = emit(ontology, id_map, config, tmp_path)
    assert result.parameter_file is not None
    import yaml

    parameters = yaml.safe_load(result.parameter_file.read_text())
    finds = {entry["find_value"] for entry in parameters["find_replace"]}
    assert config.workspace_id in finds
    assert "11111111-1111-1111-1111-111111111111" in finds


def test_no_parameter_file_without_environments(tmp_path):
    _ontology, _ids, result = _emit(CLEAN_TTL, tmp_path)
    assert result.parameter_file is None


def test_entity_synonyms_emitted_as_a_json_array(tmp_path):
    _ontology, id_map, result = _emit(ENRICHED_TTL, tmp_path)
    entity_dir = result.item_dir / "EntityTypes" / id_map.entity_id("Customer")
    entity = json.loads((entity_dir / "definition.json").read_text())
    assert entity["semanticEnrichment"]["synonyms"] == ["Customer", "Client", "Account Holder"]


def test_entity_without_description_but_with_synonyms_still_emits_enrichment(tmp_path):
    _ontology, id_map, result = _emit(ENRICHED_TTL, tmp_path)
    entity_dir = result.item_dir / "EntityTypes" / id_map.entity_id("Customer")
    entity = json.loads((entity_dir / "definition.json").read_text())
    assert entity["semanticEnrichment"]["description"] is None


def test_entity_without_synonyms_or_description_has_no_enrichment(tmp_path):
    _ontology, id_map, result = _emit(ENRICHED_TTL, tmp_path)
    entity_dir = result.item_dir / "EntityTypes" / id_map.entity_id("Address")
    entity = json.loads((entity_dir / "definition.json").read_text())
    assert "semanticEnrichment" not in entity


def test_property_synonyms_emitted_as_a_comma_joined_string(tmp_path):
    _ontology, id_map, result = _emit(ENRICHED_TTL, tmp_path)
    entity_dir = result.item_dir / "EntityTypes" / id_map.entity_id("Customer")
    entity = json.loads((entity_dir / "definition.json").read_text())
    prop = next(p for p in entity["properties"] if p["name"] == "CustomerKey")
    assert prop["semanticEnrichment"]["customAttributes"]["synonyms"] == "Customer Id,Customer Key"


def test_fk_property_carries_alt_label_custom_attribute(tmp_path):
    _ontology, id_map, result = _emit(ENRICHED_TTL, tmp_path)
    entity_dir = result.item_dir / "EntityTypes" / id_map.entity_id("Customer")
    entity = json.loads((entity_dir / "definition.json").read_text())
    prop = next(p for p in entity["properties"] if p["name"] == "MainAddressKey")
    assert prop["semanticEnrichment"]["customAttributes"]["altLabel"] == "IsAddressOfCustomer"


def test_relationship_carries_alt_label_custom_attribute(tmp_path):
    _ontology, id_map, result = _emit(ENRICHED_TTL, tmp_path)
    relationship_dir = result.item_dir / "RelationshipTypes" / id_map.relationship_id("hasAddress")
    relationship = json.loads((relationship_dir / "definition.json").read_text())
    assert relationship["semanticEnrichment"]["customAttributes"]["altLabel"] == "IsAddressOfCustomer"
