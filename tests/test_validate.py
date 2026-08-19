"""Every documented error class must be caught before anything reaches Fabric."""

import json

from rdf2ontology.config import Config, LakehouseRef
from rdf2ontology.diagnostics import DiagnosticBag
from rdf2ontology.emit import emit
from rdf2ontology.ids import IdMap
from rdf2ontology.ir import (
    ContextualizationIR,
    EntityIR,
    OntologyIR,
    PropertyIR,
    RelationshipIR,
    TimeSeriesBindingIR,
)
from rdf2ontology.validate import validate_item_dir, validate_ontology

from conftest import CLEAN_TTL, PHYSICAL_TTL, PREFIXES, build


def _config(**defaults) -> Config:
    config = Config()
    config.lakehouses = {"lh": LakehouseRef(name="lh", item_id="11111111-1111-1111-1111-111111111111")}
    config.workspace_id = "22222222-2222-2222-2222-222222222222"
    config.defaults.update(defaults)
    return config


def _entity(name="Customer", bound=True, **kwargs) -> EntityIR:
    entity = EntityIR(
        name=name,
        iri=f"http://example.org/o#{name}",
        lakehouse="lh" if bound else None,
        schema="dbo" if bound else None,
        table="customer" if bound else None,
        **kwargs,
    )
    return entity


def _validate(ontology: OntologyIR, config: Config = None) -> DiagnosticBag:
    config = config or _config()
    id_map = IdMap(ontology_name=ontology.name, allow_new=True)
    id_map.assign(ontology)
    bag = DiagnosticBag()
    validate_ontology(ontology, id_map, config, bag)
    return bag


def _rules(bag: DiagnosticBag) -> set[str]:
    return {d.rule for d in bag.errors}


def _simple(bound=True) -> OntologyIR:
    entity = _entity(bound=bound)
    entity.properties["CustomerKey"] = PropertyIR("CustomerKey", "iri", "String", "CustomerKey")
    entity.key = ["CustomerKey"]
    return OntologyIR(name="Demo", entities={"Customer": entity})


def test_baseline_is_clean():
    assert not _validate(_simple()).has_errors()


def test_e1_invalid_name():
    ontology = _simple()
    ontology.entities["Customer"].name = "9Bad"
    assert "E1" in _rules(_validate(ontology))


def test_e2_invalid_value_type():
    ontology = _simple()
    ontology.entities["Customer"].properties["CustomerKey"].value_type = "Int64"
    assert "E2" in _rules(_validate(ontology))


def test_e3_bound_entity_without_key():
    ontology = _simple()
    ontology.entities["Customer"].key = []
    assert "E3" in _rules(_validate(ontology))


def test_e3_key_with_unsupported_value_type():
    ontology = _simple()
    ontology.entities["Customer"].properties["CustomerKey"].value_type = "Double"
    assert "E3" in _rules(_validate(ontology))


def test_e4_conflicting_value_types_across_entities():
    ontology = _simple()
    other = _entity(name="Address", bound=False)
    other.properties["CustomerKey"] = PropertyIR("CustomerKey", "iri", "BigInt", "CustomerKey")
    ontology.entities["Address"] = other
    assert "E4" in _rules(_validate(ontology))


def test_physical_data_property_name_conflict_is_resolved_before_validation():
    """mapping.py falls back to RDF local names on conflict, so validate never has to raise E4 for it."""
    ontology, _ids, mapping_bag = build(PHYSICAL_TTL)
    assert "W12" in {d.rule for d in mapping_bag}
    assert "E4" not in _rules(_validate(ontology))


def test_e5_name_in_both_property_arrays():
    ontology = _simple()
    entity = ontology.entities["Customer"]
    entity.properties["Reading"] = PropertyIR("Reading", "iri", "Double", "Reading")
    duplicate = PropertyIR("Reading", "iri", "Double", "Reading", timeseries=True)
    entity.properties["ReadingTs"] = duplicate
    duplicate.name = "Reading"
    assert "E5" in _rules(_validate(ontology))


