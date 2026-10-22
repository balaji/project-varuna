"""Unified Extractor Registry that coordinates AST, OpenAPI, and Infrastructure extraction."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

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

logger = logging.getLogger(__name__)

DEFAULT_EXCLUDED_DIRS: set[str] = {
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
        self.extractors: list[BaseASTExtractor] = [
            PythonASTExtractor(),
            TypeScriptASTExtractor(),
            RubyASTExtractor(),
        ]

    def extract_all(
        self,
        repo_root: Path,
        openapi_path: Path | None = None,
    ) -> tuple[dict[str, CodeNode], dict[str, ExternalBoundary], list[RouteEndpoint]]:
        nodes: dict[str, CodeNode] = {}

        logger.info(
            "[Stage 1/5] Scanning repository source files in %s (include_tests=%s)...",
            repo_root,
            self.include_tests,
        )
        skipped_empty_inits = 0
        for file_path in self._iter_source_files(repo_root):
            for extractor in self.extractors:
                if extractor.supports_file(file_path):
                    code_node = extractor.extract_file(repo_root, file_path)
                    if file_path.name in ("__init__.py", "index.ts", "index.js") and not (
                        code_node.classes or code_node.functions or code_node.routes
                    ):
                        skipped_empty_inits += 1
                        logger.debug(
                            "  Skipped empty barrel/init file: %s",
                            code_node.relative_path,
                        )
                        break
                    nodes[code_node.id] = code_node
                    logger.debug(
                        "  Extracted module '%s' (%s, %d LOC): %d imports, %d classes, %d functions, %d routes, %d entrypoints",
                        code_node.id,
                        code_node.language.value,
                        code_node.lines_of_code,
                        len(code_node.raw_imports),
                        len(code_node.classes),
                        len(code_node.functions),
                        len(code_node.routes),
                        len(code_node.entrypoints),
                    )
                    break

        logger.info(
            "[Stage 1/5] AST extraction complete: %d source modules extracted (%d empty init/index files skipped).",
            len(nodes),
            skipped_empty_inits,
        )

        logger.info("[Stage 1/5] Scanning infrastructure manifests and dependency files...")
        external_boundaries = scan_infrastructure_and_manifests(repo_root)
        logger.info(
            "[Stage 1/5] Discovered %d external/datastore boundaries from manifests.",
            len(external_boundaries),
        )

        spec_file = openapi_path or discover_openapi_spec(repo_root)
        if spec_file:
            logger.info("[Stage 1/5] Parsing OpenAPI specification at %s...", spec_file)
            spec_routes = parse_openapi_spec(spec_file)
            unmatched_routes = merge_openapi_into_nodes(spec_routes, nodes)
            logger.info(
                "[Stage 1/5] OpenAPI merge complete: %d total spec routes (%d unmatched).",
                len(spec_routes),
                len(unmatched_routes),
            )
        else:
            logger.info(
                "[Stage 1/5] No OpenAPI specification provided or auto-discovered; relying on AST + topological entrypoint discovery."
            )
            unmatched_routes = []

        return nodes, external_boundaries, unmatched_routes

    def _iter_source_files(self, repo_root: Path) -> Iterable[Path]:
        for path in sorted(repo_root.rglob("*")):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(repo_root).parts
            if any(
                part in DEFAULT_EXCLUDED_DIRS or part.startswith(".") for part in rel_parts[:-1]
            ):
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
