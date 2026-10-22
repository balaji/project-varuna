"""Multi-language AST and specification extractors for Project Varuna."""

from varuna.extractors.base import BaseASTExtractor, compute_module_id, infer_directory_namespace
from varuna.extractors.infra_scanner import scan_infrastructure_and_manifests
from varuna.extractors.openapi_parser import (
    discover_openapi_spec,
    merge_openapi_into_nodes,
    parse_openapi_spec,
)
from varuna.extractors.python_extractor import PythonASTExtractor
from varuna.extractors.ruby_extractor import RubyASTExtractor
from varuna.extractors.ts_extractor import TypeScriptASTExtractor

__all__ = [
    "BaseASTExtractor",
    "PythonASTExtractor",
    "RubyASTExtractor",
    "TypeScriptASTExtractor",
    "compute_module_id",
    "discover_openapi_spec",
    "infer_directory_namespace",
    "merge_openapi_into_nodes",
    "parse_openapi_spec",
    "scan_infrastructure_and_manifests",
]
