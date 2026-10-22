"""Optional OpenAPI 3.x / Swagger 2.0 parser and route-to-code merger."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional
import yaml

from varuna.extractors.base import sanitize_boundary_id
from varuna.models.graph_schema import CodeNode, RouteEndpoint

CANDIDATE_SPEC_NAMES = (
    "openapi.yaml",
    "openapi.yml",
    "openapi.json",
    "swagger.yaml",
    "swagger.yml",
    "swagger.json",
    "docs/openapi.yaml",
    "docs/openapi.json",
    "api/openapi.yaml",
)

HTTP_METHODS = {"get", "post", "put", "delete", "patch", "options", "head"}


def discover_openapi_spec(repo_root: Path) -> Optional[Path]:
    """Auto-discover an OpenAPI/Swagger spec in common repository locations if one exists."""
    for rel in CANDIDATE_SPEC_NAMES:
        candidate = repo_root / rel
        if candidate.is_file():
            return candidate
    return None


def parse_openapi_spec(spec_path: Path) -> List[RouteEndpoint]:
    """Parse an OpenAPI 3.x or Swagger 2.0 specification into RouteEndpoint objects."""
    if not spec_path or not spec_path.is_file():
        return []

    raw_text = spec_path.read_text(encoding="utf-8", errors="replace")
    if spec_path.suffix.lower() == ".json":
        data = json.loads(raw_text)
    else:
        data = yaml.safe_load(raw_text)

    if not isinstance(data, dict):
        return []

    paths_obj = data.get("paths", {})
    if not isinstance(paths_obj, dict):
        return []

    endpoints: List[RouteEndpoint] = []
    for route_path, methods_map in paths_obj.items():
        if not isinstance(methods_map, dict):
            continue
        for method_key, op_spec in methods_map.items():
            if method_key.lower() not in HTTP_METHODS or not isinstance(op_spec, dict):
                continue
            operation_id = str(op_spec.get("operationId", "")).strip()
            summary = str(op_spec.get("summary") or op_spec.get("description") or "").strip()
            tags = [str(t) for t in op_spec.get("tags", []) if t]
            endpoints.append(
                RouteEndpoint(
                    method=method_key.upper(),
                    path=str(route_path),
                    handler_symbol=operation_id,
                    summary=summary,
                    tags=tags,
                    from_openapi=True,
                )
            )
    return endpoints


def merge_openapi_into_nodes(
    spec_routes: List[RouteEndpoint], nodes: Dict[str, CodeNode]
) -> List[RouteEndpoint]:
    """
    Overlay OpenAPI route metadata (tags, summaries, operationIds) onto AST-discovered code nodes.
    Returns any unmatched OpenAPI routes.
    """
    if not spec_routes:
        return []

    unmatched: List[RouteEndpoint] = []
    for spec_ep in spec_routes:
        matched = False
        norm_spec_path = _normalize_route_path(spec_ep.path)

        # 1. Match against AST-extracted routes by path + method
        for node in nodes.values():
            for code_ep in node.routes:
                if _normalize_route_path(code_ep.path) == norm_spec_path and (
                    code_ep.method == spec_ep.method or code_ep.method in ("HTTP", "REST")
                ):
                    code_ep.tags = list(dict.fromkeys(code_ep.tags + spec_ep.tags))
                    if spec_ep.summary and not code_ep.summary:
                        code_ep.summary = spec_ep.summary
                    code_ep.from_openapi = True
                    matched = True

        # 2. Match by operationId -> function/method/controller name
        if not matched and spec_ep.handler_symbol:
            op_clean = sanitize_boundary_id(spec_ep.handler_symbol)
            for node in nodes.values():
                fn_Matches = [f.lower() for f in node.functions + node.classes]
                if op_clean in fn_Matches or any(op_clean.endswith(f"_{fn}") for fn in fn_Matches):
                    node.routes.append(spec_ep)
                    matched = True
                    break

        if not matched:
            unmatched.append(spec_ep)

    return unmatched


def _normalize_route_path(path_str: str) -> str:
    """Normalize `/users/{id}` and `/users/:id` to `/users/{param}` for matching."""
    import re

    p = path_str.strip().rstrip("/") or "/"
    p = re.sub(r"\{[^}]+\}", "{param}", p)
    p = re.sub(r":([a-zA-Z0-9_]+)", "{param}", p)
    return p.lower()
