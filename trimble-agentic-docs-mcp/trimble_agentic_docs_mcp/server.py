"""
Trimble Agentic AI — public documentation MCP (Streamable HTTP only).

Integrators (default)
---------------------
1. `pip install -e .` from `trimble-agentic-docs-mcp/`, then `python -m trimble_agentic_docs_mcp`.
2. Point the MCP client at the URL printed on stderr (see `examples/mcp-cursor-config.example.json`).
3. Shipped artifacts: OpenAPI under `docs/api/`, optional narrative cache under `docs/cached/dev-portal/`.

Public MCP tool list excludes sync/admin tools. Agents use read/search tools only.

Operators (refreshing artifacts)
--------------------------------
Use CLI `trimble-agentic-openapi-sync` / `python -m trimble_agentic_docs_mcp.sync_cli` in CI or on a
secure host (`TRIMBLE_AGENTIC_SYNC_BEARER_TOKEN` when the portal returns 401). For recurring jobs,
`trimble-agentic-docs-refresh` runs OpenAPI + dev-docs sync with ETag skips by default (weekly
CronJob / `--daemon`). Optional in-process admin MCP tools: set `TRIMBLE_AGENTIC_MCP_ADMIN_TOOLS=1`
on the server to expose sync/cache tools (`sync_*`, `refresh_api_docs_cache`, `get_openapi_sync_status`).

Env: TRIMBLE_AGENTIC_API_DOCS_DIR, TRIMBLE_AGENTIC_URLS_FILE, TRIMBLE_AGENTIC_DEV_DOCS_CACHE_DIR,
TRIMBLE_AGENTIC_MCP_HOST, TRIMBLE_AGENTIC_MCP_PORT, TRIMBLE_AGENTIC_MCP_PATH (or FASTMCP_*).
Behind nginx with a public Host (e.g. 52.13.6.105), set TRIMBLE_AGENTIC_MCP_ALLOWED_HOSTS to that
hostname or IP (comma-separated). Or set TRIMBLE_AGENTIC_MCP_DISABLE_DNS_REBINDING=1 (not recommended
on untrusted networks). Alternatively, nginx may proxy_set_header Host 127.0.0.1:PORT to match defaults.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from trimble_agentic_docs_mcp.dev_docs_sync import (
    _default_dev_docs_cache_dir,
    list_dev_docs_pages,
    load_dev_docs_manifest,
    read_dev_docs_page,
    search_dev_docs,
)
from trimble_agentic_docs_mcp.json_output import truncate_json_response
from trimble_agentic_docs_mcp.manifest_summary import summarize_openapi_manifest
from trimble_agentic_docs_mcp.operation_guide import build_operation_guide
from trimble_agentic_docs_mcp.runtime_state import get_store
from trimble_agentic_docs_mcp.transport_security_bind import transport_security_for_bind


def _mcp_listen_settings() -> tuple[str, int, str]:
    host = os.environ.get("TRIMBLE_AGENTIC_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("TRIMBLE_AGENTIC_MCP_PORT", "8305"))
    path = os.environ.get("TRIMBLE_AGENTIC_MCP_PATH", "/mcp")
    return host, port, path


_http_host, _http_port, _http_path = _mcp_listen_settings()

mcp = FastMCP(
    "trimble-agentic-docs",
    instructions=(
        "Trimble Agentic AI developer assistant (read-only). "
        "Workflow: (1) list_api_specs — note spec_id and servers[].url. "
        "(2) search_operations — find path, method, operationId. "
        "(3) get_api_operation_guide — one structured view: parameters, request/response shapes, security. "
        "(4) get_operation_details or resolve_schema_ref for full OpenAPI fragments. "
        "(5) get_spec_description for service-wide auth and concepts. "
        "(6) search_dev_documentation / get_dev_docs_page for product docs; list_cached_dev_docs for inventory. "
        "(7) list_documentation_urls for portal links. "
        "If a tool response JSON has truncated=true, only preview is partial—narrow the query or fetch one operation/schema. "
        "list_api_specs may include openapi_manifest when _openapi_manifest.json is present (sync provenance). "
        "Resources: trimble-agentic://catalog, trimble-agentic://urls, trimble-agentic://dev-docs/inventory, "
        "trimble-agentic://spec/{spec_id}/info|paths-index|description."
    ),
    host=_http_host,
    port=_http_port,
    streamable_http_path=_http_path,
    transport_security=transport_security_for_bind(_http_host),
)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 80] + "\n\n… [truncated; narrow your query or use get_operation_details / resolve_schema_ref] …\n"


@mcp.tool()
def list_cached_dev_docs() -> str:
    """List narrative doc pages cached from urls.txt ## Docs (page_id, url, title, fetch metadata). Empty if this distribution has no bundled doc cache."""
    cache = _default_dev_docs_cache_dir()
    rows = list_dev_docs_pages(cache)
    return json.dumps(
        {"cache_dir": str(cache), "count": len(rows), "pages": rows},
        indent=2,
    )


