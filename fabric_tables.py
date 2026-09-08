"""Live Fabric/OneLake probing for physical table existence (`--require-physical-tables`).

No new dependency: auth reuses `azure-identity` (already required by `deploy.py`) and the
HTTP calls use the stdlib `urllib`. Per-lookup failures are fail-open — an unresolved
lakehouse id or a single network/auth error is reported as "unverifiable" rather than
treated as "missing", so a placeholder GUID or a transient blip never empties the ontology.
Callers should still treat a *total* probe failure (every lookup unverifiable) as a hard
error rather than silent success — see `TableIndex.stats`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import Message
from typing import Optional

from .config import PLACEHOLDER_GUID, LakehouseRef

FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"
ONELAKE_DFS_BASE = "https://onelake.dfs.fabric.microsoft.com"
FABRIC_RESOURCE_SCOPE = "https://api.fabric.microsoft.com/.default"
# OneLake is addressed as ADLS Gen2 storage and only accepts Storage-audience tokens
# (learn.microsoft.com/fabric/onelake/onelake-access-api#authorization) - NOT the Fabric
# REST API audience above.
ONELAKE_STORAGE_SCOPE = "https://storage.azure.com/.default"


class TableProbeError(Exception):
    """Raised when the Fabric REST API / OneLake DFS listing could not be retrieved."""


@dataclass
class TableIndexStats:
    """Lookup outcome counters, so a total probe failure can be reported instead of silently
    treated as success (every entity 'unverifiable' looks identical to 'nothing was dropped')."""

    verified_present: int = 0
    verified_missing: int = 0
    unverified: int = 0

    @property
    def total(self) -> int:
        return self.verified_present + self.verified_missing + self.unverified


class TableIndex:
    """Answers whether a `schema.table` physically exists in a lakehouse.

    `has()` returns `None` when existence cannot be determined (unresolved lakehouse id);
    callers must treat `None` as "keep the entity" (fail open), never as "missing".
    """

    def __init__(self) -> None:
        self.stats = TableIndexStats()
        self._counted: set[tuple[str, str, str]] = set()

    def has(self, lakehouse: LakehouseRef, schema: str, table: str) -> Optional[bool]:
        raise NotImplementedError

    def is_unverifiable(self, lakehouse: LakehouseRef) -> bool:
        raise NotImplementedError

    def known_sample(self, limit: int = 12) -> list[str]:
        """A few discovered 'schema.table' names, for error messages that would otherwise be unactionable."""
        return []

    def _record(self, lakehouse: LakehouseRef, schema: str, table: str, result: Optional[bool]) -> Optional[bool]:
        # The mapper looks the same table up more than once (entity pass, valueType-conflict
        # pre-pass, contextualization); count distinct tables so the reported totals stay honest.
        key = (lakehouse.name or "", schema.lower(), table.lower())
        if key in self._counted:
            return result
        self._counted.add(key)
        if result is None:
            self.stats.unverified += 1
        elif result:
            self.stats.verified_present += 1
        else:
            self.stats.verified_missing += 1
        return result


class StaticTableIndex(TableIndex):
    """Offline index for tests: an explicit set of known '<lakehouse>|<schema>.<table>' keys."""

    def __init__(self, known: Optional[set[str]] = None, unverifiable_lakehouses: Optional[set[str]] = None) -> None:
        super().__init__()
        self.known = {key.lower() for key in (known or set())}
        self.unverifiable_lakehouses = set(unverifiable_lakehouses or set())

    def has(self, lakehouse: LakehouseRef, schema: str, table: str) -> Optional[bool]:
        if lakehouse.name in self.unverifiable_lakehouses:
            return self._record(lakehouse, schema, table, None)
        return self._record(lakehouse, schema, table, f"{lakehouse.name}|{schema}.{table}".lower() in self.known)

    def is_unverifiable(self, lakehouse: LakehouseRef) -> bool:
        return lakehouse.name in self.unverifiable_lakehouses


class FabricTableProbe(TableIndex):
    """Live index backed by the Fabric REST 'List Tables' API.

    Schema-enabled lakehouses (`defaultSchema != "dbo"`) fall back to listing the OneLake
    DFS filesystem under `Tables/`, since List Tables does not expose schema names.
    Results are cached per `(workspaceId, lakehouseItemId)` for the life of the process.
    """

    def __init__(self, credential=None) -> None:
        super().__init__()
        self._credential = credential
        self._tokens: dict[str, str] = {}
        self._cache: dict[tuple[str, str], set[str]] = {}
        # Populated by has() on a probe failure; callers fail open and surface these once.
        self.errors: list[str] = []

    def _get_token(self, scope: str) -> str:
        if scope not in self._tokens:
            if self._credential is None:
                try:
                    from azure.identity import AzureCliCredential
                except ImportError as exc:
                    raise TableProbeError(
                        "azure-identity is required for --require-physical-tables: pip install azure-identity"
                    ) from exc
                self._credential = AzureCliCredential()
            try:
                self._tokens[scope] = self._credential.get_token(scope).token
            except Exception as exc:  # credential libraries raise their own exception types
                raise TableProbeError(f"failed to acquire an access token for {scope}: {exc}") from exc
        return self._tokens[scope]

    def _get_json(self, url: str, scope: str) -> tuple[dict, Message]:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._get_token(scope)}"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8")), response.headers
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            raise TableProbeError(f"HTTP {exc.code} from {url}: {body}") from exc
        except urllib.error.URLError as exc:
            raise TableProbeError(f"failed to reach {url}: {exc}") from exc

    def _list_via_rest(self, workspace_id: str, item_id: str) -> set[str]:
        tables: set[str] = set()
        url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/lakehouses/{item_id}/tables"
        while url:
            payload, _ = self._get_json(url, FABRIC_RESOURCE_SCOPE)
            for entry in payload.get("data") or []:
                name = entry.get("name")
                if name:
                    tables.add(f"dbo.{name}".lower())
            token = payload.get("continuationToken")
            url = (
                f"{FABRIC_API_BASE}/workspaces/{workspace_id}/lakehouses/{item_id}/tables"
                f"?continuationToken={urllib.parse.quote(token)}"
                if token
                else None
            )
        return tables

    def _dfs_listing(self, workspace_id: str, item_id: str, directory: str, recursive: bool) -> list[tuple[str, bool]]:
        """One paginated ADLS List Path call. Returns (path relative to `directory`, is_directory)."""
        base = (
            f"{ONELAKE_DFS_BASE}/{workspace_id}?resource=filesystem"
            f"&recursive={'true' if recursive else 'false'}"
            f"&directory={urllib.parse.quote(f'{item_id}/{directory}', safe='')}"
        )
        prefix = f"{item_id}/{directory}/"
        entries: list[tuple[str, bool]] = []
        continuation: Optional[str] = None
        while True:
            url = base if not continuation else f"{base}&continuation={urllib.parse.quote(continuation, safe='')}"
            payload, headers = self._get_json(url, ONELAKE_STORAGE_SCOPE)
            for entry in payload.get("paths") or []:
                name = entry.get("name", "")
                if name.startswith(prefix):
                    entries.append((name[len(prefix) :], str(entry.get("isDirectory")).lower() == "true"))
            continuation = headers.get("x-ms-continuation")
            if not continuation:
                break
        return entries

    def _list_via_dfs(self, workspace_id: str, item_id: str) -> set[str]:
        # OneLake DFS addressing: the WORKSPACE is the filesystem, the item is a path segment
        # under it (learn.microsoft.com/fabric/onelake/onelake-access-api#uri-syntax) - the item
        # must NOT be placed in the filesystem/account position.
        children: dict[str, set[str]] = {}
        top_level: set[str] = set()
        for rel, is_dir in self._dfs_listing(workspace_id, item_id, "Tables", recursive=True):
            if not is_dir:
                continue
            parts = rel.split("/")
            if len(parts) == 1:
                top_level.add(parts[0])
            elif len(parts) == 2:
                children.setdefault(parts[0], set()).add(parts[1])

        # A recursive listing does NOT descend into shortcut directories, so a schema backed by a
        # shortcut comes back as a childless directory; re-list those explicitly or their tables
        # are invisible and every entity bound to them looks "missing".
        for name in sorted(top_level - set(children)):
            children[name] = {
                rel
                for rel, is_dir in self._dfs_listing(workspace_id, item_id, f"Tables/{name}", recursive=False)
                if is_dir and "/" not in rel
            }

        tables: set[str] = set()
        for name, kids in children.items():
            if "_delta_log" in kids:
                # schema-less lakehouse: this directory is the table itself, not a schema
                tables.add(f"dbo.{name}".lower())
            else:
                tables.update(f"{name}.{kid}".lower() for kid in kids)
        return tables

    def _tables_for(self, lakehouse: LakehouseRef) -> Optional[set[str]]:
        if self.is_unverifiable(lakehouse):
            return None
        key = (lakehouse.workspace_id, lakehouse.item_id)
        if key not in self._cache:
            use_dfs = lakehouse.default_schema != "dbo"
            tables = self._list_via_dfs(*key) if use_dfs else self._list_via_rest(*key)
            if not tables:
                raise TableProbeError(
                    f"probe for lakehouse '{lakehouse.name}' ({key[1]}) returned zero tables; "
                    "treating as unverifiable rather than dropping every declared entity"
                )
            self._cache[key] = tables
        return self._cache[key]

    def has(self, lakehouse: LakehouseRef, schema: str, table: str) -> Optional[bool]:
        try:
            tables = self._tables_for(lakehouse)
        except TableProbeError as exc:
            # Fail open: an auth/network error must never be mistaken for "table missing".
            self.errors.append(str(exc))
            return self._record(lakehouse, schema, table, None)
        if tables is None:
            return self._record(lakehouse, schema, table, None)
        return self._record(lakehouse, schema, table, f"{schema}.{table}".lower() in tables)

    def is_unverifiable(self, lakehouse: LakehouseRef) -> bool:
        return not lakehouse.workspace_id or lakehouse.item_id == PLACEHOLDER_GUID

    def known_sample(self, limit: int = 12) -> list[str]:
        discovered: set[str] = set()
        for tables in self._cache.values():
            discovered |= tables
        return sorted(discovered)[:limit]
