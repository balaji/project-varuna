"""Base Tree-sitter AST extractor interface and path normalization helpers."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from tree_sitter import Node

from varuna.models.graph_schema import CodeNode, Language

WRAPPER_DIRS: set[str] = {"src", "lib", "app", "packages", "services"}


def sanitize_boundary_id(raw: str) -> str:
    """Normalize a boundary or route identifier into a safe snake_case token."""
    return re.sub(r"[^a-z0-9_]+", "_", raw.lower()).strip("_")


def compute_module_id(repo_root: Path, file_path: Path) -> str:
    """Compute a canonical dot-separated module ID from a file path relative to repo root."""
    rel = file_path.relative_to(repo_root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] in ("__init__", "index"):
        parts = parts[:-1]
    if not parts:
        return file_path.stem
    return ".".join(parts)


def infer_directory_namespace(repo_root: Path, file_path: Path) -> str:
    """
    Infer the logical architectural subsystem/domain folder for drift comparison.
    Strips generic wrapper directories like `src/<pkg>/` or `app/`.
    """
    rel_parts = list(file_path.relative_to(repo_root).parts[:-1])  # exclude filename
    if not rel_parts:
        return "root"

    # Strip leading 'src', 'lib', 'app'
    idx = 0
    while idx < len(rel_parts) and rel_parts[idx].lower() in WRAPPER_DIRS:
        idx += 1

    remaining = rel_parts[idx:]
    if not remaining:
        return rel_parts[-1]

    # In Python `src/<package_name>/<subsystem>/file.py`, skip `<package_name>` if there's a deeper folder
    if idx > 0 and rel_parts[0].lower() == "src" and len(remaining) >= 2:
        return remaining[1]

    # In Rails `app/models/billing/...` or `app/services/...`
    return remaining[0]


def walk_tree(root: Node) -> Iterator[Node]:
    """Depth-first traversal over all Tree-sitter AST nodes."""
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        yield node
        # Push children in reverse order for pre-order traversal
        for child in reversed(node.children):
            stack.append(child)


def node_text(node: Node, source_bytes: bytes) -> str:
    """Decode UTF-8 slice for a Tree-sitter node."""
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


class BaseASTExtractor(ABC):
    """Abstract base class for language-specific Tree-sitter extractors."""

    language: ClassVar[Language] = Language.UNKNOWN
    extensions: ClassVar[set[str]] = set()

    def supports_file(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.extensions

    @abstractmethod
    def extract_file(self, repo_root: Path, file_path: Path) -> CodeNode:
        """Parse a source file and return a populated CodeNode."""
        raise NotImplementedError
