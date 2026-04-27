"""Compact OpenAPI sync manifest summary for MCP clients."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from trimble_agentic_docs_mcp.upstream_sync import load_manifest, manifest_path


def summarize_openapi_manifest(api_dir: Path) -> dict[str, Any] | None:
    """
    If _openapi_manifest.json exists under api_dir, return a small summary for staleness.
    Returns None when the manifest file is absent.
    """
    p = manifest_path(api_dir)
    if not p.is_file():
        return None
    raw = load_manifest(api_dir)
    entries = raw.get("entries") or {}
    if not isinstance(entries, dict):
        entries = {}
    out: dict[str, Any] = {
        "manifest_path": str(p),
        "updated_at": raw.get("updated_at"),
        "entry_count": len(entries),
    }
    if raw.get("error"):
        out["manifest_error"] = raw["error"]
    per: dict[str, Any] = {}
    for spec_id in sorted(entries.keys()):
        row = entries.get(spec_id)
        if isinstance(row, dict):
            per[spec_id] = {
                "fetched_at": row.get("fetched_at"),
                "etag": row.get("etag"),
                "source_url": row.get("source_url"),
            }
    out["entries"] = per
    return out
