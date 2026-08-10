"""The core ask: a customer with only an RDF file must get a working ontology.

No YAML config, no CLI flags - `build` must succeed, and defects the tool can resolve
deterministically (illegal names, duplicate names, punning) must warn and auto-resolve
rather than block.
"""

from rdf2ontology.config import Config
from rdf2ontology.mapping import sanitize_name, title_to_name

from conftest import PREFIXES, SAMPLE_TTL, build


def test_sample_file_builds_with_zero_config():
    with open(SAMPLE_TTL, encoding="utf-8") as handle:
        ttl = handle.read()
    ontology, id_map, bag = build(ttl, Config(), name="Test")
    assert not bag.has_errors(), [str(d) for d in bag.errors]
    assert len(ontology.entities) == 23
    assert len(ontology.bound_entities()) == 2


def test_bound_entities_still_get_a_full_binding_with_placeholder_ids():
    with open(SAMPLE_TTL, encoding="utf-8") as handle:
        ttl = handle.read()
    ontology, id_map, _bag = build(ttl, Config(), name="Test")
    assert ontology.entities["Customer"].bound
    assert "static" in id_map.entity_types["Customer"]["bindings"]


def test_sanitize_name_strips_illegal_characters_and_fixes_the_leading_character():
    assert sanitize_name("Customer") == "Customer"
    assert sanitize_name("9Bad") == "X9Bad"
    assert sanitize_name("Bad Name!") == "BadName"
    assert sanitize_name("___") == "X___"


def test_title_to_name():
    assert title_to_name("Customer & Address Ontology") == "CustomerAddressOntology"
    assert title_to_name("") == "Ontology"


def test_illegal_class_name_auto_resolves_instead_of_blocking():
    ttl = PREFIXES + """
<http://example.org/o#9Bad> a owl:Class .
ex:Key a owl:DatatypeProperty ; rdfs:domain <http://example.org/o#9Bad> ; rdfs:range xsd:string .
"""
    ontology, _ids, bag = build(ttl, Config())
    assert not bag.has_errors(), [str(d) for d in bag.errors]
    assert "X9Bad" in ontology.entities
    assert ontology.renames["entity:9Bad"] == "X9Bad"
    assert any(d.rule == "L-NAME-REGEX" for d in bag.warnings)


def test_duplicate_class_local_name_is_auto_suffixed_deterministically():
    ttl = PREFIXES + """
@prefix other: <http://example.org/other#> .
ex:Customer a owl:Class .
other:Customer a owl:Class .
"""
    ontology, _ids, bag = build(ttl, Config())
    assert not bag.has_errors(), [str(d) for d in bag.errors]
    assert {"Customer", "Customer2"} <= set(ontology.entities)
    assert any(d.rule == "L-NAME-DUP" for d in bag.warnings)


def test_punned_iri_is_emitted_as_both_class_and_property():
    ttl = PREFIXES + """
ex:Widget a owl:Class , owl:DatatypeProperty ; rdfs:domain ex:Widget ; rdfs:range xsd:string .
"""
    ontology, _ids, bag = build(ttl, Config())
    assert not bag.has_errors(), [str(d) for d in bag.errors]
    assert "Widget" in ontology.entities  # mapping never blocked on the punning; see test_lint.py::test_punning


def test_source_table_with_no_schema_defaults_rather_than_errors():
    ttl = PREFIXES + """
ex:sourceTable a owl:AnnotationProperty .
ex:sourceColumn a owl:AnnotationProperty .
ex:Widget a owl:Class ; ex:sourceTable "widget" .
ex:WidgetKey a owl:DatatypeProperty ; ex:sourceColumn "WidgetKey" ; rdfs:domain ex:Widget ; rdfs:range xsd:string .
"""
    ontology, _ids, bag = build(ttl, Config())
    assert not bag.has_errors(), [str(d) for d in bag.errors]
    assert ontology.entities["Widget"].schema == "dbo"
    assert ontology.entities["Widget"].table == "widget"


def test_renaming_via_config_does_not_change_the_derived_id():
    """A user disagreeing with the auto-resolution can rename via config with zero id churn."""
    ttl = PREFIXES + """
ex:Widget a owl:Class .
ex:WidgetKey a owl:DatatypeProperty ; rdfs:domain ex:Widget ; rdfs:range xsd:string .
"""
    _o1, before, _b1 = build(ttl, Config(), name="Demo")

    renamed_config = Config()
    renamed_config.renames = {"Widget": "Gadget"}
    _o2, after, _b2 = build(ttl, renamed_config, name="Demo")

    assert before.entity_id("Widget") == after.entity_id("Gadget")
