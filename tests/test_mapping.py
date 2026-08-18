"""Value-type mapping, key resolution, relationship derivation, unbound behaviour."""

import pytest

from rdf2ontology.config import Config, ConfigError, load_config
from rdf2ontology.diagnostics import DiagnosticBag
from rdf2ontology.mapping import build_ontology, resolve_key, split_table, value_type_for_range
from rdf2ontology.rdf_model import GraphModel

from conftest import (
    CLEAN_TTL,
    DEFAULTS_CONFIG,
    ENRICHED_TTL,
    PREFIXES,
    SAMPLE_CONFIG,
    SAMPLE_OVERRIDES,
    build,
    custom_attributes_config,
    make_model,
)

XSD = "http://www.w3.org/2001/XMLSchema#"


@pytest.mark.parametrize(
    "range_iri,expected",
    [
        (f"{XSD}string", "String"),
        (f"{XSD}anyURI", "String"),
        (f"{XSD}boolean", "Boolean"),
        (f"{XSD}dateTime", "DateTime"),
        (f"{XSD}date", "DateTime"),
        (f"{XSD}integer", "BigInt"),
        (f"{XSD}long", "BigInt"),
        (f"{XSD}double", "Double"),
        (f"{XSD}decimal", "Double"),
        (f"{XSD}duration", None),
        (None, None),
    ],
)
def test_value_type_mapping(range_iri, expected):
    assert value_type_for_range(range_iri) == expected


def test_unmapped_range_defaults_to_string_with_a_warning():
    ttl = CLEAN_TTL + """
ex:Elapsed a owl:DatatypeProperty ; ex:sourceColumn "Elapsed" ;
    rdfs:domain ex:Customer ; rdfs:range xsd:duration .
"""
    ontology, _ids, bag = build(ttl)
    assert ontology.entities["Customer"].properties["Elapsed"].value_type == "String"
    assert "W2" in {d.rule for d in bag}


def test_value_type_override_wins():
    config = Config()
    config.value_type_overrides = {"CustomerName": "BigInt"}
    ontology, _ids, _bag = build(CLEAN_TTL, config)
    assert ontology.entities["Customer"].properties["CustomerName"].value_type == "BigInt"


def test_split_table():
    assert split_table("dbo.customer") == ("dbo", "customer")
    assert split_table("customer") == (None, "customer")


def test_key_from_restriction():
    model = make_model(CLEAN_TTL)
    key, rule, _candidates = resolve_key(model, Config(), "http://example.org/o#Customer")
    assert key == ["CustomerKey"]
    assert rule == "restriction"


def test_key_from_naming_convention():
    ttl = PREFIXES + """
ex:City a owl:Class .
ex:CityKey a owl:DatatypeProperty ; rdfs:domain ex:City ; rdfs:range xsd:string .
"""
    model = make_model(ttl)
    key, rule, _ = resolve_key(model, Config(), "http://example.org/o#City")
    assert key == ["CityKey"]
    assert rule == "naming-convention"


def test_key_from_config_wins():
    config = Config()
    config.entities = {"Customer": {"key": ["CustomerName"]}}
    model = make_model(CLEAN_TTL, config)
    key, rule, _ = resolve_key(model, config, "http://example.org/o#Customer")
    assert key == ["CustomerName"]
    assert rule == "config"


def test_keyless_unbound_class_is_a_warning_not_an_error():
    ttl = PREFIXES + "ex:Region a owl:Class .\n"
    ontology, _ids, bag = build(ttl)
    assert ontology.entities["Region"].key == []
    assert not bag.has_errors()
    assert "W9" in {d.rule for d in bag}


def test_keyless_bound_class_is_an_error():
    ttl = PREFIXES + """
ex:sourceTable a owl:AnnotationProperty .
ex:Region a owl:Class ; ex:sourceTable "dbo.region" .
ex:Label a owl:DatatypeProperty ; rdfs:domain ex:Region ; rdfs:range xsd:string .
"""
    _ontology, _ids, bag = build(ttl)
    assert any(d.rule == "E3" for d in bag.errors)


def test_key_from_value_matched_annotation():
    ttl = PREFIXES + """
ex:classification a owl:AnnotationProperty .
ex:City a owl:Class .
ex:City_CityKey a owl:DatatypeProperty ; rdfs:domain ex:City ; rdfs:range xsd:string ;
    ex:classification "Key" .
"""
    config = Config()
    config.rdf = {**config.rdf, "keyProperty": "classification", "keyValue": "Key"}
    model = make_model(ttl, config)
    key, rule, _ = resolve_key(model, config, "http://example.org/o#City")
    assert key == ["City_CityKey"]
    assert rule == "annotation"