def test_e6_self_referencing_relationship():
    ontology = _simple()
    ontology.relationships.append(RelationshipIR("loops", "iri", "Customer", "Customer"))
    assert "E6" in _rules(_validate(ontology))


def test_e6_relationship_to_unknown_entity():
    ontology = _simple()
    ontology.relationships.append(RelationshipIR("dangling", "iri", "Customer", "Ghost"))
    assert "E6" in _rules(_validate(ontology))


def test_e7_duplicate_relationship_name():
    ontology = _simple()
    ontology.entities["Address"] = _entity(name="Address", bound=False)
    ontology.relationships.append(RelationshipIR("dup", "iri", "Customer", "Address"))
    ontology.relationships.append(RelationshipIR("dup", "iri2", "Customer", "Address"))
    assert "E7" in _rules(_validate(ontology))


def test_e8_timeseries_without_static_binding():
    ontology = _simple(bound=False)
    entity = ontology.entities["Customer"]
    entity.properties["Reading"] = PropertyIR("Reading", "iri", "Double", "Reading", timeseries=True)
    entity.properties["Ts"] = PropertyIR("Ts", "iri", "DateTime", "Ts", timeseries=True)
    entity.timeseries.append(TimeSeriesBindingIR("dbo", "readings", "Ts", ["Reading", "Ts"]))
    assert "E8" in _rules(_validate(ontology))


def test_e9_timestamp_column_must_be_datetime():
    ontology = _simple()
    entity = ontology.entities["Customer"]
    entity.properties["Ts"] = PropertyIR("Ts", "iri", "String", "Ts", timeseries=True)
    entity.timeseries.append(TimeSeriesBindingIR("dbo", "readings", "Ts", ["Ts"]))
    assert "E9" in _rules(_validate(ontology))


def test_e9_unknown_timestamp_column():
    ontology = _simple()
    ontology.entities["Customer"].timeseries.append(TimeSeriesBindingIR("dbo", "readings", "Missing", []))
    assert "E9" in _rules(_validate(ontology))


def test_e10_duplicate_ids():
    ontology = _simple()
    id_map = IdMap(ontology_name="Demo", allow_new=True)
    id_map.assign(ontology)
    id_map.entity_types["Customer"]["properties"]["CustomerKey"]["id"] = id_map.entity_types["Customer"]["id"]
    bag = DiagnosticBag()
    validate_ontology(ontology, id_map, _config(), bag)
    assert "E10" in _rules(bag)


def test_e10_non_numeric_id():
    ontology = _simple()
    id_map = IdMap(ontology_name="Demo", allow_new=True)
    id_map.assign(ontology)
    id_map.entity_types["Customer"]["id"] = "not-a-number"
    bag = DiagnosticBag()
    validate_ontology(ontology, id_map, _config(), bag)
    assert "E10" in _rules(bag)


def test_e11_unknown_display_name_property():
    ontology = _simple()
    ontology.entities["Customer"].display_name_property = "Nope"
    assert "E11" in _rules(_validate(ontology))


def test_w7_missing_lakehouse_entry_is_a_warning_not_an_error():
    ontology = _simple()
    config = _config()
    config.lakehouses = {}
    bag = _validate(ontology, config)
    assert "E11" not in _rules(bag)
    assert any(d.rule == "W7" for d in bag.warnings)


def test_e12_contextualization_outside_the_key():
    ontology = _simple()
    address = _entity(name="Address", bound=False)
    address.properties["AddressKey"] = PropertyIR("AddressKey", "iri", "String", "AddressKey")
    address.properties["Other"] = PropertyIR("Other", "iri", "String", "Other")
    address.key = ["AddressKey"]
    ontology.entities["Address"] = address
    relationship = RelationshipIR("hasAddress", "iri", "Customer", "Address")
    relationship.contextualization = ContextualizationIR(
        lakehouse="lh",
        schema="dbo",
        table="customer",
        source_columns=["CustomerKey"],
        source_properties=["CustomerKey"],
        target_columns=["Other"],
        target_properties=["Other"],
    )
    ontology.relationships.append(relationship)
    assert "E12" in _rules(_validate(ontology))


