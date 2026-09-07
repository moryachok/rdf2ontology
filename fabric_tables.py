"""Live Fabric/OneLake probing for physical table existence (`--require-physical-tables`).

No new dependency: auth reuses `azure-identity` (already required by `deploy.py`) and the
HTTP calls use the stdlib `urllib`. All lookups are fail-open — an unresolved lakehouse id
or a network/auth error is reported as "unverifiable" rather than treated as "missing", so a
placeholder GUID or an offline run never empties the ontology.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from email.message import Message
from typing import Optional

from .config import PLACEHOLDER_GUID, LakehouseRef

FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"
ONELAKE_DFS_BASE = "https://onelake.dfs.fabric.microsoft.com"
FABRIC_RESOURCE_SCOPE = "https://api.fabric.microsoft.com/.default"


class TableProbeError(Exception):
    """Raised when the Fabric REST API / OneLake DFS listing could not be retrieved."""


class TableIndex:
    """Answers whether a `schema.table` physically exists in a lakehouse.

    `has()` returns `None` when existence cannot be determined (unresolved lakehouse id);
    callers must treat `None` as "keep the entity" (fail open), never as "missing".
    """

    def has(self, lakehouse: LakehouseRef, schema: str, table: str) -> Optional[bool]:
        raise NotImplementedError

    def is_unverifiable(self, lakehouse: LakehouseRef) -> bool:
        raise NotImplementedError


class StaticTableIndex(TableIndex):
    """Offline index for tests: an explicit set of known '<lakehouse>|<schema>.<table>' keys."""

    def __init__(self, known: Optional[set[str]] = None, unverifiable_lakehouses: Optional[set[str]] = None) -> None:
        self.known = {key.lower() for key in (known or set())}
        self.unverifiable_lakehouses = set(unverifiable_lakehouses or set())

    def has(self, lakehouse: LakehouseRef, schema: str, table: str) -> Optional[bool]:
        if lakehouse.name in self.unverifiable_lakehouses:
            return None
        return f"{lakehouse.name}|{schema}.{table}".lower() in self.known

    def is_unverifiable(self, lakehouse: LakehouseRef) -> bool:
        return lakehouse.name in self.unverifiable_lakehouses


class FabricTableProbe(TableIndex):
    """Live index backed by the Fabric REST 'List Tables' API.

    Schema-enabled lakehouses (`defaultSchema != "dbo"`) fall back to listing the OneLake
    DFS filesystem under `Tables/`, since List Tables does not expose schema names.
    Results are cached per `(workspaceId, lakehouseItemId)` for the life of the process.
    """

    def __init__(self, credential=None) -> None:
        self._credential = credential
        self._token: Optional[str] = None
        self._cache: dict[tuple[str, str], set[str]] = {}
        # Populated by has() on a probe failure; callers fail open and surface these once.
        self.errors: list[str] = []

    def _get_token(self) -> str:
        if self._token is None:
            if self._credential is None:
                try:
                    from azure.identity import AzureCliCredential
                except ImportError as exc:
                    raise TableProbeError(
                        "azure-identity is required for --require-physical-tables: pip install azure-identity"
                    ) from exc
                self._credential = AzureCliCredential()
            try:
                self._token = self._credential.get_token(FABRIC_RESOURCE_SCOPE).token
            except Exception as exc:  # credential libraries raise their own exception types
                raise TableProbeError(f"failed to acquire a Fabric access token: {exc}") from exc
        return self._token

    def _get_json(self, url: str) -> tuple[dict, Message]:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._get_token()}"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8")), response.headers
        except urllib.error.URLError as exc:
            raise TableProbeError(f"failed to reach Fabric ({url}): {exc}") from exc

    def _list_via_rest(self, workspace_id: str, item_id: str) -> set[str]:
        tables: set[str] = set()
        url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/lakehouses/{item_id}/tables"
        while url:
            payload, _ = self._get_json(url)
            for entry in payload.get("data") or []:
                name = entry.get("name")
                if name:
                    tables.add(f"dbo.{name}".lower())
            token = payload.get("continuationToken")
            url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/lakehouses/{item_id}/tables?continuationToken={token}" if token else None
        return tables

    def _list_via_dfs(self, workspace_id: str, item_id: str) -> set[str]:
        tables: set[str] = set()
        base_url = f"{ONELAKE_DFS_BASE}/{workspace_id}/{item_id}?resource=filesystem&recursive=true&directory=Tables"
        continuation: Optional[str] = None
        while True:
            url = base_url if not continuation else f"{base_url}&continuation={continuation}"
            payload, headers = self._get_json(url)
            for entry in payload.get("paths") or []:
                # a directory two levels below "Tables/" is "Tables/<schema>/<table>"; one level is "Tables/<table>".
                parts = [p for p in entry.get("name", "").split("/") if p]
                if len(parts) < 2 or parts[0] != "Tables" or str(entry.get("isDirectory")).lower() != "true":
                    continue
                if len(parts) == 3:
                    tables.add(f"{parts[1]}.{parts[2]}".lower())
                elif len(parts) == 2:
                    tables.add(f"dbo.{parts[1]}".lower())
            continuation = headers.get("x-ms-continuation")
            if not continuation:
                break
        return tables

    def _tables_for(self, lakehouse: LakehouseRef) -> Optional[set[str]]:
        if self.is_unverifiable(lakehouse):
            return None
        key = (lakehouse.workspace_id, lakehouse.item_id)
        if key not in self._cache:
            use_dfs = lakehouse.default_schema != "dbo"
            self._cache[key] = self._list_via_dfs(*key) if use_dfs else self._list_via_rest(*key)
        return self._cache[key]

    def has(self, lakehouse: LakehouseRef, schema: str, table: str) -> Optional[bool]:
        try:
            tables = self._tables_for(lakehouse)
        except TableProbeError as exc:
            # Fail open: an auth/network error must never be mistaken for "table missing".
            self.errors.append(str(exc))
            return None
        if tables is None:
            return None
        return f"{schema}.{table}".lower() in tables

    def is_unverifiable(self, lakehouse: LakehouseRef) -> bool:
        return not lakehouse.workspace_id or lakehouse.item_id == PLACEHOLDER_GUID