def test_fk_column_derived_from_join_condition():
    ttl = PREFIXES + """
ex:joinCondition a owl:AnnotationProperty .
ex:Customer a owl:Class . ex:Address a owl:Class .
ex:Customer_HasAddress_Address a owl:ObjectProperty ;
    rdfs:domain ex:Customer ; rdfs:range ex:Address ;
    ex:joinCondition "customer__t.addressKey = address__t.addressKey" .
"""
    config = Config()
    config.rdf = {**config.rdf, "joinConditionProperty": "joinCondition"}
    model = make_model(ttl, config)
    prop = model.properties["http://example.org/o#Customer_HasAddress_Address"]
    assert prop.annotations["column"] == "addressKey"


def test_join_condition_does_not_override_explicit_source_column():
    ttl = PREFIXES + """
ex:sourceColumn a owl:AnnotationProperty .
ex:joinCondition a owl:AnnotationProperty .
ex:Customer a owl:Class . ex:Address a owl:Class .
ex:Customer_HasAddress_Address a owl:ObjectProperty ;
    rdfs:domain ex:Customer ; rdfs:range ex:Address ;
    ex:sourceColumn "MainAddressKey" ;
    ex:joinCondition "customer__t.addressKey = address__t.addressKey" .
"""
    config = Config()
    config.rdf = {**config.rdf, "joinConditionProperty": "joinCondition"}
    model = make_model(ttl, config)
    prop = model.properties["http://example.org/o#Customer_HasAddress_Address"]
    assert prop.annotations["column"] == "MainAddressKey"


def test_display_name_heuristic():
    ontology, _ids, _bag = build(CLEAN_TTL)
    assert ontology.entities["Customer"].display_name_property == "CustomerName"


def test_union_domain_property_lands_on_both_entities_with_distinct_ids():
    ttl = CLEAN_TTL + """
ex:Shared a owl:DatatypeProperty ; ex:sourceColumn "Shared" ; rdfs:range xsd:string ;
    rdfs:domain [ a owl:Class ; owl:unionOf ( ex:Customer ex:Address ) ] .
"""
    ontology, id_map, _bag = build(ttl)
    assert "Shared" in ontology.entities["Customer"].properties
    assert "Shared" in ontology.entities["Address"].properties
    assert id_map.property_id("Customer", "Shared") != id_map.property_id("Address", "Shared")


def test_self_referencing_relationship_is_dropped():
    ttl = CLEAN_TTL + """
ex:hasParent a owl:ObjectProperty ; ex:sourceColumn "ParentKey" ;
    rdfs:domain ex:Customer ; rdfs:range ex:Customer .
"""
    ontology, _ids, bag = build(ttl)
    assert "hasParent" not in {r.name for r in ontology.relationships}
    assert "W4" in {d.rule for d in bag}
    # the FK column survives as a queryable scalar
    assert "ParentKey" in ontology.entities["Customer"].properties


def test_inverse_relationship_is_dropped_by_default():
    ttl = CLEAN_TTL + """
ex:isAddressOf a owl:ObjectProperty ; owl:inverseOf ex:hasAddress ;
    rdfs:domain ex:Address ; rdfs:range ex:Customer .
"""
    ontology, _ids, _bag = build(ttl)
    names = {r.name for r in ontology.relationships}
    assert "hasAddress" in names
    assert "isAddressOf" not in names


def test_relationship_to_unbound_entity_is_kept_without_contextualization():
    ttl = CLEAN_TTL + """
ex:City a owl:Class .
ex:CityKey a owl:DatatypeProperty ; rdfs:domain ex:City ; rdfs:range xsd:string .
ex:locatedIn a owl:ObjectProperty ; ex:sourceColumn "CityKey" ;
    rdfs:domain ex:Customer ; rdfs:range ex:City .
"""
    ontology, _ids, _bag = build(ttl)
    relationship = next(r for r in ontology.relationships if r.name == "locatedIn")
    assert relationship.target_entity == "City"
    assert relationship.contextualization is not None  # City has a key, Customer is bound


def test_relationship_without_fk_column_has_no_contextualization():
    ttl = CLEAN_TTL + """
ex:relatesTo a owl:ObjectProperty ; rdfs:domain ex:Customer ; rdfs:range ex:Address .
"""
    ontology, _ids, bag = build(ttl)
    relationship = next(r for r in ontology.relationships if r.name == "relatesTo")
    assert relationship.contextualization is None
    assert "W5" in {d.rule for d in bag}