def test_e15_config_references_unknown_entity():
    config = _config()
    config.entities = {"Ghost": {"key": ["GhostKey"]}}
    assert "E15" in _rules(_validate(_simple(), config))


def test_e15_config_references_unknown_relationship():
    config = _config()
    config.relationships = {"ghostRel": {"linkTable": "dbo.x"}}
    assert "E15" in _rules(_validate(_simple(), config))


def test_e14_punning_is_reported_by_lint_as_a_warning():
    ttl = CLEAN_TTL + "\nex:AddressName a owl:Class .\n"
    from rdf2ontology.lint import lint
    from conftest import make_model

    bag = DiagnosticBag()
    lint(make_model(ttl), Config(), bag)
    assert "L-PUN" in {d.rule for d in bag.warnings}


def test_warnings_w1_w7_w8():
    ontology = _simple()
    entity = ontology.entities["Customer"]
    entity.properties["AVeryLongPropertyNameBeyondTheLimit"] = PropertyIR(
        "AVeryLongPropertyNameBeyondTheLimit", "iri", "String", "Legacy Column"
    )
    config = _config()
    config.lakehouses["lh"].item_id = "00000000-0000-0000-0000-000000000000"
    config.workspace_id = "00000000-0000-0000-0000-000000000000"
    bag = _validate(ontology, config)
    rules = {d.rule for d in bag.warnings}
    assert {"W1", "W7", "W8"} <= rules


# -- structural validation of an emitted tree ------------------------------
def _emit_sample(tmp_path):
    ontology, id_map, _bag = build(CLEAN_TTL, name="Demo")
    config = _config()
    result = emit(ontology, id_map, config, tmp_path)
    return result.item_dir


def test_emitted_tree_validates(tmp_path):
    bag = validate_item_dir(_emit_sample(tmp_path))
    assert not bag.has_errors(), [str(d) for d in bag.errors]


def test_definition_json_must_be_empty(tmp_path):
    item_dir = _emit_sample(tmp_path)
    (item_dir / "definition.json").write_text('{"x": 1}', encoding="utf-8")
    assert "E11" in _rules(validate_item_dir(item_dir))


def test_platform_type_must_be_ontology(tmp_path):
    item_dir = _emit_sample(tmp_path)
    platform = json.loads((item_dir / ".platform").read_text(encoding="utf-8"))
    platform["metadata"]["type"] = "Notebook"
    (item_dir / ".platform").write_text(json.dumps(platform), encoding="utf-8")
    assert "E11" in _rules(validate_item_dir(item_dir))


def test_e13_kusto_table_cannot_back_a_static_binding(tmp_path):
    item_dir = _emit_sample(tmp_path)
    binding_file = next((item_dir / "EntityTypes").glob("*/DataBindings/*.json"))
    binding = json.loads(binding_file.read_text(encoding="utf-8"))
    binding["dataBindingConfiguration"]["sourceTableProperties"]["sourceType"] = "KustoTable"
    binding_file.write_text(json.dumps(binding), encoding="utf-8")
    assert "E13" in _rules(validate_item_dir(item_dir))


def test_file_level_duplicate_property_name(tmp_path):
    item_dir = _emit_sample(tmp_path)
    entity_file = next((item_dir / "EntityTypes").glob("*/definition.json"))
    entity = json.loads(entity_file.read_text(encoding="utf-8"))
    entity["properties"].append(dict(entity["properties"][0], id="999999999999999999"))
    entity_file.write_text(json.dumps(entity), encoding="utf-8")
    assert "E5" in _rules(validate_item_dir(item_dir))
