"""Publish a generated Ontology item with fabric-cicd."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .config import PLACEHOLDER_GUID
from .diff import load_item


class DeployError(Exception):
    """Raised when the deployment cannot be attempted or fails."""


def resolve_repository_directory(item_dir: Path) -> tuple[Path, list[str]]:
    """Accept either the repository folder or a single '<Name>.Ontology' folder."""
    item_dir = Path(item_dir)
    if item_dir.name.endswith(".Ontology"):
        return item_dir.parent, [item_dir.name]
    names = sorted(child.name for child in item_dir.iterdir() if child.is_dir() and child.name.endswith(".Ontology"))
    if not names:
        raise DeployError(f"no '*.Ontology' folder found under {item_dir}")
    return item_dir, names


def render_plan(repository_directory: Path, item_names: list[str], workspace_id: str, environment: Optional[str]) -> str:
    lines = [
        "== Deployment plan ==",
        f"  repository : {repository_directory}",
        f"  workspace  : {workspace_id}",
        f"  environment: {environment or '<none>'}",
        f"  item types : Ontology",
    ]
    for name in item_names:
        item = load_item(repository_directory / name, ignore_ids=True)
        bound = sum(1 for entity in item["entityTypes"].values() if entity["bindings"])
        lines.append(
            f"  item       : {name} "
            f"({len(item['entityTypes'])} entity types, {bound} bound, "
            f"{len(item['relationshipTypes'])} relationship types)"
        )
    lines.append("  NOTE: updateDefinition replaces the full parts tree; portal-side edits will be overwritten.")
    return "\n".join(lines)


def _covered_by_parameter_file(repository_directory: Path, environment: Optional[str]) -> set[str]:
    """find_value entries that parameter.yml will substitute for the given environment."""
    path = repository_directory / "parameter.yml"
    if not path.is_file() or not environment:
        return set()
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    covered: set[str] = set()
    for entry in data.get("find_replace") or []:
        replace_value = entry.get("replace_value") or {}
        if isinstance(replace_value, dict) and replace_value.get(environment):
            covered.add(entry.get("find_value"))
    return covered


def _find_placeholders(repository_directory: Path, item_names: list[str]) -> list[str]:
    hits = []
    for name in item_names:
        for path in sorted((repository_directory / name).rglob("*.json")):
            if PLACEHOLDER_GUID in path.read_text(encoding="utf-8"):
                hits.append(str(path.relative_to(repository_directory)))
    return hits


def deploy(
    item_dir: Path,
    workspace_id: str,
    environment: Optional[str] = None,
    dry_run: bool = False,
    assume_yes: bool = False,
    allow_placeholders: bool = False,
    confirm=input,
    echo=print,
) -> int:
    repository_directory, item_names = resolve_repository_directory(item_dir)
    echo(render_plan(repository_directory, item_names, workspace_id, environment))

    covered = _covered_by_parameter_file(repository_directory, environment)
    placeholders = [] if PLACEHOLDER_GUID in covered else _find_placeholders(repository_directory, item_names)

    if dry_run:
        if placeholders:
            echo(
                f"note: {len(placeholders)} file(s) still carry the placeholder GUID {PLACEHOLDER_GUID}; "
                "a real deploy will refuse to publish unless --allow-placeholders is passed."
            )
        echo("dry run: nothing was published.")
        return 0

    if placeholders and not allow_placeholders:
        echo(f"refusing to publish: {len(placeholders)} file(s) still carry the placeholder GUID {PLACEHOLDER_GUID}.")
        for path in placeholders[:10]:
            echo(f"  - {path}")
        echo(
            "pass --allow-placeholders to publish anyway, rebuild with --workspace-id/--lakehouse-id, "
            "or add a parameter.yml find_replace entry for this --environment."
        )
        return 3

    if not assume_yes:
        answer = confirm("Publish these items to Fabric? Type 'yes' to continue: ").strip().lower()
        if answer != "yes":
            echo("aborted; nothing was published.")
            return 3

    try:
        from azure.identity import AzureCliCredential
        from fabric_cicd import FabricWorkspace, publish_all_items
    except ImportError as exc:
        raise DeployError(
            "fabric-cicd and azure-identity are required to deploy: pip install fabric-cicd azure-identity"
        ) from exc

    workspace = FabricWorkspace(
        workspace_id=workspace_id,
        # fabric_cicd requires a plain string; "N/A" is its own default for "no environment".
        environment=environment or "N/A",
        repository_directory=str(repository_directory),
        item_type_in_scope=["Ontology"],
        token_credential=AzureCliCredential(),
    )
    publish_all_items(workspace)
    echo(f"published {len(item_names)} ontology item(s) to workspace {workspace_id}.")
    return 0
