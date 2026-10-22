"""Unified Extractor Registry that coordinates AST, OpenAPI, and Infrastructure extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from varuna.extractors.base import BaseASTExtractor
from varuna.extractors.infra_scanner import scan_infrastructure_and_manifests
from varuna.extractors.openapi_parser import (
    discover_openapi_spec,
    merge_openapi_into_nodes,
    parse_openapi_spec,
)
from varuna.extractors.python_extractor import PythonASTExtractor
from varuna.extractors.ruby_extractor import RubyASTExtractor
from varuna.extractors.ts_extractor import TypeScriptASTExtractor
from varuna.models.graph_schema import CodeNode, ExternalBoundary, RouteEndpoint

DEFAULT_EXCLUDED_DIRS: Set[str] = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "out",
    "target",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "vendor",
    "coverage",
    ".next",
    ".nuxt",
}


class ExtractorRegistry:
    """Coordinates all language AST extractors, optional OpenAPI specs, and infrastructure manifests."""

    def __init__(self, include_tests: bool = False) -> None:
        self.include_tests = include_tests
        self.extractors: List[BaseASTExtractor] = [
            PythonASTExtractor(),
            TypeScriptASTExtractor(),
            RubyASTExtractor(),
        ]

    def extract_all(
        self,
        repo_root: Path,
        openapi_path: Optional[Path] = None,
    ) -> Tuple[Dict[str, CodeNode], Dict[str, ExternalBoundary], List[RouteEndpoint]]:
        nodes: Dict[str, CodeNode] = {}

        for file_path in self._iter_source_files(repo_root):
            for extractor in self.extractors:
                if extractor.supports_file(file_path):
                    code_node = extractor.extract_file(repo_root, file_path)
                    if file_path.name in ("__init__.py", "index.ts", "index.js") and not (
                        code_node.classes or code_node.functions or code_node.routes
                    ):
                        break
                    nodes[code_node.id] = code_node
                    break

        external_boundaries = scan_infrastructure_and_manifests(repo_root)

        spec_file = openapi_path or discover_openapi_spec(repo_root)
        spec_routes = parse_openapi_spec(spec_file) if spec_file else []
        unmatched_routes = merge_openapi_into_nodes(spec_routes, nodes)

        return nodes, external_boundaries, unmatched_routes

    def _iter_source_files(self, repo_root: Path) -> Iterable[Path]:
        for path in sorted(repo_root.rglob("*")):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(repo_root).parts
            if any(part in DEFAULT_EXCLUDED_DIRS or part.startswith(".") for part in rel_parts[:-1]):
                continue
            if not self.include_tests:
                if any(
                    part.lower() in ("tests", "test", "spec", "specs", "fixtures", "__tests__")
                    for part in rel_parts[:-1]
                ):
                    continue
                if path.name.startswith("test_") or path.name.endswith(
                    ("_test.py", ".spec.ts", ".test.ts", "_spec.rb")
                ):
                    continue
            yield path
