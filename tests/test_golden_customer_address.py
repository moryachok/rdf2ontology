"""P6: the Customer / Address / hasAddress subset must match the reference item."""

import pytest

from rdf2ontology.config import load_config
from rdf2ontology.diagnostics import DiagnosticBag
from rdf2ontology.diff import diff_items, load_item
from rdf2ontology.emit import emit
from rdf2ontology.ids import IdMap
from rdf2ontology.mapping import build_ontology

from conftest import REFERENCE_ITEM, SAMPLE_CONFIG

# The reference item models Address from the customer table, so it carries two extra
# unbound properties and a different display-name choice. Everything else must match.
EXPECTED_DIFFERENCES = {
    "~ displayName",
    "~ entityTypes.Address.displayNameProperty",
    "- entityTypes.Address.properties.MainAddressKey",
    "- entityTypes.Address.properties.StreetAddress",
}


@pytest.fixture(scope="module")
def generated_item(tmp_path_factory, sample_model=None):
    from conftest import SAMPLE_TTL
    from rdflib import Graph
    from rdf2ontology.rdf_model import GraphModel

    config = load_config(SAMPLE_CONFIG)
    graph = Graph()
    graph.parse(source=str(SAMPLE_TTL), format="turtle")
    model = GraphModel(graph, config)

    bag = DiagnosticBag(ignore=config.lint_ignore)
    ontology = build_ontology(model, config, "Customer_Address", bag)
    id_map = IdMap(ontology_name="Customer_Address", allow_new=True)
    id_map.assign(ontology, source_rdf=str(SAMPLE_TTL))
    out = tmp_path_factory.mktemp("golden")
    return emit(ontology, id_map, config, out).item_dir


def _paths(lines: list[str]) -> set[str]:
    return {line.split(":", 1)[0].strip() for line in lines}


def test_customer_address_subset_matches_the_reference(generated_item):
    lines = diff_items(
        REFERENCE_ITEM,
        generated_item,
        ignore_ids=True,
        ignore_enrichment=True,
        subset=["Customer", "Address"],
    )
    assert _paths(lines) == EXPECTED_DIFFERENCES, "\n".join(lines)


def test_customer_properties_and_binding_match_exactly(generated_item):
    reference = load_item(REFERENCE_ITEM, ignore_ids=True, ignore_enrichment=True)["entityTypes"]["Customer"]
    generated = load_item(generated_item, ignore_ids=True, ignore_enrichment=True)["entityTypes"]["Customer"]
    assert generated["properties"] == reference["properties"]
    assert generated["key"] == reference["key"]
    assert generated["displayNameProperty"] == reference["displayNameProperty"]
    assert generated["bindings"] == reference["bindings"]


def test_relationship_and_contextualization_match(generated_item):
    reference = load_item(REFERENCE_ITEM, ignore_ids=True, ignore_enrichment=True)["relationshipTypes"]["hasAddress"]
    generated = load_item(generated_item, ignore_ids=True, ignore_enrichment=True)["relationshipTypes"]["hasAddress"]
    assert generated["source"] == reference["source"] == "Customer"
    assert generated["target"] == reference["target"] == "Address"
    assert generated["contextualizations"] == reference["contextualizations"]


def test_diff_of_an_item_against_itself_is_empty():
    assert diff_items(REFERENCE_ITEM, REFERENCE_ITEM, ignore_ids=False) == []
