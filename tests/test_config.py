"""Config resolution for fabric.lakehouses.* / fabric.workspaceId, incl. the --require-physical-tables
prerequisite: a lakehouse must resolve to a real workspace id, not stay stuck on the placeholder."""

import textwrap

from rdf2ontology.config import Config, LakehouseRef, PLACEHOLDER_GUID, apply_cli_overrides, load_config


def test_lakehouse_workspace_id_falls_back_to_top_level_fabric_workspace_id(tmp_path):
    config_path = tmp_path / "cfg.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            fabric:
              workspaceId: 11111111-1111-1111-1111-111111111111
              lakehouses:
                default:
                  itemId: 22222222-2222-2222-2222-222222222222
                  defaultSchema: bronze
            """
        ),
        encoding="utf-8",
    )

    config = load_config([config_path])
    ref = config.lakehouse("default")

    assert ref.workspace_id == "11111111-1111-1111-1111-111111111111"
    assert ref.item_id == "22222222-2222-2222-2222-222222222222"


def test_lakehouse_entry_workspace_id_still_wins_over_the_top_level_one(tmp_path):
    config_path = tmp_path / "cfg.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            fabric:
              workspaceId: 11111111-1111-1111-1111-111111111111
              lakehouses:
                default:
                  itemId: 22222222-2222-2222-2222-222222222222
                  workspaceId: 33333333-3333-3333-3333-333333333333
            """
        ),
        encoding="utf-8",
    )

    config = load_config([config_path])

    assert config.lakehouse("default").workspace_id == "33333333-3333-3333-3333-333333333333"


def test_synthetic_lakehouse_ref_falls_back_to_config_workspace_id():
    config = Config(workspace_id="44444444-4444-4444-4444-444444444444")

    ref = config.lakehouse(None)

    assert ref.workspace_id == "44444444-4444-4444-4444-444444444444"
    assert ref.item_id == PLACEHOLDER_GUID


def test_apply_cli_overrides_backfills_workspace_id_from_config_when_cli_flag_absent():
    config = Config(workspace_id="55555555-5555-5555-5555-555555555555")
    config.lakehouses["default"] = LakehouseRef(name="default", item_id="66666666-6666-6666-6666-666666666666")

    apply_cli_overrides(config)

    assert config.lakehouses["default"].workspace_id == "55555555-5555-5555-5555-555555555555"


def test_cli_workspace_id_still_wins_over_config_workspace_id():
    config = Config(workspace_id="55555555-5555-5555-5555-555555555555")
    config.lakehouses["default"] = LakehouseRef(name="default", item_id="66666666-6666-6666-6666-666666666666")

    apply_cli_overrides(config, workspace_id="77777777-7777-7777-7777-777777777777")

    assert config.lakehouses["default"].workspace_id == "77777777-7777-7777-7777-777777777777"
