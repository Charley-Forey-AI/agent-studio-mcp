# agent-studio-mcp

Local assets for building against the **Trimble Agentic AI** developer APIs: exported OpenAPI JSON, portal URLs, optional cached narrative docs, and a **public** MCP server (Streamable HTTP) that helps agents discover APIs and schemas without exposing operator workflows.

## Layout

| Path | Purpose |
|------|--------|
| `docs/api/` | OpenAPI 3.x JSON (`agents.json`, `tools.json`, …) — primary MCP source |
| `docs/urls.txt` | Curated developer portal links |
| `docs/mcp-llms-full.txt` | Optional MCP reference material (large) |
| `trimble-agentic-docs-mcp/` | Python MCP package (Streamable HTTP only) |
| `docs/cached/dev-portal/` | Optional: extracted text from **## Docs** (bundle in releases or CI) |

## For integrators (default public MCP)

```bash
cd trimble-agentic-docs-mcp
pip install -e .
python -m trimble_agentic_docs_mcp
```

Point your MCP client at the URL printed on stderr (e.g. `http://127.0.0.1:8305/mcp`). Merge [`trimble-agentic-docs-mcp/examples/mcp-cursor-config.example.json`](trimble-agentic-docs-mcp/examples/mcp-cursor-config.example.json) into Cursor MCP settings.

**Public tool surface (read-only):** discover specs (`list_api_specs` — includes **`openapi_manifest`** when `docs/api/_openapi_manifest.json` exists from sync), search operations (`search_operations`), structured operation view (`get_api_operation_guide`), raw operation JSON (`get_operation_details`), service guides (`get_spec_description`), schema names and refs (`list_schema_component_names`, `resolve_schema_ref`), portal link list (`list_documentation_urls`), and optional bundled narrative docs (`list_cached_dev_docs`, `search_dev_documentation`, `get_dev_docs_page`). Large JSON tool responses may return an object with **`truncated`: true** and a **`preview`** string instead of the full payload; narrow the query or fetch one operation/schema. MCP **resources** are listed in the server `instructions` string in [`trimble_agentic_docs_mcp/server.py`](trimble-agentic-docs-mcp/trimble_agentic_docs_mcp/server.py).

CLI flags `--host`, `--port`, and `--streamable-http-path` override bind settings. Other environment variables are documented in that same module docstring.

### Agent playbook (coding with this MCP)

1. **Order of operations:** `list_api_specs` (note `spec_id`, `servers[].url`) → `search_operations` → **`get_api_operation_guide`** for the target path/method → `get_operation_details` / **`resolve_schema_ref`** when you need full OpenAPI or a specific `#/components/schemas/...` tree → `get_spec_description` for service-wide auth and concepts → `search_dev_documentation` / `get_dev_docs_page` when narrative cache exists → `list_documentation_urls` for live portal links.
2. **Staleness:** Answers reflect whatever is on disk under `docs/api/`. Use **`openapi_manifest`** inside `list_api_specs` (after a successful `trimble-agentic-openapi-sync`) to see per-spec **`fetched_at`** / **`etag`**. Restart the MCP process after refreshing files on disk.
3. **Truncation:** If a tool returns JSON with **`truncated`: true**, treat **`preview`** as incomplete; refine `search_operations` / limits or call **`resolve_schema_ref`** for one schema.
4. **Empty narrative cache:** If `search_dev_documentation` returns no pages and a hint about missing cache, use **`list_documentation_urls`** or ship **`docs/cached/dev-portal/`** in your distribution.

### Tests and CI

From `trimble-agentic-docs-mcp/`:

```bash
pip install -e ".[dev]"
python -m pytest -q
```

GitHub Actions workflow [`.github/workflows/trimble-agentic-docs-mcp.yml`](.github/workflows/trimble-agentic-docs-mcp.yml) runs the same on pushes and PRs that touch the package.

### Remote server (systemd + nginx)

Default bind port is **8305** (override with `TRIMBLE_AGENTIC_MCP_PORT`). For a public path such as `http://52.13.6.105/mcp/agent-studio`, set **`TRIMBLE_AGENTIC_MCP_PATH=/mcp/agent-studio`** on the MCP service so it matches nginx. Set **`TRIMBLE_AGENTIC_MCP_ALLOWED_HOSTS`** to the same hostname or IP clients use in the URL (e.g. `52.13.6.105`) so the MCP library accepts the `Host` header from nginx (otherwise Cursor sees **421 Invalid Host header**). Copy and adapt:

- [`trimble-agentic-docs-mcp/examples/trimble-agentic-docs-mcp.service.example`](trimble-agentic-docs-mcp/examples/trimble-agentic-docs-mcp.service.example)
- [`trimble-agentic-docs-mcp/examples/nginx-mcp-agent-studio.conf.example`](trimble-agentic-docs-mcp/examples/nginx-mcp-agent-studio.conf.example)

## For operators (refreshing artifacts — not part of the public tool list)

Ship fresh `docs/api/` and optional `docs/cached/dev-portal/` using the CLI (CI or secure workstation), not end-user MCP tools:

```bash
cd trimble-agentic-docs-mcp
pip install -e .
set TRIMBLE_AGENTIC_SYNC_BEARER_TOKEN=your_access_token   # if the portal returns 401
trimble-agentic-openapi-sync --dry-run
trimble-agentic-openapi-sync
```

