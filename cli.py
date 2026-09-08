"""Command-line entry point: lint | build | validate | diff | deploy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from .config import Config, ConfigError, apply_cli_overrides, load_config
from .deploy import DeployError, deploy
from .diagnostics import DiagnosticBag
from .diff import diff_items
from .emit import emit
from .fabric_tables import FabricTableProbe, TableIndex, TableProbeError
from .ids import IdMap, IdMapError
from .ir import OntologyIR
from .lint import lint
from .mapping import build_ontology, title_to_name
from .rdf_model import GraphModel, RdfParseError, load_graph
from .report import json_report, render_diagnostics, render_preview, render_summary
from .validate import validate_item_dir, validate_ontology

EXIT_OK = 0
EXIT_VALIDATION = 1
EXIT_INPUT = 2
EXIT_DEPLOY = 3
EXIT_LINT = 4


def _default_name(
    config: Config, input_path: Optional[Path], explicit: Optional[str], model: Optional[GraphModel] = None
) -> str:
    if explicit:
        return explicit
    if config.display_name:
        return config.display_name
    if model is not None and model.ontology_title:
        return title_to_name(model.ontology_title)
    if input_path is not None:
        return "".join(part.capitalize() for part in input_path.stem.replace("-", "_").split("_"))
    return "Ontology"


def _load(args) -> tuple[Config, GraphModel, DiagnosticBag, Optional[TableIndex]]:
    config = load_config(getattr(args, "config", None))
    apply_cli_overrides(
        config,
        workspace_id=getattr(args, "workspace_id", None),
        lakehouse_ids=getattr(args, "lakehouse_id", None),
        schema=getattr(args, "schema", None),
        require_physical_tables=getattr(args, "require_physical_tables", None),
        emit_unbound_entities=(False if getattr(args, "skip_unbound_entities", False) else None),
        emit_unbound_relationships=(False if getattr(args, "skip_unbound_relationships", False) else None),
    )
    _graph, model = load_graph(Path(args.input), config)
    ignore = list(config.lint_ignore) + list(getattr(args, "ignore", None) or [])
    bag = DiagnosticBag(ignore=ignore, strict=bool(getattr(args, "strict", False)))
    table_index = _build_table_index(config, bag)
    lint(model, config, bag, table_index)
    _report_table_probe_errors(table_index, bag)
    return config, model, bag, table_index


def _report_table_probe_errors(table_index: Optional[TableIndex], bag: DiagnosticBag) -> None:
    """Surface (and clear) any probe failures recorded on a FabricTableProbe; fail-open elsewhere already applied."""
    errors = getattr(table_index, "errors", None)
    if not errors:
        return
    for message in errors:
        bag.warning("W3d", f"Fabric table probe failed ({message}); affected tables treated as unverifiable")
    errors.clear()


def _table_probe_ineffective(table_index: Optional[TableIndex]) -> Optional[str]:
    """None if fine; else an error message for a --require-physical-tables run that verified nothing.

    Two silent-failure modes, both of which look identical to a healthy run in the output:
    nothing could be resolved (unusable ids), or the probe worked but not one declared table
    matched - which in practice means the configured schema or naming convention is wrong,
    not that the whole warehouse is empty.
    """
    if table_index is None:
        return None
    stats = table_index.stats
    if stats.total == 0:
        return None
    if stats.verified_present == 0 and stats.verified_missing == 0:
        return (
            f"--require-physical-tables checked {stats.total} table(s) but could not verify any of them "
            "(every lakehouse id/workspace id was unresolved, or every probe failed); "
            "pass --workspace-id/--lakehouse-id or set fabric.workspaceId / fabric.lakehouses.<name>.itemId"
        )
    if stats.verified_present == 0:
        sample = table_index.known_sample()
        found = ("; tables that do exist include: " + ", ".join(sample)) if sample else ""
        return (
            f"--require-physical-tables found NONE of the {stats.verified_missing} declared table(s) in the "
            f"lakehouse, which would emit an empty ontology; check fabric.lakehouses.<name>.defaultSchema "
            f"and the source-table naming convention{found}"
        )
    return None


def _build_table_index(config: Config, bag: DiagnosticBag) -> Optional[TableIndex]:
    if not config.flag("requirePhysicalTables"):
        return None
    try:
        return FabricTableProbe()
    except TableProbeError as exc:
        bag.warning(
            "W3d",
            f"could not initialize the Fabric table probe ({exc}); proceeding as if every table exists",
        )
        return None


def _lint_failed(bag: DiagnosticBag, config: Config, args) -> bool:
    fail_on = getattr(args, "fail_on", None) or config.lint_fail_on
    if bag.has_errors():
        return True
    return fail_on == "warning" and bool(bag.warnings)


def cmd_lint(args) -> int:
    config, _model, bag, table_index = _load(args)
    if args.format == "json":
        print(json.dumps({"counts": bag.counts(), "diagnostics": bag.to_list()}, indent=2))
    else:
        print(render_diagnostics(bag, f"Lint {args.input}"))
    ineffective = _table_probe_ineffective(table_index)
    if ineffective:
        print(f"\nerror: {ineffective}", file=sys.stderr)
        return EXIT_INPUT
    return EXIT_LINT if _lint_failed(bag, config, args) else EXIT_OK


def cmd_build(args) -> int:
    config, model, lint_bag, table_index = _load(args)
    print(render_diagnostics(lint_bag, f"Lint {args.input}", show_info=args.verbose))
    if _lint_failed(lint_bag, config, args):
        print("\nbuild aborted: fix the RDF (or pass --ignore <RULE>) before building.", file=sys.stderr)
        return EXIT_LINT

    name = _default_name(config, Path(args.input), args.name, model)
    build_bag = DiagnosticBag(ignore=lint_bag.ignore, strict=bool(args.strict))
    ontology: OntologyIR = build_ontology(model, config, name, build_bag, table_index)
    _report_table_probe_errors(table_index, build_bag)
    ineffective = _table_probe_ineffective(table_index)
    if ineffective:
        print(f"\nbuild aborted: {ineffective}", file=sys.stderr)
        return EXIT_INPUT

    id_map_path = Path(args.id_map) if args.id_map else Path(args.output) / f"{name}.id-map.json"
    id_map = IdMap.load(id_map_path, name, allow_new=True)
    existed = id_map_path.is_file()
    id_map.allow_new = bool(args.allow_new_ids) or not existed
    try:
        id_map.assign(ontology, source_rdf=str(args.input))
    except IdMapError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_VALIDATION

    validate_ontology(ontology, id_map, config, build_bag)
    print()
    print(render_diagnostics(build_bag, "Validate", show_info=args.verbose))

    if build_bag.has_errors():
        print("\nbuild aborted: validation errors.", file=sys.stderr)
        return EXIT_VALIDATION
    if args.check_only:
        print()
        print(render_summary(ontology, table_index=table_index))
        print("\ncheck-only: nothing was written.")
        return EXIT_OK

    result = emit(ontology, id_map, config, Path(args.output))
    id_map.save(id_map_path)
    structural = DiagnosticBag(ignore=lint_bag.ignore)
    validate_item_dir(result.item_dir, structural)
    if structural.has_errors():
        print()
        print(render_diagnostics(structural, "Validate (emitted tree)"))
        return EXIT_VALIDATION

    print()
    print(render_summary(ontology, result, table_index=table_index))
    print(f"  id map            : {id_map_path}")
    if args.json_report:
        report = json_report(ontology, lint_bag, build_bag, result, table_index=table_index)
        Path(args.json_report).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"  json report       : {args.json_report}")
    return EXIT_OK


def cmd_validate(args) -> int:
    if args.item:
        bag = DiagnosticBag(ignore=args.ignore or [], strict=bool(args.strict))
        validate_item_dir(Path(args.item), bag)
        print(render_diagnostics(bag, f"Validate {args.item}"))
        return EXIT_VALIDATION if bag.has_errors() else EXIT_OK

    config, model, lint_bag, table_index = _load(args)
    print(render_diagnostics(lint_bag, f"Lint {args.input}", show_info=False))
    if _lint_failed(lint_bag, config, args):
        return EXIT_LINT

    name = _default_name(config, Path(args.input), args.name, model)
    bag = DiagnosticBag(ignore=lint_bag.ignore, strict=bool(args.strict))
    ontology = build_ontology(model, config, name, bag, table_index)
    _report_table_probe_errors(table_index, bag)
    ineffective = _table_probe_ineffective(table_index)
    if ineffective:
        print(f"\nerror: {ineffective}", file=sys.stderr)
        return EXIT_INPUT
    id_map = IdMap(ontology_name=name, allow_new=True)
    id_map.assign(ontology, source_rdf=str(args.input))
    validate_ontology(ontology, id_map, config, bag)
    print()
    print(render_diagnostics(bag, "Validate"))
    print()
    print(render_preview(ontology))
    return EXIT_VALIDATION if bag.has_errors() else EXIT_OK


def cmd_diff(args) -> int:
    subset = [part.strip() for part in args.only.split(",")] if args.only else None
    lines = diff_items(
        Path(args.left),
        Path(args.right),
        ignore_ids=not args.with_ids,
        ignore_enrichment=args.ignore_enrichment,
        subset=subset,
    )
    print(f"== Diff: {args.left} -> {args.right} ==")
    if not lines:
        print("  (no differences)")
        return EXIT_OK
    for line in lines:
        print(f"  {line}")
    print(f"  -- {len(lines)} difference(s)")
    return EXIT_OK


def cmd_deploy(args) -> int:
    try:
        return deploy(
            Path(args.item_dir),
            workspace_id=args.workspace_id,
            environment=args.environment,
            dry_run=args.dry_run,
            assume_yes=args.yes,
            allow_placeholders=args.allow_placeholders,
        )
    except DeployError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DEPLOY


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rdf2ontology",
        description="Transform an RDF/OWL ontology into a Fabric IQ Ontology item deployable with fabric-cicd.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub):
        sub.add_argument(
            "--config",
            action="append",
            metavar="YAML",
            help="config file; repeatable to layer a generic defaults file with per-ontology overrides "
            "(later files win)",
        )
        sub.add_argument("--strict", action="store_true", help="treat warnings as errors")
        sub.add_argument("--ignore", action="append", metavar="RULE", help="ignore a lint/validation rule (repeatable)")
        sub.add_argument(
            "--require-physical-tables",
            action="store_true",
            default=None,
            help="probe Fabric and drop entity/relationship types whose declared source table does not exist yet",
        )
        sub.add_argument(
            "--skip-unbound-entities",
            action="store_true",
            default=False,
            help="drop entity types that have no declared source table (overrides defaults.emitUnboundEntities)",
        )
        sub.add_argument(
            "--skip-unbound-relationships",
            action="store_true",
            default=False,
            help="drop relationship types with no contextualization (overrides defaults.emitUnboundRelationships)",
        )

    lint_parser = subparsers.add_parser("lint", help="step 1: lint the RDF graph")
    lint_parser.add_argument("--input", required=True, help="RDF file (Turtle, RDF/XML, JSON-LD, ...)")
    lint_parser.add_argument("--format", choices=["text", "json"], default="text")
    lint_parser.add_argument("--fail-on", choices=["error", "warning"], default=None)
    add_common(lint_parser)
    lint_parser.set_defaults(func=cmd_lint)

    build_parser_ = subparsers.add_parser("build", help="lint, map and emit the Fabric definition tree")
    build_parser_.add_argument("--input", required=True)
    build_parser_.add_argument("--output", default="build", help="folder that will hold '<Name>.Ontology'")
    build_parser_.add_argument("--name", help="ontology display name")
    build_parser_.add_argument("--workspace-id", help="Fabric workspace id, used to fill in unresolved bindings")
    build_parser_.add_argument(
        "--lakehouse-id",
        action="append",
        metavar="[NAME=]GUID",
        help="lakehouse item id; bare GUID applies to every unresolved lakehouse, or scope with 'name=GUID' (repeatable)",
    )
    build_parser_.add_argument("--schema", help="default lakehouse schema for tables with no schema prefix (default: dbo)")
    build_parser_.add_argument("--id-map", help="path to the persisted name -> id map")
    build_parser_.add_argument("--allow-new-ids", action="store_true", help="allow minting ids absent from the id map")
    build_parser_.add_argument("--check-only", action="store_true", help="run the full pipeline without writing")
    build_parser_.add_argument("--json-report", help="write a machine-readable build report")
    build_parser_.add_argument("--fail-on", choices=["error", "warning"], default=None)
    build_parser_.add_argument("--verbose", action="store_true", help="include info diagnostics")
    add_common(build_parser_)
    build_parser_.set_defaults(func=cmd_build)

    validate_parser = subparsers.add_parser("validate", help="validate an RDF source or an emitted item folder")
    validate_parser.add_argument("--input", help="RDF file (IR-level validation)")
    validate_parser.add_argument("--item", help="existing '<Name>.Ontology' folder (structural validation)")
    validate_parser.add_argument("--name")
    validate_parser.add_argument("--fail-on", choices=["error", "warning"], default=None)
    add_common(validate_parser)
    validate_parser.set_defaults(func=cmd_validate)

    diff_parser = subparsers.add_parser("diff", help="compare two ontology item folders")
    diff_parser.add_argument("--left", required=True)
    diff_parser.add_argument("--right", required=True)
    diff_parser.add_argument("--with-ids", action="store_true", help="compare generated ids as well")
    diff_parser.add_argument("--ignore-enrichment", action="store_true")
    diff_parser.add_argument("--only", help="comma-separated entity type names to restrict the comparison to")
    diff_parser.set_defaults(func=cmd_diff)

    deploy_parser = subparsers.add_parser("deploy", help="publish with fabric-cicd")
    deploy_parser.add_argument("--item-dir", required=True, help="repository folder or a single '<Name>.Ontology' folder")
    deploy_parser.add_argument("--workspace-id", required=True)
    deploy_parser.add_argument("--environment", help="parameter.yml environment key")
    deploy_parser.add_argument("--dry-run", action="store_true")
    deploy_parser.add_argument("--yes", action="store_true", help="skip the interactive confirmation")
    deploy_parser.add_argument(
        "--allow-placeholders", action="store_true", help="publish even if placeholder GUIDs remain uncovered by parameter.yml"
    )
    deploy_parser.set_defaults(func=cmd_deploy)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate" and not args.input and not args.item:
        parser.error("validate requires --input or --item")
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_INPUT
    except RdfParseError as exc:
        print(f"L-PARSE  {exc}", file=sys.stderr)
        return EXIT_LINT
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT


if __name__ == "__main__":
    raise SystemExit(main())
