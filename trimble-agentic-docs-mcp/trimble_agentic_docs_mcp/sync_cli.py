"""CLI: sync OpenAPI JSON and/or cached developer docs from the portal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from trimble_agentic_docs_mcp.dev_docs_sync import _default_dev_docs_cache_dir, run_dev_docs_sync
from trimble_agentic_docs_mcp.store import _default_api_dir, _default_urls_file
from trimble_agentic_docs_mcp.upstream_sync import run_openapi_sync


def main() -> None:
    from trimble_agentic_docs_mcp.repo_env import load_optional_repo_env

    load_optional_repo_env()
    parser = argparse.ArgumentParser(
        description="Refresh local OpenAPI specs (/api/*) and/or extracted /docs HTML (see urls.txt).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate remote responses but do not write files.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--openapi-only",
        action="store_true",
        help="Only sync OpenAPI JSON into TRIMBLE_AGENTIC_API_DOCS_DIR.",
    )
    mode.add_argument(
        "--dev-docs-only",
        action="store_true",
        help="Only sync ## Docs URLs into TRIMBLE_AGENTIC_DEV_DOCS_CACHE_DIR.",
    )
    parser.add_argument(
        "--api-dir",
        type=str,
        default=None,
        help="Override TRIMBLE_AGENTIC_API_DOCS_DIR.",
    )
    parser.add_argument(
        "--urls-file",
        type=str,
        default=None,
        help="Override TRIMBLE_AGENTIC_URLS_FILE.",
    )
    parser.add_argument(
        "--dev-docs-cache-dir",
        type=str,
        default=None,
        help="Override TRIMBLE_AGENTIC_DEV_DOCS_CACHE_DIR.",
    )
    parser.add_argument(
        "--if-changed",
        action="store_true",
        help="Use HEAD + ETag to skip download when manifest shows no change (saves bandwidth).",
    )
    args = parser.parse_args()

    api_dir = Path(args.api_dir).resolve() if args.api_dir else _default_api_dir()
    urls = Path(args.urls_file).resolve() if args.urls_file else _default_urls_file()
    cache_dir = Path(args.dev_docs_cache_dir).resolve() if args.dev_docs_cache_dir else _default_dev_docs_cache_dir()

    run_openapi = not args.dev_docs_only
    run_docs = not args.openapi_only
    exit_code = 0
    out: dict[str, Any] = {}

    if_changed = bool(args.if_changed)

    if run_openapi:
        o = run_openapi_sync(api_dir=api_dir, urls_file=urls, dry_run=args.dry_run, if_changed=if_changed)
        out["openapi"] = o
        if o.get("error_count"):
            exit_code = 1

    if run_docs:
        d = run_dev_docs_sync(urls_file=urls, cache_dir=cache_dir, dry_run=args.dry_run, if_changed=if_changed)
        out["dev_docs"] = d
        if d.get("error_count"):
            exit_code = 1
        if "error" in d and d["error"] in ("urls_file_missing", "no_docs_urls"):
            exit_code = 1

    print(json.dumps(out, indent=2))
    # Dry-run is for connectivity checks; 401 without a token is expected on stage — do not fail CI.
    if args.dry_run:
        sys.exit(0)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
