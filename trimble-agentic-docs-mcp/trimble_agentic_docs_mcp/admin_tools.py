"""Operator-only MCP tools (upstream sync, cache). Not registered unless TRIMBLE_AGENTIC_MCP_ADMIN_TOOLS=1."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from trimble_agentic_docs_mcp.dev_docs_sync import run_dev_docs_sync
from trimble_agentic_docs_mcp.runtime_state import clear_openapi_cache, get_store
from trimble_agentic_docs_mcp.upstream_sync import (
    EXPECTED_OPENAPI_SPECS,
    load_manifest,
    manifest_path,
    run_openapi_sync,
)


def _upstream_writes_allowed() -> bool:
    return os.environ.get("TRIMBLE_AGENTIC_ALLOW_NETWORK", "").strip().lower() in ("1", "true", "yes", "on")


def register_admin_tools(mcp: FastMCP) -> None:
    """Register sync / cache tools on the given FastMCP instance (call once at startup)."""

    @mcp.tool()
    def refresh_api_docs_cache() -> str:
        """Clear in-memory OpenAPI JSON cache. Call after you replace files under the api/ folder."""
        clear_openapi_cache()
        return "Cache cleared. Next tool calls will reload JSON from disk."

    @mcp.tool()
    def get_openapi_sync_status() -> str:
        """
        Local provenance only (no HTTP): reads docs/api/_openapi_manifest.json and per-spec file stats.
        Use after running sync (CLI or sync_openapi_from_upstream) to confirm versions and fetch times.
        """
        store = get_store()
        api_dir = store.api_dir
        manifest = load_manifest(api_dir)
        files: dict[str, dict[str, str | int]] = {}
        for spec_id in store.list_spec_ids():
            p = (api_dir / f"{spec_id}.json").resolve()
            if not p.is_file():
                continue
            try:
                p.relative_to(api_dir.resolve())
            except ValueError:
                continue
            st = p.stat()
            files[spec_id] = {
                "path": str(p),
                "bytes": st.st_size,
                "mtime_utc": datetime.fromtimestamp(st.st_mtime, UTC)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
            }
        payload = {
            "expected_specs": list(EXPECTED_OPENAPI_SPECS),
            "local_spec_files": sorted(files.keys()),
            "manifest_path": str(manifest_path(api_dir)),
            "manifest": manifest,
            "files": files,
            "note": "OpenAPI sync targets /api/* JSON. Narrative /docs pages use sync_dev_docs_from_urls (HTML fetch + text extract).",
        }
        return json.dumps(payload, indent=2)

    @mcp.tool()
    def sync_openapi_from_upstream(dry_run: bool = True) -> str:
        """
        Download official OpenAPI 3.x JSON over HTTPS (same sources as urls.txt ## APIs). Not HTML scraping.

        dry_run=true (default): GET and validate only; no files written.
        dry_run=false: writes docs/api/{spec}.json and _openapi_manifest.json (requires TRIMBLE_AGENTIC_ALLOW_NETWORK=1).

        After a successful non-dry sync, call refresh_api_docs_cache so this server reloads from disk.
        For CI or locked-down hosts, prefer CLI: trimble-agentic-openapi-sync (syncs OpenAPI + dev docs by default).
        """
        if not dry_run and not _upstream_writes_allowed():
            return json.dumps(
                {
                    "error": "network_writes_disabled",
                    "hint": "Set TRIMBLE_AGENTIC_ALLOW_NETWORK=1 for non-dry sync, or run: trimble-agentic-openapi-sync",
                },
                indent=2,
            )
        store = get_store()
        try:
            summary = run_openapi_sync(api_dir=store.api_dir, urls_file=store.urls_file, dry_run=dry_run)
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": str(e)}, indent=2)
        if not dry_run and summary.get("ok_count", 0) > 0:
            clear_openapi_cache()
            summary["cache"] = "cleared_in_process"
        return json.dumps(summary, indent=2)

    @mcp.tool()
    def sync_dev_docs_from_urls(dry_run: bool = True) -> str:
        """
        Fetch urls.txt ## Docs over HTTPS, extract main article text (trafilatura), write docs/cached/dev-portal/.

        dry_run=false requires TRIMBLE_AGENTIC_ALLOW_NETWORK=1. Optional TRIMBLE_AGENTIC_SYNC_BEARER_TOKEN
        if pages return 401. This is not a headless browser; heavy client-rendered pages may need an official export.
        """
        if not dry_run and not _upstream_writes_allowed():
            return json.dumps(
                {
                    "error": "network_writes_disabled",
                    "hint": "Set TRIMBLE_AGENTIC_ALLOW_NETWORK=1 for non-dry sync.",
                },
                indent=2,
            )
        store = get_store()
        try:
            summary = run_dev_docs_sync(urls_file=store.urls_file, dry_run=dry_run)
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": str(e)}, indent=2)
        return json.dumps(summary, indent=2)

    @mcp.tool()
    def sync_all_upstream_content(dry_run: bool = True) -> str:
        """Run OpenAPI JSON sync plus dev-docs cache sync in one call (same rules as the individual sync tools)."""
        if not dry_run and not _upstream_writes_allowed():
            return json.dumps(
                {
                    "error": "network_writes_disabled",
                    "hint": "Set TRIMBLE_AGENTIC_ALLOW_NETWORK=1 for non-dry sync.",
                },
                indent=2,
            )
        store = get_store()
        out: dict[str, Any] = {}
        try:
            out["openapi"] = run_openapi_sync(api_dir=store.api_dir, urls_file=store.urls_file, dry_run=dry_run)
        except Exception as e:  # noqa: BLE001
            out["openapi"] = {"error": str(e)}
        try:
            out["dev_docs"] = run_dev_docs_sync(urls_file=store.urls_file, dry_run=dry_run)
        except Exception as e:  # noqa: BLE001
            out["dev_docs"] = {"error": str(e)}
        if not dry_run and isinstance(out.get("openapi"), dict) and out["openapi"].get("ok_count", 0) > 0:
            clear_openapi_cache()
            out["openapi_cache"] = "cleared_in_process"
        return json.dumps(out, indent=2)
