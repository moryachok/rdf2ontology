"""One positive and one negative case per lint rule."""

from rdf2ontology.config import Config, load_config
from rdf2ontology.diagnostics import DiagnosticBag, Severity
from rdf2ontology.lint import RULES, lint
from rdf2ontology.rdf_model import RdfParseError, load_graph

from conftest import CLEAN_TTL, PREFIXES, SAMPLE_TTL, lint_rules, make_model


def test_clean_graph_is_quiet():
    rules = lint_rules(CLEAN_TTL)
    assert rules == set(), f"unexpected diagnostics on a clean graph: {sorted(rules)}"


def test_parse_error_is_reported():
    import pytest

    with pytest.raises(RdfParseError):
        load_graph(SAMPLE_TTL.parent / "does-not-exist.ttl", Config())


def test_punning():
    ttl = CLEAN_TTL + "\nex:AddressName a owl:Class .\n"
    assert "L-PUN" in lint_rules(ttl)


def test_undeclared_domain():
    ttl = CLEAN_TTL + """
ex:Ghost a owl:DatatypeProperty ; rdfs:domain ex:Nowhere ; rdfs:range xsd:string .
"""
    assert "L-UNDECLARED" in lint_rules(ttl)


def test_domain_missing():
    ttl = CLEAN_TTL + "\nex:Loose a owl:DatatypeProperty ; rdfs:range xsd:string .\n"
    assert "L-DOMAIN-MISSING" in lint_rules(ttl)


def test_domain_multi():
    ttl = CLEAN_TTL + """
ex:Shared a owl:DatatypeProperty ; rdfs:range xsd:string ;
    rdfs:domain [ a owl:Class ; owl:unionOf ( ex:Customer ex:Address ) ] .
"""
    assert "L-DOMAIN-MULTI" in lint_rules(ttl)


def test_range_missing():
    ttl = CLEAN_TTL + "\nex:NoRange a owl:DatatypeProperty ; rdfs:domain ex:Customer .\n"
    assert "L-RANGE-MISSING" in lint_rules(ttl)


def test_range_unmapped():
    ttl = CLEAN_TTL + """
ex:Elapsed a owl:DatatypeProperty ; rdfs:domain ex:Customer ; rdfs:range xsd:duration .
"""
    assert "L-RANGE-UNMAPPED" in lint_rules(ttl)


def test_range_conflict():
    ttl = PREFIXES + """
ex:A a owl:Class . ex:B a owl:Class .
ex:Amount a owl:DatatypeProperty ; rdfs:domain ex:A ; rdfs:range xsd:string , xsd:integer .
"""
    assert "L-RANGE-CONFLICT" in lint_rules(ttl)


def test_name_regex():
    ttl = PREFIXES + """
<http://example.org/o#9Bad> a owl:Class .
"""
    assert "L-NAME-REGEX" in lint_rules(ttl)


def test_name_length():
    ttl = PREFIXES + """
ex:AClassNameWayBeyondThePortalLimit a owl:Class .
"""
    assert "L-NAME-LENGTH" in lint_rules(ttl)


def test_name_duplicate():
    ttl = PREFIXES + """
@prefix other: <http://example.org/other#> .
ex:Customer a owl:Class .
other:Customer a owl:Class .
"""
    assert "L-NAME-DUP" in lint_rules(ttl)


def test_selfloop():
    ttl = CLEAN_TTL + """
ex:hasParent a owl:ObjectProperty ; ex:sourceColumn "ParentKey" ;
    rdfs:domain ex:Customer ; rdfs:range ex:Customer .
"""
    assert "L-SELFLOOP" in lint_rules(ttl)


def test_inverse():
    ttl = CLEAN_TTL + """
ex:isAddressOf a owl:ObjectProperty ; owl:inverseOf ex:hasAddress ;
    ex:sourceColumn "MainAddressKey" ; rdfs:domain ex:Address ; rdfs:range ex:Customer .
"""
    assert "L-INVERSE" in lint_rules(ttl)


def test_unsupported_class_expression():
    ttl = CLEAN_TTL + """
ex:Weird a owl:DatatypeProperty ; rdfs:range xsd:string ;
    rdfs:domain [ a owl:Class ; owl:intersectionOf ( ex:Customer ex:Address ) ] .
"""
    assert "L-CLASSEXPR" in lint_rules(ttl)


def test_source_table_missing_and_format():
    missing = PREFIXES + "ex:Loose a owl:Class .\n"
    assert "L-SRC-TABLE" in lint_rules(missing)

    bad_format = PREFIXES + """
ex:sourceTable a owl:AnnotationProperty .
ex:Loose a owl:Class ; ex:sourceTable "customer" .
"""
    assert "L-SRC-TABLE-FORMAT" in lint_rules(bad_format)


