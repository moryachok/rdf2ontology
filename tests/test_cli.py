"""CLI wiring: subcommands, exit codes and the mandatory lint gate."""

import json

from rdf2ontology.cli import EXIT_DEPLOY, EXIT_INPUT, EXIT_LINT, EXIT_OK, main

from conftest import DEFAULTS_CONFIG, REFERENCE_ITEM, SAMPLE_OVERRIDES, SAMPLE_TTL

CONFIG_ARGS = ["--config", str(DEFAULTS_CONFIG), "--config", str(SAMPLE_OVERRIDES)]


def test_lint_passes_with_the_shipped_config(capsys):
    code = main(["lint", "--input", str(SAMPLE_TTL), *CONFIG_ARGS])
    assert code == EXIT_OK
    assert "L-SRC-TABLE" in capsys.readouterr().out


def test_lint_succeeds_with_no_config_at_all(capsys):
    """The core ask: a customer with only the RDF file must get a clean lint."""
    assert main(["lint", "--input", str(SAMPLE_TTL)]) == EXIT_OK
    assert "L-PUN" in capsys.readouterr().out  # still reported, just as a warning


def test_lint_json_format(capsys):
    main(["lint", "--input", str(SAMPLE_TTL), *CONFIG_ARGS, "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["error"] == 0
    assert any(d["rule"] == "L-KEY" for d in payload["diagnostics"])


def test_lint_fail_on_warning(capsys):
    code = main(["lint", "--input", str(SAMPLE_TTL), *CONFIG_ARGS, "--fail-on", "warning"])
    assert code == EXIT_LINT


def test_build_succeeds_with_no_config_at_all(tmp_path, capsys):
    """The core ask: `build --input <ttl>` must work with nothing else supplied."""
    code = main(["build", "--input", str(SAMPLE_TTL), "--output", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    item_dir = tmp_path / "CustomerAddressOntology.Ontology"
    assert (item_dir / ".platform").is_file()
    assert "23 (2 bound, 21 unbound)" in out
    # bound entities still emit a full binding, just with placeholder deployment ids
    binding = json.loads(next((item_dir / "EntityTypes").glob("*/DataBindings/*.json")).read_text())
    assert binding["dataBindingConfiguration"]["sourceTableProperties"]["itemId"] == "00000000-0000-0000-0000-000000000000"


def test_build_with_workspace_and_lakehouse_ids_has_no_placeholders(tmp_path, capsys):
    code = main(
        [
            "build", "--input", str(SAMPLE_TTL), "--output", str(tmp_path),
            "--workspace-id", "11111111-1111-1111-1111-111111111111",
            "--lakehouse-id", "22222222-2222-2222-2222-222222222222",
        ]
    )
    assert code == EXIT_OK
    item_dir = tmp_path / "CustomerAddressOntology.Ontology"
    for binding_file in (item_dir / "EntityTypes").glob("*/DataBindings/*.json"):
        source = json.loads(binding_file.read_text())["dataBindingConfiguration"]["sourceTableProperties"]
        assert source["workspaceId"] == "11111111-1111-1111-1111-111111111111"
        assert source["itemId"] == "22222222-2222-2222-2222-222222222222"


def test_build_writes_the_tree(tmp_path, capsys):
    code = main(["build", "--input", str(SAMPLE_TTL), *CONFIG_ARGS, "--output", str(tmp_path)])
    assert code == EXIT_OK
    item_dir = tmp_path / "Customer_Address.Ontology"
    assert (item_dir / ".platform").is_file()
    assert (tmp_path / "Customer_Address.id-map.json").is_file()
    assert "23 (2 bound, 21 unbound)" in capsys.readouterr().out


def test_build_check_only_writes_nothing(tmp_path, capsys):
    code = main(
        [
            "build",
            "--input", str(SAMPLE_TTL),
            *CONFIG_ARGS,
            "--output", str(tmp_path),
            "--check-only",
        ]
    )
    assert code == EXIT_OK
    assert not list(tmp_path.iterdir())


def test_build_json_report(tmp_path):
    report = tmp_path / "report.json"
    main(
        [
            "build",
            "--input", str(SAMPLE_TTL),
            *CONFIG_ARGS,
            "--output", str(tmp_path),
            "--json-report", str(report),
        ]
    )
    payload = json.loads(report.read_text())
    assert payload["ontology"] == "Customer_Address"
    assert payload["entityTypes"]["Customer"]["bound"] is True
    assert payload["entityTypes"]["City"]["bound"] is False
    assert payload["relationshipTypes"]["hasAddress"]["contextualized"] is True


def test_ignore_flag_is_additive_to_the_config(tmp_path, capsys):
    code = main(
        [
            "build",
            "--input", str(SAMPLE_TTL),
            *CONFIG_ARGS,
            "--output", str(tmp_path),
            "--ignore", "L-KEY",
            "--ignore", "W9",
        ]
    )
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "L-KEY" not in out
    assert "W9" not in out


def test_build_without_a_config_reports_the_missing_lakehouse_as_a_warning(tmp_path, capsys):
    code = main(["build", "--input", str(SAMPLE_TTL), "--output", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "W7" in out
    assert "E11" not in out


def test_validate_item_folder(capsys):
    assert main(["validate", "--item", str(REFERENCE_ITEM)]) == EXIT_OK


def test_validate_rdf_source(capsys):
    code = main(["validate", "--input", str(SAMPLE_TTL), *CONFIG_ARGS])
    assert code == EXIT_OK
    assert "Preview" in capsys.readouterr().out


def test_diff_self_is_empty(capsys):
    code = main(["diff", "--left", str(REFERENCE_ITEM), "--right", str(REFERENCE_ITEM), "--with-ids"])
    assert code == EXIT_OK
    assert "(no differences)" in capsys.readouterr().out


def test_bad_config_exits_with_input_error(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("nope: {}\n", encoding="utf-8")
    assert main(["lint", "--input", str(SAMPLE_TTL), "--config", str(bad)]) == EXIT_INPUT


def test_deploy_dry_run(tmp_path, capsys):
    main(["build", "--input", str(SAMPLE_TTL), *CONFIG_ARGS, "--output", str(tmp_path)])
    capsys.readouterr()
    code = main(
        [
            "deploy",
            "--item-dir", str(tmp_path),
            "--workspace-id", "11111111-1111-1111-1111-111111111111",
            "--dry-run",
        ]
    )
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "Deployment plan" in out
    assert "Customer_Address.Ontology" in out
    assert "dry run" in out
    assert "placeholder GUID" in out  # sample config's workspaceId is still the placeholder


def test_deploy_refuses_placeholders_without_dry_run(tmp_path, capsys):
    main(["build", "--input", str(SAMPLE_TTL), *CONFIG_ARGS, "--output", str(tmp_path)])
    capsys.readouterr()
    code = main(
        ["deploy", "--item-dir", str(tmp_path), "--workspace-id", "11111111-1111-1111-1111-111111111111", "--yes"]
    )
    out = capsys.readouterr().out
    assert code == EXIT_DEPLOY
    assert "refusing to publish" in out
    assert "--allow-placeholders" in out