@mcp.tool()
def search_dev_documentation(query: str, limit: int = 12) -> str:
    """Full-text substring search over cached extracted doc text (## Docs). Use list_cached_dev_docs for inventory."""
    lim = max(1, min(int(limit), 50))
    cache = _default_dev_docs_cache_dir()
    hits = search_dev_docs(cache, query, limit=lim)
    if not hits and not load_dev_docs_manifest(cache).get("pages"):
        return json.dumps(
            {
                "count": 0,
                "results": [],
                "hint": "No cached narrative docs in this deployment. Use list_documentation_urls for live portal links, or an operator build that bundles docs/cached/dev-portal/.",
            },
            indent=2,
        )
    return truncate_json_response({"count": len(hits), "results": hits}, 120_000)


@mcp.tool()
def get_dev_docs_page(page_id: str, max_chars: int = 48_000) -> str:
    """Load one cached doc page by page_id (from search_dev_documentation or list_cached_dev_docs)."""
    mc = max(2_000, min(int(max_chars), 500_000))
    cache = _default_dev_docs_cache_dir()
    doc = read_dev_docs_page(cache, page_id.strip())
    if doc is None:
        return json.dumps({"error": "not_found", "page_id": page_id, "cache_dir": str(cache)}, indent=2)
    return truncate_json_response(doc, mc)


@mcp.tool()
def list_api_specs() -> str:
    """List all OpenAPI specs found in TRIMBLE_AGENTIC_API_DOCS_DIR (title, version, servers, path count)."""
    store = get_store()
    if not store.api_dir.is_dir():
        return json.dumps(
            {
                "error": "api_dir_not_found",
                "api_dir": str(store.api_dir),
                "hint": "Set TRIMBLE_AGENTIC_API_DOCS_DIR to the folder with agents.json, tools.json, etc.",
            },
            indent=2,
        )
    rows = store.list_all_summaries()
    payload: dict[str, Any] = {"specs": rows, "api_dir": str(store.api_dir)}
    man = summarize_openapi_manifest(store.api_dir)
    if man is not None:
        payload["openapi_manifest"] = man
    return truncate_json_response(payload, 120_000)


@mcp.tool()
def search_operations(query: str, spec_id: str | None = None, limit: int = 35) -> str:
    """
    Search across operation path, method, operationId, summary, and tags (substring match, case-insensitive).
    spec_id: optional filter (e.g. agents, tools, evals, ingest, knowledge-base, model-control-plane, modes-inference).
    """
    lim = max(1, min(int(limit), 200))
    store = get_store()
    hits = store.search_operations(query, spec_id=spec_id, limit=lim)
    payload = {"count": len(hits), "results": hits}
    return truncate_json_response(payload, 120_000)


@mcp.tool()
def get_operation_details(spec_id: str, path: str, method: str) -> str:
    """Return one OpenAPI operation object (parameters, requestBody, responses, security, etc.)."""
    store = get_store()
    op = store.get_operation(spec_id, path, method)
    if op is None:
        return json.dumps(
            {"error": "not_found", "spec_id": spec_id, "path": path, "method": method},
            indent=2,
        )
    return truncate_json_response(op, 200_000)


@mcp.tool()
def get_api_operation_guide(
    spec_id: str,
    path: str,
    method: str,
    include_request_schema: bool = True,
    include_response_codes: bool = True,
    max_schema_depth: int = 2,
) -> str:
    """
    Single structured view for implementing one operation: servers, security, merged parameters,
    request body schema summary (shallow), and per-status response summaries. Prefer this before
    drilling into resolve_schema_ref for full component trees.
    """
    store = get_store()
    md = max(0, min(int(max_schema_depth), 6))
    guide = build_operation_guide(
        store,
        spec_id,
        path,
        method,
        include_request_schema=include_request_schema,
        include_response_codes=include_response_codes,
        max_schema_depth=md,
    )
    if guide is None:
        return json.dumps(
            {"error": "not_found", "spec_id": spec_id, "path": path, "method": method},
            indent=2,
        )
    return truncate_json_response(guide, 200_000)


@mcp.tool()
def get_spec_description(spec_id: str, max_chars: int = 24_000) -> str:
    """Return info.title, info.version, and info.description (markdown) for narrative / auth / concepts."""
    mc = max(2_000, min(int(max_chars), 400_000))
    store = get_store()
    doc = store.get_doc(spec_id)
    info = doc.get("info") or {}
    return truncate_json_response(
        {
            "spec_id": spec_id,
            "title": info.get("title"),
            "version": info.get("version"),
            "description": info.get("description"),
        },
        mc,
    )


@mcp.tool()
def list_schema_component_names(spec_id: str, limit: int = 300) -> str:
    """List names under components/schemas for a spec (useful before resolve_schema_ref)."""
    lim = max(1, min(int(limit), 2000))
    store = get_store()
    names = store.list_schema_names(spec_id, limit=lim)
    return json.dumps({"spec_id": spec_id, "schema_names": names, "count": len(names)}, indent=2)