def test_union_domain_object_property_fans_out_with_disambiguated_names():
    ttl = CLEAN_TTL + """
ex:City a owl:Class .
ex:CityKey a owl:DatatypeProperty ; rdfs:domain ex:City ; rdfs:range xsd:string .
ex:locatedIn a owl:ObjectProperty ; ex:sourceColumn "CityKey" ; rdfs:range ex:City ;
    rdfs:domain [ a owl:Class ; owl:unionOf ( ex:Customer ex:Address ) ] .
"""
    ontology, _ids, _bag = build(ttl)
    names = {r.name for r in ontology.relationships}
    assert {"locatedInCustomerCity", "locatedInAddressCity"} <= names


def test_contextualization_matches_the_reference_shape():
    ontology, _ids, _bag = build(CLEAN_TTL)
    relationship = next(r for r in ontology.relationships if r.name == "hasAddress")
    ctx = relationship.contextualization
    assert (ctx.schema, ctx.table) == ("dbo", "customer")
    assert ctx.source_columns == ["CustomerKey"]
    assert ctx.target_columns == ["MainAddressKey"]


def test_emit_unbound_entities_can_be_turned_off():
    ttl = CLEAN_TTL + "\nex:Region a owl:Class .\n"
    config = Config()
    config.defaults["emitUnboundEntities"] = False
    ontology, _ids, _bag = build(ttl, config)
    assert "Region" not in ontology.entities


def test_foreign_key_properties_can_be_turned_off():
    config = Config()
    config.defaults["emitForeignKeyProperties"] = False
    ontology, _ids, _bag = build(CLEAN_TTL, config)
    assert "MainAddressKey" not in ontology.entities["Customer"].properties


