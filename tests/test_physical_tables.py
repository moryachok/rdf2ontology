"""--require-physical-tables: drop entity/relationship types whose declared table is missing."""

from rdf2ontology.config import Config, LakehouseRef
from rdf2ontology.diagnostics import DiagnosticBag, Severity
from rdf2ontology.emit import emit
from rdf2ontology.fabric_tables import StaticTableIndex
from rdf2ontology.ids import IdMap
from rdf2ontology.lint import lint
from rdf2ontology.mapping import build_ontology
from rdf2ontology.validate import validate_item_dir

from conftest import PREFIXES, make_model

LAKEHOUSE = LakehouseRef(name="lh1", item_id="11111111-1111-1111-1111-111111111111", workspace_id="ws1", default_schema="dbo")

TTL = PREFIXES + """
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


def _config() -> Config:
    config = Config()
    config.lakehouses = {"lh1": LAKEHOUSE}
    return config


def _build(ttl: str, config: Config, table_index):
    bag = DiagnosticBag()
    ontology = build_ontology(make_model(ttl, config), config, "TestOntology", bag, table_index)
    id_map = IdMap(ontology_name="TestOntology", allow_new=True)
    id_map.assign(ontology)
    return ontology, id_map, bag


def test_entity_with_missing_table_is_dropped():
    table_index = StaticTableIndex(known={"lh1|dbo.customer"})  # address is not in the known set
    ontology, _id_map, bag = _build(TTL, _config(), table_index)

    assert "Address" not in ontology.entities
    assert "Customer" in ontology.entities
    assert ontology.skipped_entities == {"Address": "dbo.address"}
    assert any(d.rule == "W3c" for d in bag.warnings)


def test_relationship_to_a_dropped_entity_is_also_dropped():
    table_index = StaticTableIndex(known={"lh1|dbo.customer"})
    ontology, _id_map, _bag = _build(TTL, _config(), table_index)

    assert ontology.relationships == []


def test_missing_link_table_degrades_to_no_contextualization():
    config = _config()
    config.relationships["hasAddress"] = {"linkTable": "dbo.customer_address_link"}
    table_index = StaticTableIndex(known={"lh1|dbo.customer", "lh1|dbo.address"})  # link table is missing
    ontology, _id_map, bag = _build(TTL, config, table_index)

    relationship = next(r for r in ontology.relationships if r.name == "hasAddress")
    assert relationship.contextualization is None
    assert any(d.rule == "W3c" and "link table" in d.message for d in bag.warnings)


def test_missing_link_table_drops_the_relationship_when_unbound_relationships_disabled():
    config = _config()
    config.relationships["hasAddress"] = {"linkTable": "dbo.customer_address_link"}
    config.defaults["emitUnboundRelationships"] = False
    table_index = StaticTableIndex(known={"lh1|dbo.customer", "lh1|dbo.address"})
    ontology, _id_map, _bag = _build(TTL, config, table_index)

    assert ontology.relationships == []


def test_timeseries_binding_on_a_missing_table_is_dropped_but_entity_survives():
    config = _config()
    config.entities["Customer"] = {
        "timeseries": [{"table": "dbo.customer_events", "timestampColumn": "CustomerName", "properties": []}]
    }
    table_index = StaticTableIndex(known={"lh1|dbo.customer", "lh1|dbo.address"})  # customer_events is missing
    ontology, _id_map, bag = _build(TTL, config, table_index)

    assert "Customer" in ontology.entities
    assert ontology.entities["Customer"].timeseries == []
    assert any(d.rule == "W3c" and "timeseries" in d.message for d in bag.warnings)


def test_unverifiable_lakehouse_keeps_the_entity_and_only_infos():
    table_index = StaticTableIndex(unverifiable_lakehouses={"lh1"})
    ontology, _id_map, bag = _build(TTL, _config(), table_index)

    assert {"Customer", "Address"} <= set(ontology.entities)
    assert not any(d.rule.startswith("W3") for d in bag.warnings + bag.errors)


def test_lint_reports_missing_and_unverifiable_tables():
    config = _config()
    missing_bag = DiagnosticBag()
    lint(make_model(TTL, config), config, missing_bag, StaticTableIndex(known={"lh1|dbo.customer"}))
    assert any(d.rule == "L-SRC-TABLE-MISSING" for d in missing_bag.warnings)

    unverifiable_bag = DiagnosticBag()
    lint(make_model(TTL, config), config, unverifiable_bag, StaticTableIndex(unverifiable_lakehouses={"lh1"}))
    assert any(d.rule == "L-SRC-TABLE-UNVERIFIED" for d in unverifiable_bag.of(Severity.INFO))


def test_emitted_tree_still_validates_when_an_end_is_dropped(tmp_path):
    table_index = StaticTableIndex(known={"lh1|dbo.customer"})
    ontology, id_map, _bag = _build(TTL, _config(), table_index)
    config = _config()
    result = emit(ontology, id_map, config, tmp_path)

    structural = DiagnosticBag()
    validate_item_dir(result.item_dir, structural)
    assert not structural.has_errors()


def test_id_map_retains_the_id_of_a_skipped_entity_across_a_rebuild():
    config = _config()

    present = StaticTableIndex(known={"lh1|dbo.customer", "lh1|dbo.address"})
    ontology, id_map, _bag = _build(TTL, config, present)
    address_id = id_map.entity_id("Address")

    missing = StaticTableIndex(known={"lh1|dbo.customer"})
    bag = DiagnosticBag()
    ontology = build_ontology(make_model(TTL, config), config, "TestOntology", bag, missing)
    id_map.assign(ontology)
    assert "Address" not in ontology.entities
    assert id_map.entity_types["Address"]["id"] == address_id

    ontology = build_ontology(make_model(TTL, config), config, "TestOntology", DiagnosticBag(), present)
    id_map.assign(ontology)
    assert id_map.entity_id("Address") == address_id