`--dry-run` always exits 0 so CI can smoke-test without secrets (401s still appear in JSON). Non-dry syncs exit non-zero if any write fails.

Flags: `--openapi-only`, `--dev-docs-only`, `--dry-run`, `--dev-docs-cache-dir`, `--if-changed` (skip full GET when HEAD ETag matches the saved manifest; off by default for this CLI).

This updates `docs/api/*.json` plus `docs/api/_openapi_manifest.json`, and `docs/cached/dev-portal/pages/*.json` plus `manifest.json`.

### Weekly refresh (hands-off)

Use **`trimble-agentic-docs-refresh`** for scheduled jobs (Kubernetes CronJob, systemd timer, Windows Task Scheduler, or a small VM). By default it runs **once** with **ETag-aware skips** so unchanged specs and doc pages are not re-downloaded. Output is JSON on stdout; progress logs go to stderr.

```bash
trimble-agentic-docs-refresh
# Force full re-download (ignore ETags):
trimble-agentic-docs-refresh --full
# Long-lived container: sleep 168 hours between cycles (default):
trimble-agentic-docs-refresh --daemon --interval-hours 168
```

Set **`TRIMBLE_AGENTIC_SYNC_BEARER_TOKEN`** when the portal returns 401, same as the sync CLI. After artifacts change on disk, **restart the MCP process** (or rely on your deployment’s file reload policy) so long-running servers pick up new OpenAPI and cached docs.

#### Automatic token refresh for scheduled jobs

`trimble-agentic-docs-refresh` can mint a fresh bearer token before every cycle so weekly jobs do not fail on token expiry.

Set these env vars on the refresh job host:

- **Required:** `TRIMBLE_AGENTIC_SYNC_OAUTH_TOKEN_URL`, `TRIMBLE_AGENTIC_SYNC_OAUTH_CLIENT_ID`
- **Optional (client credentials):** `TRIMBLE_AGENTIC_SYNC_OAUTH_CLIENT_SECRET`, `TRIMBLE_AGENTIC_SYNC_OAUTH_SCOPE`
- **Optional (refresh token flow):** `TRIMBLE_AGENTIC_SYNC_OAUTH_REFRESH_TOKEN`
- **Optional tuning:** `TRIMBLE_AGENTIC_SYNC_OAUTH_GRANT_TYPE` (`client_credentials` or `refresh_token`), `TRIMBLE_AGENTIC_SYNC_OAUTH_CLIENT_AUTH` (`body` or `basic`), `TRIMBLE_AGENTIC_SYNC_OAUTH_AUDIENCE`, `TRIMBLE_AGENTIC_SYNC_OAUTH_RESOURCE`

Behavior:

- If `TRIMBLE_AGENTIC_SYNC_OAUTH_TOKEN_URL` is set, refresh runner obtains a new access token each cycle and exports it to `TRIMBLE_AGENTIC_SYNC_BEARER_TOKEN` in-process.
- If OAuth vars are not set, runner uses any pre-set `TRIMBLE_AGENTIC_SYNC_BEARER_TOKEN` as-is.
- Default grant selection is `refresh_token` when `TRIMBLE_AGENTIC_SYNC_OAUTH_REFRESH_TOKEN` is present, otherwise `client_credentials`.

Example (client credentials):

```bash
export TRIMBLE_AGENTIC_SYNC_OAUTH_TOKEN_URL="https://stage.id.trimblecloud.com/oauth2/token"
export TRIMBLE_AGENTIC_SYNC_OAUTH_CLIENT_ID="your_client_id"
export TRIMBLE_AGENTIC_SYNC_OAUTH_CLIENT_SECRET="your_client_secret"
export TRIMBLE_AGENTIC_SYNC_OAUTH_SCOPE="openid agents tools models kb"
trimble-agentic-docs-refresh --if-changed
```

Use a secret manager or systemd credential/env-file mechanism for secrets; do not commit tokens or client secrets to git.

Systemd examples:

- [`trimble-agentic-docs-mcp/examples/trimble-agentic-docs-refresh.service.example`](trimble-agentic-docs-mcp/examples/trimble-agentic-docs-refresh.service.example)
- [`trimble-agentic-docs-mcp/examples/trimble-agentic-docs-refresh.timer.example`](trimble-agentic-docs-mcp/examples/trimble-agentic-docs-refresh.timer.example)
- [`trimble-agentic-docs-mcp/examples/trimble-agentic-docs-refresh.env.example`](trimble-agentic-docs-mcp/examples/trimble-agentic-docs-refresh.env.example)

### Optional in-process admin MCP tools

If you must sync from the running MCP process (unusual), set **`TRIMBLE_AGENTIC_MCP_ADMIN_TOOLS=1`** on the server. That exposes `refresh_api_docs_cache`, `get_openapi_sync_status`, `sync_openapi_from_upstream`, `sync_dev_docs_from_urls`, and `sync_all_upstream_content`. Non-dry writes still require **`TRIMBLE_AGENTIC_ALLOW_NETWORK=1`**. Public/hosted deployments should leave admin tools **disabled** (default).