def test_config_rejects_unknown_keys(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("entites:\n  Customer: {}\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_config_rejects_unknown_custom_attributes_key(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("customAttributes:\n  relationships: [label]\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_config_rejects_non_list_custom_attributes(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("customAttributes:\n  entities: label\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_config_rejects_unknown_entity_keys(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("entities:\n  Customer:\n    keys: [A]\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_class_synonyms_split_on_semicolon_and_comma():
    ontology, _ids, _bag = build(ENRICHED_TTL)
    assert ontology.entities["Customer"].synonyms == ["Customer", "Client", "Account Holder"]


def test_property_synonyms_split_on_semicolon_and_comma():
    ontology, _ids, _bag = build(ENRICHED_TTL)
    assert ontology.entities["Customer"].properties["CustomerKey"].synonyms == ["Customer Id", "Customer Key"]


def test_class_without_synonyms_annotation_has_none():
    ontology, _ids, _bag = build(ENRICHED_TTL)
    assert ontology.entities["Address"].synonyms == []


def test_object_property_alt_label_carried_onto_fk_property_and_relationship():
    ontology, _ids, _bag = build(ENRICHED_TTL)
    assert ontology.entities["Customer"].properties["MainAddressKey"].alt_label == "IsAddressOfCustomer"
    relationship = next(r for r in ontology.relationships if r.name == "hasAddress")
    assert relationship.custom_attributes == {"altLabel": "IsAddressOfCustomer"}


def test_no_custom_attributes_by_default():
    ontology, _ids, _bag = build(ENRICHED_TTL)
    assert ontology.entities["Customer"].custom_attributes == {}
    assert ontology.entities["Customer"].properties["CustomerKey"].custom_attributes == {}


def test_entity_custom_attributes_resolve_label_and_annotations():
    ontology, _ids, _bag = build(ENRICHED_TTL, custom_attributes_config())
    assert ontology.entities["Customer"].custom_attributes == {
        "label": "Customer",
        "classId": "119",
        "subjectArea": "Customer",
        "classType": "Master",
    }


def test_entity_custom_attributes_omit_absent_annotations():
    ontology, _ids, _bag = build(ENRICHED_TTL, custom_attributes_config())
    # Address has none of classId/subjectArea/classType and no rdfs:label.
    assert ontology.entities["Address"].custom_attributes == {}


def test_data_property_custom_attributes_resolve_label_and_domain():
    ontology, _ids, _bag = build(ENRICHED_TTL, custom_attributes_config())
    assert ontology.entities["Customer"].properties["CustomerKey"].custom_attributes == {
        "label": "Customer Key",
        "domain": "Customer",
        "dataPropertyId": "283",
        "classification": "Key",
        "mandatoryOptionalInd": "Mandatory",
    }


def test_fk_property_gets_description_and_object_property_custom_attributes():
    ontology, _ids, _bag = build(ENRICHED_TTL, custom_attributes_config())
    prop = ontology.entities["Customer"].properties["MainAddressKey"]
    assert prop.description == "Links a customer to their main address."
    assert prop.custom_attributes == {
        "label": "Customer has Main Address",
        "domain": "Customer",
        "range": "Address",
    }


def test_relationship_custom_attributes_include_domain_range_and_alt_label():
    ontology, _ids, _bag = build(ENRICHED_TTL, custom_attributes_config())
    relationship = next(r for r in ontology.relationships if r.name == "hasAddress")
    assert relationship.custom_attributes == {
        "label": "Customer has Main Address",
        "domain": "Customer",
        "range": "Address",
        "altLabel": "IsAddressOfCustomer",
    }


def test_synonyms_property_respects_annotation_namespace_scoping():
    config = Config()
    config.rdf = {**config.rdf, "annotationNamespace": "http://other.example.org/"}
    model = make_model(ENRICHED_TTL, config)
    assert model.classes["http://example.org/o#Customer"].synonyms == []


def test_synonyms_predicate_name_is_configurable():
    ttl = PREFIXES + """
ex:altNames a owl:AnnotationProperty .
ex:Customer a owl:Class ; ex:altNames "Customer; Client" .
"""
    config = Config()
    config.rdf = {**config.rdf, "synonymsProperty": "altNames"}
    model = make_model(ttl, config)
    assert model.classes["http://example.org/o#Customer"].synonyms == ["Customer", "Client"]


def test_sample_config_loads():
    config = load_config(SAMPLE_CONFIG)
    assert config.display_name == "Customer_Address"
    assert config.value_type_overrides["MVNOFlag"] == "BigInt"
    assert config.lint_ignore == []


def test_generic_defaults_file_has_no_ontology_specific_content():
    """The whole point: this file is reusable, unmodified, across any RDF file."""
    config = load_config(DEFAULTS_CONFIG)
    assert config.display_name is None
    assert config.entities == {}
    assert config.relationships == {}
    assert config.lakehouses == {}
    assert config.flag("emitForeignKeyProperties") is True


def test_config_files_merge_with_later_file_winning(tmp_path):
    base = tmp_path / "base.yaml"
    base.write_text("defaults:\n  maxPortalNameLength: 26\n  emitUnboundEntities: true\n", encoding="utf-8")
    override = tmp_path / "override.yaml"
    override.write_text("defaults:\n  maxPortalNameLength: 40\n", encoding="utf-8")

    config = load_config([base, override])
    assert config.flag("maxPortalNameLength") == 40  # override wins
    assert config.flag("emitUnboundEntities") is True  # base value preserved (dict merge)


def test_config_files_merge_entities_key_by_key(tmp_path):
    base = tmp_path / "base.yaml"
    base.write_text("entities:\n  Customer:\n    key: [CustomerKey]\n", encoding="utf-8")
    override = tmp_path / "override.yaml"
    override.write_text("entities:\n  Address:\n    key: [AddressKey]\n", encoding="utf-8")

    config = load_config([base, override])
    assert config.entity("Customer") == {"key": ["CustomerKey"]}
    assert config.entity("Address") == {"key": ["AddressKey"]}


def test_single_config_argument_still_works(tmp_path):
    path = tmp_path / "solo.yaml"
    path.write_text("ontology:\n  displayName: Solo\n", encoding="utf-8")
    assert load_config(path).display_name == "Solo"


def test_generic_and_overrides_files_together_reproduce_the_full_sample_config():
    combined = load_config([DEFAULTS_CONFIG, SAMPLE_OVERRIDES])
    assert combined.display_name == "Customer_Address"
    assert combined.flag("emitForeignKeyProperties") is True  # from the generic file
    assert combined.entity("Customer") == {"key": ["CustomerKey"], "displayNameProperty": "CustomerLegalName"}


def test_sample_ontology_shape(sample_ontology):
    ontology, _id_map, bag = sample_ontology
    assert not bag.has_errors(), [str(d) for d in bag.errors]
    assert len(ontology.entities) == 23
    assert len(ontology.bound_entities()) == 2
    assert len(ontology.unbound_entities()) == 21
    assert {e.name for e in ontology.bound_entities()} == {"Customer", "Address"}
    assert "hasAddress" in {r.name for r in ontology.relationships}
    assert ontology.entities["Customer"].key == ["CustomerKey"]
    assert ontology.entities["Address"].key == ["AddressKey"]
    assert ontology.entities["Customer"].properties["MVNOFlag"].value_type == "BigInt"
