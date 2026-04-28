"""
Scheduled artifact refresh for operators (CronJob, systemd timer, or long-lived daemon).

Default: one run with ETag-aware skips (--if-changed). Use --full to ignore ETags.
Public MCP servers read the updated files on disk; restart the MCP if it does not pick up changes.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

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


def _parse_token_response(resp: httpx.Response) -> tuple[str | None, int | None, str | None]:
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    access_token = payload.get("access_token")
    expires_in_raw = payload.get("expires_in")
    expires_in: int | None = None
    if isinstance(expires_in_raw, int):
        expires_in = expires_in_raw
    elif isinstance(expires_in_raw, str) and expires_in_raw.isdigit():
        expires_in = int(expires_in_raw)
    err = payload.get("error_description") or payload.get("error")
    if not isinstance(access_token, str) or not access_token.strip():
        return None, expires_in, str(err) if err is not None else None
    return access_token.strip(), expires_in, str(err) if err is not None else None


def _refresh_bearer_token_from_oauth(timeout_s: float = 30.0) -> dict[str, Any]:
    """
    Optionally mint TRIMBLE_AGENTIC_SYNC_BEARER_TOKEN from OAuth before each cycle.

    Enabled when TRIMBLE_AGENTIC_SYNC_OAUTH_TOKEN_URL is set.
    Grant defaults to refresh_token when TRIMBLE_AGENTIC_SYNC_OAUTH_REFRESH_TOKEN is present,
    otherwise client_credentials.
    """
    token_url = (os.environ.get("TRIMBLE_AGENTIC_SYNC_OAUTH_TOKEN_URL") or "").strip()
    if not token_url:
        return {"mode": "env-bearer", "used_oauth": False}

    client_id = (os.environ.get("TRIMBLE_AGENTIC_SYNC_OAUTH_CLIENT_ID") or "").strip()
    client_secret = os.environ.get("TRIMBLE_AGENTIC_SYNC_OAUTH_CLIENT_SECRET")
    refresh_token = (os.environ.get("TRIMBLE_AGENTIC_SYNC_OAUTH_REFRESH_TOKEN") or "").strip()
    grant_type = (os.environ.get("TRIMBLE_AGENTIC_SYNC_OAUTH_GRANT_TYPE") or "").strip().lower()
    scope = (os.environ.get("TRIMBLE_AGENTIC_SYNC_OAUTH_SCOPE") or "").strip()
    audience = (os.environ.get("TRIMBLE_AGENTIC_SYNC_OAUTH_AUDIENCE") or "").strip()
    resource = (os.environ.get("TRIMBLE_AGENTIC_SYNC_OAUTH_RESOURCE") or "").strip()
    client_auth = (os.environ.get("TRIMBLE_AGENTIC_SYNC_OAUTH_CLIENT_AUTH") or "body").strip().lower()

    if grant_type not in ("", "client_credentials", "refresh_token"):
        return {
            "mode": "oauth",
            "used_oauth": True,
            "error": "invalid_grant_type",
            "hint": "TRIMBLE_AGENTIC_SYNC_OAUTH_GRANT_TYPE must be client_credentials or refresh_token",
        }
    if not grant_type:
        grant_type = "refresh_token" if refresh_token else "client_credentials"
    if grant_type == "refresh_token" and not refresh_token:
        return {
            "mode": "oauth",
            "used_oauth": True,
            "grant_type": grant_type,
            "error": "missing_refresh_token",
            "hint": "Set TRIMBLE_AGENTIC_SYNC_OAUTH_REFRESH_TOKEN or use client_credentials grant",
        }
    if not client_id:
        return {
            "mode": "oauth",
            "used_oauth": True,
            "grant_type": grant_type,
            "error": "missing_client_id",
            "hint": "Set TRIMBLE_AGENTIC_SYNC_OAUTH_CLIENT_ID",
        }

    data: dict[str, str] = {"grant_type": grant_type, "client_id": client_id}
    if grant_type == "refresh_token":
        data["refresh_token"] = refresh_token
    if scope:
        data["scope"] = scope
    if audience:
        data["audience"] = audience
    if resource:
        data["resource"] = resource

    auth: tuple[str, str] | None = None
    if client_secret:
        if client_auth == "basic":
            auth = (client_id, client_secret)
            data.pop("client_id", None)
        else:
            data["client_secret"] = client_secret

    headers = {"Accept": "application/json"}
    try:
        with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
            resp = client.post(token_url, data=data, headers=headers, auth=auth)
    except httpx.HTTPError as e:
        return {
            "mode": "oauth",
            "used_oauth": True,
            "grant_type": grant_type,
            "token_url": token_url,
            "error": f"http_error:{e!s}",
        }

    access_token, expires_in, token_error = _parse_token_response(resp)
    if resp.status_code != 200 or not access_token:
        return {
            "mode": "oauth",
            "used_oauth": True,
            "grant_type": grant_type,
            "token_url": token_url,
            "http_status": resp.status_code,
            "error": token_error or f"oauth_http_{resp.status_code}",
        }

    os.environ["TRIMBLE_AGENTIC_SYNC_BEARER_TOKEN"] = access_token
    out: dict[str, Any] = {
        "mode": "oauth",
        "used_oauth": True,
        "grant_type": grant_type,
        "token_url": token_url,
        "token_set": True,
    }
    if expires_in is not None:
        out["expires_in_s"] = expires_in
    return out


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
    from trimble_agentic_docs_mcp.repo_env import load_optional_repo_env

    load_optional_repo_env()
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
        auth_result = _refresh_bearer_token_from_oauth()
        if auth_result.get("error"):
            print(json.dumps({"auth": auth_result}, indent=2))
            _log.error("auth bootstrap failed: %s", auth_result.get("error"))
            return 1

        payload = run_refresh_cycle(
            api_dir=api_dir,
            urls=urls,
            cache_dir=cache_dir,
            dry_run=args.dry_run,
            if_changed=if_changed,
            openapi_only=args.openapi_only,
            dev_docs_only=args.dev_docs_only,
        )
        payload["result"]["auth"] = auth_result
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
