"""Tests for summarize_openapi_manifest."""

from __future__ import annotations

import json
from pathlib import Path

from trimble_agentic_docs_mcp.manifest_summary import summarize_openapi_manifest
from trimble_agentic_docs_mcp.upstream_sync import manifest_path


def test_summarize_returns_none_when_no_manifest(tmp_path: Path) -> None:
    assert summarize_openapi_manifest(tmp_path) is None


def test_summarize_reads_manifest(tmp_path: Path) -> None:
    api = tmp_path / "api"
    api.mkdir()
    payload = {
        "updated_at": "2026-01-01T00:00:00Z",
        "entries": {
            "agents": {
                "fetched_at": "2026-01-01T00:00:01Z",
                "etag": "abc",
                "source_url": "https://example.test/api/agents",
            }
        },
    }
    manifest_path(api).write_text(json.dumps(payload), encoding="utf-8")
    s = summarize_openapi_manifest(api)
    assert s is not None
    assert s["updated_at"] == "2026-01-01T00:00:00Z"
    assert s["entry_count"] == 1
    assert "agents" in s["entries"]
    assert s["entries"]["agents"]["etag"] == "abc"
