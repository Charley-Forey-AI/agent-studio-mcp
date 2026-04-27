"""Integration-style tests for list_api_specs tool output."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from trimble_agentic_docs_mcp import runtime_state
from trimble_agentic_docs_mcp.store import OpenAPIDocStore
from trimble_agentic_docs_mcp.upstream_sync import manifest_path


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    runtime_state.reset_store()
    yield
    runtime_state.reset_store()


def test_list_api_specs_includes_openapi_manifest_when_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = Path(__file__).resolve().parent
    fixture = root / "fixtures" / "minimal.json"
    api = tmp_path / "api"
    api.mkdir()
    shutil.copy(fixture, api / "minimal.json")
    manifest_path(api).write_text(
        json.dumps(
            {
                "updated_at": "2026-04-01T12:00:00Z",
                "entries": {
                    "minimal": {
                        "fetched_at": "2026-04-01T12:00:01Z",
                        "etag": '"etag1"',
                        "source_url": "https://example.test/openapi.json",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    store = OpenAPIDocStore(api_dir=api, urls_file=api / "missing.txt")
    import trimble_agentic_docs_mcp.server as server_mod

    monkeypatch.setattr(server_mod, "get_store", lambda: store)
    raw = server_mod.list_api_specs()
    data = json.loads(raw)
    assert "specs" in data and data["api_dir"] == str(api)
    om = data.get("openapi_manifest")
    assert om is not None
    assert om["updated_at"] == "2026-04-01T12:00:00Z"
    assert om["entries"]["minimal"]["etag"] == '"etag1"'
