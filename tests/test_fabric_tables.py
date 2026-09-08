"""FabricTableProbe URL shape / token audience / OneLake path parsing (no network)."""

import io
import json
from email.message import Message

from rdf2ontology.config import LakehouseRef
from rdf2ontology.fabric_tables import FABRIC_RESOURCE_SCOPE, ONELAKE_STORAGE_SCOPE, FabricTableProbe


class _FakeToken:
    def __init__(self, token):
        self.token = token


class _FakeCredential:
    """Returns a scope-tagged token so tests can assert which audience was requested."""

    def __init__(self):
        self.requested_scopes = []

    def get_token(self, scope):
        self.requested_scopes.append(scope)
        return _FakeToken(f"token-for-{scope}")


class _FakeResponse:
    def __init__(self, payload, headers=None):
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_rest_list_uses_fabric_scope_and_dbo_schema(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout=30):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer token-for-" + FABRIC_RESOURCE_SCOPE
        return _FakeResponse({"data": [{"name": "customer"}], "continuationToken": None})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    credential = _FakeCredential()
    probe = FabricTableProbe(credential=credential)
    lakehouse = LakehouseRef(name="lh", item_id="item-1", workspace_id="ws-1", default_schema="dbo")

    assert probe.has(lakehouse, "dbo", "customer") is True
    assert probe.has(lakehouse, "dbo", "missing_table") is False
    assert "/workspaces/ws-1/lakehouses/item-1/tables" in requests[0].full_url
    assert credential.requested_scopes == [FABRIC_RESOURCE_SCOPE]
    assert probe.stats.verified_present == 1
    assert probe.stats.verified_missing == 1


def test_dfs_list_uses_storage_scope_and_correct_url_shape(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout=30):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer token-for-" + ONELAKE_STORAGE_SCOPE
        return _FakeResponse(
            {
                "paths": [
                    {"name": "item-1/Tables/bronze/customer", "isDirectory": "true"},
                    {"name": "item-1/Tables/bronze/customer/_delta_log", "isDirectory": "true"},
                    {"name": "item-1/Files/README.md", "isDirectory": "false"},
                ]
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    credential = _FakeCredential()
    probe = FabricTableProbe(credential=credential)
    lakehouse = LakehouseRef(name="lh", item_id="item-1", workspace_id="ws-1", default_schema="bronze")

    assert probe.has(lakehouse, "bronze", "customer") is True
    assert probe.has(lakehouse, "bronze", "nonexistent") is False

    url = requests[0].full_url
    # workspace is the filesystem; the item id is a *path* segment (directory=), not the filesystem itself.
    assert url.startswith("https://onelake.dfs.fabric.microsoft.com/ws-1?")
    assert "directory=item-1%2FTables" in url
    assert credential.requested_scopes == [ONELAKE_STORAGE_SCOPE]
    assert probe.stats.verified_present == 1
    assert probe.stats.verified_missing == 1


def test_unresolved_lakehouse_is_unverifiable_without_any_network_call(monkeypatch):
    def fake_urlopen(*_args, **_kwargs):
        raise AssertionError("must not probe an unresolved lakehouse")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    probe = FabricTableProbe(credential=_FakeCredential())
    lakehouse = LakehouseRef(name="lh", item_id="00000000-0000-0000-0000-000000000000", workspace_id=None)

    assert probe.has(lakehouse, "dbo", "customer") is None
    assert probe.stats.unverified == 1


def test_empty_probe_result_is_a_probe_error_not_a_silent_drop(monkeypatch):
    def fake_urlopen(request, timeout=30):
        return _FakeResponse({"data": [], "continuationToken": None})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    probe = FabricTableProbe(credential=_FakeCredential())
    lakehouse = LakehouseRef(name="lh", item_id="item-1", workspace_id="ws-1", default_schema="dbo")

    assert probe.has(lakehouse, "dbo", "customer") is None
    assert probe.stats.unverified == 1
    assert probe.errors and "zero tables" in probe.errors[0]
