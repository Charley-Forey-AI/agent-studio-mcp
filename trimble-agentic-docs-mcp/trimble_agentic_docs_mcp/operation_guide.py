"""Build a single JSON 'handbook' object for one OpenAPI operation (developer-focused, bounded size)."""

from __future__ import annotations

from typing import Any

from trimble_agentic_docs_mcp.store import OpenAPIDocStore


def _summarize_schema(
    store: OpenAPIDocStore,
    spec_id: str,
    schema: Any,
    *,
    depth: int,
    max_depth: int,
) -> Any:
    if depth > max_depth:
        return {"_truncated": "max_schema_depth"}
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema and isinstance(schema["$ref"], str):
        resolved = store.resolve_internal_ref(spec_id, schema["$ref"], depth=0, max_depth=max_depth + 2)
        if isinstance(resolved, dict) and resolved.get("_error"):
            return {"$ref": schema["$ref"], "resolve": resolved}
        return _summarize_schema(store, spec_id, resolved, depth=depth + 1, max_depth=max_depth)
    out: dict[str, Any] = {}
    for key in ("type", "title", "description", "format", "enum", "default", "example"):
        if key in schema:
            val = schema[key]
            if key == "enum" and isinstance(val, list) and len(val) > 12:
                out[key] = val[:12] + ["…"]
            else:
                out[key] = val
    if "properties" in schema and isinstance(schema["properties"], dict):
        keys = list(schema["properties"].keys())
        out["properties"] = keys[:80]
        if len(keys) > 80:
            out["properties_omitted"] = len(keys) - 80
    if "items" in schema:
        out["items"] = _summarize_schema(store, spec_id, schema["items"], depth=depth + 1, max_depth=max_depth)
    if "anyOf" in schema or "oneOf" in schema:
        for k in ("anyOf", "oneOf"):
            if k in schema and isinstance(schema[k], list):
                out[k] = [
                    _summarize_schema(store, spec_id, branch, depth=depth + 1, max_depth=max_depth)
                    for branch in schema[k][:6]
                ]
                if len(schema[k]) > 6:
                    out[f"{k}_omitted"] = len(schema[k]) - 6
    if "required" in schema and isinstance(schema["required"], list):
        out["required"] = schema["required"][:50]
    return out if out else schema


def build_operation_guide(
    store: OpenAPIDocStore,
    spec_id: str,
    path: str,
    method: str,
    *,
    include_request_schema: bool,
    include_response_codes: bool,
    max_schema_depth: int,
) -> dict[str, Any] | None:
    opwrap = store.get_operation(spec_id, path, method)
    if opwrap is None:
        return None
    doc = store.get_doc(spec_id)
    op = opwrap["operation"]
    max_d = max(0, min(int(max_schema_depth), 6))

    path_params = opwrap.get("path_level_parameters") or []
    op_params = op.get("parameters") or []
    merged_params: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for p in list(path_params) + list(op_params):
        if not isinstance(p, dict):
            continue
        name = p.get("name")
        pin = p.get("in")
        key = (str(name), str(pin))
        if key in seen:
            continue
        seen.add(key)
        entry: dict[str, Any] = {
            "name": name,
            "in": pin,
            "required": p.get("required", False),
            "description": p.get("description"),
        }
        if include_request_schema and "schema" in p:
            entry["schema"] = _summarize_schema(store, spec_id, p["schema"], depth=0, max_depth=max_d)
        merged_params.append(entry)

    request_bodies: dict[str, Any] = {}
    rb = op.get("requestBody")
    rb_out: dict[str, Any] | None = None
    if isinstance(rb, dict):
        if include_request_schema:
            content = rb.get("content") or {}
            for mt, body in content.items():
                if not isinstance(body, dict):
                    continue
                sch = body.get("schema")
                if sch is not None:
                    request_bodies[str(mt)] = _summarize_schema(store, spec_id, sch, depth=0, max_depth=max_d)
            rb_out = {"required": rb.get("required"), "content": request_bodies}
        else:
            rb_out = {
                "required": rb.get("required"),
                "content_types": list((rb.get("content") or {}).keys()),
            }

    responses_out: dict[str, Any] = {}
    if include_response_codes:
        responses = op.get("responses") or {}
        for code, resp in responses.items():
            if not isinstance(resp, dict):
                continue
            one: dict[str, Any] = {"description": resp.get("description")}
            content = resp.get("content") or {}
            if content:
                one["content_types"] = list(content.keys())
                for mt, body in content.items():
                    if isinstance(body, dict) and "schema" in body:
                        one[f"schema_{mt}"] = _summarize_schema(
                            store, spec_id, body["schema"], depth=0, max_depth=max_d
                        )
            responses_out[str(code)] = one

    return {
        "spec_id": spec_id,
        "path": opwrap["path"],
        "method": opwrap["method"],
        "servers": doc.get("servers", []),
        "operationId": op.get("operationId"),
        "summary": op.get("summary"),
        "description": op.get("description"),
        "tags": op.get("tags"),
        "security": op.get("security"),
        "parameters": merged_params,
        "requestBody": rb_out,
        "responses": responses_out if include_response_codes else {},
    }