@mcp.tool()
def resolve_schema_ref(spec_id: str, ref: str) -> str:
    """
    Resolve an internal OpenAPI JSON Pointer, e.g. '#/components/schemas/ServerCreate'.
    Follows a single chain of $ref up to max depth.
    """
    store = get_store()
    try:
        node = store.resolve_internal_ref(spec_id, ref)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": str(e), "spec_id": spec_id, "ref": ref}, indent=2)
    return truncate_json_response(node, 200_000)


@mcp.tool()
def list_documentation_urls() -> str:
    """Return the contents of urls.txt (human docs + API index URLs on developer.stage.trimble-ai.com)."""
    store = get_store()
    body = store.read_urls_file()
    return _truncate(body, 120_000)


@mcp.resource("trimble-agentic://catalog")
def resource_catalog() -> str:
    """JSON list of available OpenAPI specs and server URLs."""
    store = get_store()
    return json.dumps({"specs": store.list_all_summaries(), "api_dir": str(store.api_dir)}, indent=2)


@mcp.resource("trimble-agentic://urls")
def resource_urls() -> str:
    """Curated developer portal URLs (same as urls.txt when found)."""
    return get_store().read_urls_file()


@mcp.resource("trimble-agentic://spec/{spec_id}/info")
def resource_spec_info(spec_id: str) -> str:
    """OpenAPI info object (title, version, contact, license) without full paths."""
    try:
        store = get_store()
        doc = store.get_doc(spec_id)
        return json.dumps(doc.get("info") or {}, indent=2)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": str(e), "spec_id": spec_id}, indent=2)


@mcp.resource("trimble-agentic://spec/{spec_id}/paths-index")
def resource_paths_index(spec_id: str) -> str:
    """Compact index: path, method, operationId, summary, tags for every operation."""
    try:
        store = get_store()
        rows = store.get_paths_index(spec_id)
        return json.dumps(rows, indent=2)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": str(e), "spec_id": spec_id}, indent=2)


@mcp.resource("trimble-agentic://spec/{spec_id}/description")
def resource_spec_description(spec_id: str) -> str:
    """Long-form markdown API guide from info.description."""
    try:
        store = get_store()
        info = store.get_doc(spec_id).get("info") or {}
        desc = info.get("description")
        if not isinstance(desc, str):
            return ""
        return _truncate(desc, 500_000)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": str(e), "spec_id": spec_id}, indent=2)


@mcp.resource("trimble-agentic://config/paths")
def resource_config_paths() -> str:
    """OpenAPI directory, urls.txt, dev-docs cache dir, and this server's Streamable HTTP MCP URL."""
    store = get_store()
    h, port, path = mcp.settings.host, mcp.settings.port, mcp.settings.streamable_http_path
    return json.dumps(
        {
            "api_dir": str(store.api_dir),
            "urls_file": str(store.urls_file),
            "dev_docs_cache_dir": str(_default_dev_docs_cache_dir()),
            "streamable_http_mcp_url": f"http://{h}:{port}{path}",
        },
        indent=2,
    )


@mcp.resource("trimble-agentic://dev-docs/inventory")
def resource_dev_docs_inventory() -> str:
    """Cached narrative docs (manifest + page list)."""
    cache = _default_dev_docs_cache_dir()
    man = load_dev_docs_manifest(cache)
    return json.dumps(
        {"cache_dir": str(cache), "manifest": man, "pages": list_dev_docs_pages(cache)},
        indent=2,
    )


def _register_optional_admin_tools() -> None:
    flag = os.environ.get("TRIMBLE_AGENTIC_MCP_ADMIN_TOOLS", "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return
    from trimble_agentic_docs_mcp.admin_tools import register_admin_tools

    register_admin_tools(mcp)


_register_optional_admin_tools()


def main() -> None:
    parser = argparse.ArgumentParser(prog="trimble-agentic-docs-mcp")
    parser.add_argument(
        "--host",
        default=None,
        metavar="ADDR",
        help="Bind address for HTTP transports (overrides TRIMBLE_AGENTIC_MCP_HOST).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        metavar="PORT",
        help="Bind port for HTTP transports (overrides TRIMBLE_AGENTIC_MCP_PORT).",
    )
    parser.add_argument(
        "--streamable-http-path",
        dest="streamable_http_path",
        default=None,
        metavar="PATH",
        help="URL path for streamable HTTP (overrides TRIMBLE_AGENTIC_MCP_PATH).",
    )
    args = parser.parse_args()

    if args.host is not None:
        mcp.settings.host = args.host
    if args.port is not None:
        mcp.settings.port = args.port
    if args.streamable_http_path is not None:
        p = args.streamable_http_path
        mcp.settings.streamable_http_path = p if p.startswith("/") else f"/{p}"

    h, port, path = mcp.settings.host, mcp.settings.port, mcp.settings.streamable_http_path
    print(
        f"trimble-agentic-docs-mcp: streamable HTTP at http://{h}:{port}{path}",
        file=sys.stderr,
    )
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
