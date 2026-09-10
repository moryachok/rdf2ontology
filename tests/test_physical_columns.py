"""--require-physical-tables: leave a property unbound (not dropped) when its physical column is missing."""

import json

from rdf2ontology.config import Config, LakehouseRef
from rdf2ontology.diagnostics import DiagnosticBag
from rdf2ontology.emit import emit
from rdf2ontology.fabric_tables import StaticTableIndex
from rdf2ontology.ids import IdMap
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

TABLES = {"lh1|dbo.customer", "lh1|dbo.address"}


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


def test_property_with_missing_column_stays_in_definition_but_is_left_unbound(tmp_path):
    table_index = StaticTableIndex(
        known=TABLES,
        columns={"lh1|dbo.customer": {"CustomerKey", "MainAddressKey"}, "lh1|dbo.address": {"AddressKey", "AddressName"}},
    )
    ontology, id_map, bag = _build(TTL, _config(), table_index)

    customer = ontology.entities["Customer"]
    assert customer.properties["CustomerName"].unbound_reason == "CustomerName"
    assert ontology.unbound_properties == {"Customer.CustomerName": "CustomerName"}
    assert any(d.rule == "W13" for d in bag.warnings)

    result = emit(ontology, id_map, _config(), tmp_path)
    entity_dir = result.item_dir / "EntityTypes" / id_map.entity_id("Customer")
    binding_file = next((entity_dir / "DataBindings").glob("*.json"))
    binding = json.loads(binding_file.read_text())
    bound_columns = {b["sourceColumnName"] for b in binding["dataBindingConfiguration"]["propertyBindings"]}
    assert "CustomerName" not in bound_columns
    assert "CustomerKey" in bound_columns

    entity_payload = json.loads((entity_dir / "definition.json").read_text())
    assert any(p["name"] == "CustomerName" for p in entity_payload["properties"])


def test_missing_key_column_is_a_hard_error():
    table_index = StaticTableIndex(
        known=TABLES,
        columns={"lh1|dbo.customer": {"CustomerName", "MainAddressKey"}, "lh1|dbo.address": {"AddressKey", "AddressName"}},
    )
    _ontology, _id_map, bag = _build(TTL, _config(), table_index)

    assert any(d.rule == "E16" for d in bag.errors)
    assert bag.has_errors()


def test_unverifiable_columns_leave_every_property_bound():
    # Tables are known but no column set is registered for them -> columns() returns None (fail open).
    table_index = StaticTableIndex(known=TABLES)
    ontology, _id_map, bag = _build(TTL, _config(), table_index)

    assert ontology.unbound_properties == {}
    assert not any(d.rule in ("W13", "E16") for d in bag.warnings + bag.errors)


def test_timeseries_block_dropped_when_timestamp_column_is_missing():
    config = _config()
    config.entities["Customer"] = {
        "timeseries": [{"table": "dbo.customer_events", "timestampColumn": "EventTime", "properties": ["CustomerName"]}]
    }
    table_index = StaticTableIndex(
        known=TABLES | {"lh1|dbo.customer_events"},
        columns={
            "lh1|dbo.customer": {"CustomerKey", "CustomerName", "MainAddressKey"},
            "lh1|dbo.address": {"AddressKey", "AddressName"},
            "lh1|dbo.customer_events": {"CustomerName"},  # no EventTime column
        },
    )
    ontology, _id_map, bag = _build(TTL, config, table_index)

    assert ontology.entities["Customer"].timeseries == []
    assert any(d.rule == "W13" and "timestamp column" in d.message for d in bag.warnings)


def test_timeseries_measure_column_missing_prunes_the_property_and_keeps_the_block():
    config = _config()
    config.entities["Customer"] = {
        "timeseries": [
            {"table": "dbo.customer_events", "timestampColumn": "EventTime", "properties": ["CustomerName"]}
        ]
    }
    table_index = StaticTableIndex(
        known=TABLES | {"lh1|dbo.customer_events"},
        columns={
            "lh1|dbo.customer": {"CustomerKey", "CustomerName", "MainAddressKey"},
            "lh1|dbo.address": {"AddressKey", "AddressName"},
            "lh1|dbo.customer_events": {"EventTime"},  # CustomerName column missing
        },
    )
    ontology, _id_map, bag = _build(TTL, config, table_index)

    # every measure column was missing -> the whole block is skipped, entity keeps the property unbound
    assert ontology.entities["Customer"].timeseries == []
    assert ontology.unbound_properties.get("Customer.CustomerName") == "CustomerName"
    assert any(d.rule == "W13" for d in bag.warnings)


def test_contextualization_dropped_when_a_key_ref_column_is_missing():
    config = _config()
    config.defaults["emitForeignKeyProperties"] = False
    table_index = StaticTableIndex(
        known=TABLES,
        columns={"lh1|dbo.customer": {"CustomerKey", "CustomerName"}, "lh1|dbo.address": {"AddressKey", "AddressName"}},
    )
    ontology, _id_map, bag = _build(TTL, config, table_index)

    relationship = next(r for r in ontology.relationships if r.name == "hasAddress")
    assert relationship.contextualization is None
    assert any(d.rule == "W13" and "link table" in d.message for d in bag.warnings)


def test_emitted_tree_still_validates_with_an_unbound_property(tmp_path):
    table_index = StaticTableIndex(
        known=TABLES,
        columns={"lh1|dbo.customer": {"CustomerKey", "MainAddressKey"}, "lh1|dbo.address": {"AddressKey", "AddressName"}},
    )
    ontology, id_map, _bag = _build(TTL, _config(), table_index)
    result = emit(ontology, id_map, _config(), tmp_path)

    structural = DiagnosticBag()
    validate_item_dir(result.item_dir, structural)
    assert not structural.has_errors()