def test_source_lakehouse_without_config_entry():
    ttl = CLEAN_TTL + """
ex:sourceLakehouse a owl:AnnotationProperty .
ex:Customer ex:sourceLakehouse "not_configured" .
"""
    config = Config()
    config.lakehouses = {"configured": None}
    assert "L-SRC-LAKEHOUSE" in lint_rules(ttl, config)


def test_source_column_missing_and_unsafe():
    ttl = PREFIXES + """
ex:sourceTable a owl:AnnotationProperty .
ex:sourceColumn a owl:AnnotationProperty .
ex:Customer a owl:Class ; ex:sourceTable "dbo.customer" .
ex:CustomerKey a owl:DatatypeProperty ; rdfs:domain ex:Customer ; rdfs:range xsd:string .
ex:Spaced a owl:DatatypeProperty ; ex:sourceColumn "Customer Key" ;
    rdfs:domain ex:Customer ; rdfs:range xsd:string .
"""
    rules = lint_rules(ttl)
    assert "L-SRC-COLUMN" in rules
    assert "L-SRC-COLUMN-UNSAFE" in rules


def test_key_missing():
    ttl = PREFIXES + "ex:Region a owl:Class .\n"
    assert "L-KEY" in lint_rules(ttl)


def test_fk_missing():
    ttl = PREFIXES + """
ex:A a owl:Class . ex:B a owl:Class .
ex:links a owl:ObjectProperty ; rdfs:domain ex:A ; rdfs:range ex:B .
"""
    assert "L-FK-MISSING" in lint_rules(ttl)


def test_orphan():
    ttl = PREFIXES + "ex:Lonely a owl:Class .\n"
    assert "L-ORPHAN" in lint_rules(ttl)


def test_restriction_outside_domain():
    ttl = CLEAN_TTL + """
ex:Address rdfs:subClassOf
    [ a owl:Restriction ; owl:onProperty ex:CustomerKey ; owl:cardinality "1"^^xsd:nonNegativeInteger ] .
"""
    assert "L-RESTRICTION" in lint_rules(ttl)


def test_every_rule_is_documented():
    documented = set(RULES)
    exercised = {
        "L-PARSE", "L-PUN", "L-UNDECLARED", "L-DOMAIN-MISSING", "L-DOMAIN-MULTI",
        "L-RANGE-MISSING", "L-RANGE-UNMAPPED", "L-RANGE-CONFLICT", "L-NAME-REGEX",
        "L-NAME-LENGTH", "L-NAME-DUP", "L-SELFLOOP", "L-INVERSE", "L-CLASSEXPR",
        "L-SRC-TABLE", "L-SRC-TABLE-FORMAT", "L-SRC-TABLE-MISSING", "L-SRC-TABLE-UNVERIFIED",
        "L-SRC-LAKEHOUSE", "L-SRC-COLUMN", "L-SRC-COLUMN-UNSAFE", "L-KEY", "L-FK-MISSING",
        "L-ORPHAN", "L-RESTRICTION",
    }
    assert documented == exercised


def test_ignore_and_strict_policies():
    ttl = PREFIXES + """
ex:A a owl:Class . ex:B a owl:Class .
ex:Amount a owl:DatatypeProperty ; rdfs:domain ex:A ; rdfs:range xsd:string , xsd:integer .
"""
    bag = DiagnosticBag(ignore=["L-RANGE-CONFLICT"])
    lint(make_model(ttl), Config(), bag)
    assert not bag.has_errors()
    assert bag.counts()["ignored"] >= 1

    strict = DiagnosticBag(strict=True)
    lint(make_model(PREFIXES + "ex:Region a owl:Class .\n"), Config(), strict)
    assert strict.has_errors(), "strict mode must promote warnings to errors"


def test_sample_file_expected_diagnostics(sample_model, sample_config):
    bag = DiagnosticBag(ignore=sample_config.lint_ignore)
    lint(sample_model, sample_config, bag)

    by_rule: dict[str, int] = {}
    for diag in bag:
        by_rule[diag.rule] = by_rule.get(diag.rule, 0) + 1

    assert not bag.has_errors(), [str(d) for d in bag.errors]
    assert by_rule["L-SRC-TABLE"] == 21
    assert by_rule["L-KEY"] == 16
    assert by_rule["L-SELFLOOP"] == 3
    assert by_rule["L-INVERSE"] == 2
    assert by_rule["L-DOMAIN-MULTI"] == 8
    assert by_rule["L-PUN"] == 1  # cao:ZipCode punning - a warning, no ignore needed


def test_sample_file_lints_clean_with_no_config_at_all(sample_model):
    """The whole point: a customer with only the RDF file must not need a config to lint clean."""
    bag = DiagnosticBag()
    lint(sample_model, Config(), bag)
    assert not bag.has_errors(), [str(d) for d in bag.errors]


def test_sample_file_punning_is_a_warning_not_an_error(sample_model):
    bag = DiagnosticBag()
    lint(sample_model, Config(), bag)
    assert any(diag.rule == "L-PUN" and diag.severity is Severity.WARNING for diag in bag)
