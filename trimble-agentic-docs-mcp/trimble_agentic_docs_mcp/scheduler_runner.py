"""
Scheduled artifact refresh for operators (CronJob, systemd timer, or long-lived daemon).

Default: one run with ETag-aware skips (--if-changed). Use --full to ignore ETags.
Public MCP servers read the updated files on disk; restart the MCP if it does not pick up changes.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from trimble_agentic_docs_mcp.dev_docs_sync import _default_dev_docs_cache_dir, run_dev_docs_sync
from trimble_agentic_docs_mcp.store import _default_api_dir, _default_urls_file
from trimble_agentic_docs_mcp.upstream_sync import run_openapi_sync

_log = logging.getLogger("trimble_agentic_docs.scheduler")


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        stream=sys.stderr,
    )


def run_refresh_cycle(
    *,
    api_dir: Path,
    urls: Path,
    cache_dir: Path,
    dry_run: bool,
    if_changed: bool,
    openapi_only: bool,
    dev_docs_only: bool,
) -> dict[str, Any]:
    run_openapi = not dev_docs_only
    run_docs = not openapi_only
    out: dict[str, Any] = {}
    exit_code = 0

    if run_openapi:
        o = run_openapi_sync(api_dir=api_dir, urls_file=urls, dry_run=dry_run, if_changed=if_changed)
        out["openapi"] = o
        _log.info(
            "openapi ok=%s errors=%s skipped=%s",
            o.get("ok_count"),
            o.get("error_count"),
            o.get("skipped_unchanged_count"),
        )
        if o.get("error_count"):
            exit_code = 1

    if run_docs:
        d = run_dev_docs_sync(urls_file=urls, cache_dir=cache_dir, dry_run=dry_run, if_changed=if_changed)
        out["dev_docs"] = d
        _log.info(
            "dev_docs ok=%s errors=%s skipped=%s",
            d.get("ok_count"),
            d.get("error_count"),
            d.get("skipped_unchanged_count"),
        )
        if isinstance(d, dict) and d.get("error_count"):
            exit_code = 1
        if isinstance(d, dict) and d.get("error") in ("urls_file_missing", "no_docs_urls"):
            exit_code = 1

    return {"exit_code": exit_code, "result": out}


def main() -> None:
    _configure_logging()
    parser = argparse.ArgumentParser(
        description="Refresh docs/api and docs/cached (ETag-aware by default). For weekly jobs use --once or --daemon.",
    )
    parser.add_argument("--dry-run", action="store_true", help="No writes; still contacts network.")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Always download full bodies (ignore ETag / manifest skip logic).",
    )
    parser.add_argument(
        "--if-changed",
        dest="if_changed",
        action="store_true",
        default=True,
        help="Skip GET when HEAD ETag matches manifest (default: on).",
    )
    parser.add_argument(
        "--no-if-changed",
        dest="if_changed",
        action="store_false",
        help="Disable ETag optimization (same as --full for OpenAPI; dev docs always re-fetch).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--openapi-only", action="store_true")
    mode.add_argument("--dev-docs-only", action="store_true")
    parser.add_argument("--api-dir", type=str, default=None)
    parser.add_argument("--urls-file", type=str, default=None)
    parser.add_argument("--dev-docs-cache-dir", type=str, default=None)
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run forever, sleeping --interval-hours between cycles (simple VM / container pattern).",
    )
    parser.add_argument(
        "--interval-hours",
        type=float,
        default=168.0,
        help="Sleep between daemon cycles (default 168 = 7 days).",
    )
    args = parser.parse_args()

    api_dir = Path(args.api_dir).resolve() if args.api_dir else _default_api_dir()
    urls = Path(args.urls_file).resolve() if args.urls_file else _default_urls_file()
    cache_dir = Path(args.dev_docs_cache_dir).resolve() if args.dev_docs_cache_dir else _default_dev_docs_cache_dir()

    if_changed = bool(args.if_changed) and not args.full

    def one_shot() -> int:
        payload = run_refresh_cycle(
            api_dir=api_dir,
            urls=urls,
            cache_dir=cache_dir,
            dry_run=args.dry_run,
            if_changed=if_changed,
            openapi_only=args.openapi_only,
            dev_docs_only=args.dev_docs_only,
        )
        print(json.dumps(payload["result"], indent=2))
        code = int(payload["exit_code"])
        if args.dry_run:
            return 0
        return code

    if args.daemon:
        _log.info("daemon mode interval_hours=%s if_changed=%s", args.interval_hours, if_changed)
        while True:
            code = one_shot()
            if code != 0:
                _log.warning("refresh cycle exited with code %s", code)
            sleep_s = max(60.0, float(args.interval_hours) * 3600.0)
            _log.info("sleeping %.0f seconds until next cycle", sleep_s)
            time.sleep(sleep_s)
    else:
        sys.exit(one_shot())


if __name__ == "__main__":
    main()
