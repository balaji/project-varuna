"""NetworkX dependency graph builder and unified graph-science pipeline coordinator."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import networkx as nx

from varuna.extractors.infra_scanner import KNOWN_EXTERNAL_DEPENDENCIES
from varuna.extractors.registry import ExtractorRegistry
from varuna.graph.centrality import analyze_centrality_and_topology
from varuna.graph.clustering import discover_c3_components
from varuna.graph.drift import analyze_architecture_drift
from varuna.models.graph_schema import (
    EDGE_BASE_WEIGHTS,
    C3Component,
    CodeNode,
    DependencyEdge,
    DriftIssue,
    EdgeKind,
    ExternalBoundary,
    Language,
    RouteEndpoint,
)

logger = logging.getLogger(__name__)


class GraphBuilder:
    """Constructs a weighted NetworkX DiGraph from extracted repository facts and runs network science analysis."""

    def __init__(
        self,
        repo_root: Path,
        openapi_path: Path | None = None,
        include_tests: bool = False,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.openapi_path = openapi_path
        self.include_tests = include_tests
        self.registry = ExtractorRegistry(include_tests=include_tests)

    def build(
        self,
    ) -> tuple[
        nx.DiGraph,
        dict[str, CodeNode],
        list[DependencyEdge],
        dict[str, ExternalBoundary],
        list[RouteEndpoint],
    ]:
        nodes, external_boundaries, unmatched_routes = self.registry.extract_all(
            repo_root=self.repo_root,
            openapi_path=self.openapi_path,
        )

        logger.info(
            "[Stage 2/5] Resolving cross-module dependency edges across %d modules...", len(nodes)
        )
        edges = self._resolve_edges(nodes, external_boundaries)

        graph = nx.DiGraph()
        for node_id, node_obj in nodes.items():
            graph.add_node(node_id, data=node_obj)

        for edge in edges:
            if edge.source == edge.target:
                continue
            if edge.source in graph and edge.target in graph:
                if graph.has_edge(edge.source, edge.target):
                    existing_w = graph[edge.source][edge.target]["weight"]
                    graph[edge.source][edge.target]["weight"] = round(
                        max(existing_w, edge.weight) + min(existing_w, edge.weight) * 0.25, 2
                    )
                    graph[edge.source][edge.target]["kinds"].add(edge.kind.value)
                    graph[edge.source][edge.target]["symbols"].update(edge.symbols)
                else:
                    graph.add_edge(
                        edge.source,
                        edge.target,
                        weight=round(edge.weight, 2),
                        kinds={edge.kind.value},
                        symbols=set(edge.symbols),
                    )

        logger.info(
            "[Stage 2/5] Built NetworkX DiGraph: %d nodes, %d directed edges (from %d raw dependency relationships).",
            graph.number_of_nodes(),
            graph.number_of_edges(),
            len(edges),
        )

        return graph, nodes, edges, external_boundaries, unmatched_routes

    def analyze_graph(
        self, resolution: float = 1.0
    ) -> tuple[
        nx.DiGraph,
        dict[str, CodeNode],
        list[DependencyEdge],
        dict[str, ExternalBoundary],
        list[RouteEndpoint],
        list[str],
        list[str],
        list[C3Component],
        float,
        float,
        list[DriftIssue],
    ]:
        """Run full NetworkX graph construction, centrality pruning, Louvain clustering, and drift analysis."""
        graph, nodes, edges, external_boundaries, unmatched_routes = self.build()
        utility_sinks, topological_entrypoints = analyze_centrality_and_topology(graph, nodes)
        components, modularity_score = discover_c3_components(
            graph=graph,
            nodes=nodes,
            external_boundaries=external_boundaries,
            utility_sinks=utility_sinks,
            resolution=resolution,
        )
        alignment_score, drift_issues = analyze_architecture_drift(graph, nodes, components)
        return (
            graph,
            nodes,
            edges,
            external_boundaries,
            unmatched_routes,
            utility_sinks,
            topological_entrypoints,
            components,
            modularity_score,
            alignment_score,
            drift_issues,
        )

    def _resolve_edges(
        self,
        nodes: dict[str, CodeNode],
        external_boundaries: dict[str, ExternalBoundary],
    ) -> list[DependencyEdge]:
        suffix_to_id: dict[str, str] = {}
        symbol_to_ids: dict[str, list[str]] = {}
        stem_to_id: dict[str, str] = {}

        sorted_nodes = sorted(
            nodes.items(),
            key=lambda item: 0 if item[1].relative_path.endswith("__init__.py") else 1,
        )

        for node_id, node in sorted_nodes:
            parts = node_id.split(".")
            for i in range(len(parts)):
                suffix = ".".join(parts[i:])
                suffix_to_id[suffix] = node_id
            stem_to_id[node.name.lower()] = node_id

            for sym in node.exported_symbols:
                if sym.startswith("_"):
                    continue
                symbol_to_ids.setdefault(sym, []).append(node_id)
                if "::" in sym:
                    short_sym = sym.split("::")[-1]
                    symbol_to_ids.setdefault(short_sym, []).append(node_id)

        pair_edges: dict[tuple[str, str, EdgeKind], DependencyEdge] = {}

        def add_edge(src: str, dst: str, kind: EdgeKind, sym: str = "") -> None:
            if not dst or src == dst or dst not in nodes:
                return
            if nodes[dst].relative_path.endswith("__init__.py") and src.startswith(dst + "."):
                return
            key = (src, dst, kind)
            base_w = EDGE_BASE_WEIGHTS[kind]
            if key not in pair_edges:
                pair_edges[key] = DependencyEdge(
                    source=src,
                    target=dst,
                    kind=kind,
                    weight=base_w,
                    symbols=[sym] if sym else [],
                )
            else:
                pair_edges[key].weight += base_w * 0.35
                if sym and sym not in pair_edges[key].symbols:
                    pair_edges[key].symbols.append(sym)

        for src_id, node in nodes.items():
            imported_targets: set[str] = set()

            for raw_imp in node.raw_imports:
                target_id = self._resolve_import_string(src_id, node, raw_imp, suffix_to_id)
                if target_id:
                    imported_targets.add(target_id)
                    add_edge(src_id, target_id, EdgeKind.IMPORTS, raw_imp)
                else:
                    self._match_external_boundary(src_id, raw_imp, external_boundaries)

            for const_sym in node.referenced_constants:
                matched_targets = symbol_to_ids.get(const_sym, [])
                for target_id in matched_targets:
                    if node.language == Language.RUBY or target_id in imported_targets:
                        imported_targets.add(target_id)
                        add_edge(src_id, target_id, EdgeKind.REFERENCES_CONSTANT, const_sym)
                if not matched_targets and node.language == Language.RUBY:
                    snake_path = self._camel_to_snake_dotted(const_sym)
                    if snake_path in suffix_to_id:
                        resolved_rb = suffix_to_id[snake_path]
                        imported_targets.add(resolved_rb)
                        add_edge(src_id, resolved_rb, EdgeKind.REFERENCES_CONSTANT, const_sym)

            for parent_sym in node.mixins_and_parents:
                clean_parent = parent_sym.split(".")[-1].split("::")[-1]
                for target_id in symbol_to_ids.get(clean_parent, []):
                    if node.language == Language.RUBY or target_id in imported_targets:
                        add_edge(src_id, target_id, EdgeKind.INHERITS_OR_MIXIN, parent_sym)

            for assoc in node.associations:
                singular = assoc[:-1] if assoc.endswith("s") else assoc
                for candidate in (assoc.lower(), singular.lower()):
                    if candidate in stem_to_id:
                        add_edge(src_id, stem_to_id[candidate], EdgeKind.ASSOCIATION, assoc)
                        break

            for called in node.called_symbols:
                short_call = called.split(".")[-1]
                if short_call.startswith("_"):
                    continue
                if short_call in symbol_to_ids:
                    for target_id in symbol_to_ids[short_call]:
                        if target_id in imported_targets or (
                            node.language == Language.RUBY and called[0].isupper()
                        ):
                            add_edge(src_id, target_id, EdgeKind.CALLS, called)

        return list(pair_edges.values())

    def _resolve_import_string(
        self,
        src_id: str,
        node: CodeNode,
        raw_imp: str,
        suffix_to_id: dict[str, str],
    ) -> str | None:
        cleaned = raw_imp.strip()
        if not cleaned:
            return None

        if cleaned.startswith("."):
            if node.language in (Language.TYPESCRIPT, Language.RUBY):
                src_dir = Path(node.relative_path).parent
                resolved_rel = (src_dir / cleaned).as_posix()
                norm_parts: list[str] = []
                for p in resolved_rel.split("/"):
                    if p in ("", "."):
                        continue
                    if p == "..":
                        if norm_parts:
                            norm_parts.pop()
                    else:
                        norm_parts.append(p)
                dotted = ".".join(norm_parts)
                if dotted in suffix_to_id:
                    return suffix_to_id[dotted]
            else:
                stripped = cleaned.lstrip(".")
                if stripped in suffix_to_id:
                    return suffix_to_id[stripped]

        if cleaned.startswith("@/"):
            dotted = cleaned[2:].replace("/", ".")
            if dotted in suffix_to_id:
                return suffix_to_id[dotted]

        dotted_imp = cleaned.replace("/", ".").lstrip(".")
        if dotted_imp in suffix_to_id:
            return suffix_to_id[dotted_imp]

        if "." in dotted_imp:
            parent_dotted = ".".join(dotted_imp.split(".")[:-1])
            if parent_dotted in suffix_to_id:
                return suffix_to_id[parent_dotted]

        return None

    def _match_external_boundary(
        self,
        src_id: str,
        raw_imp: str,
        external_boundaries: dict[str, ExternalBoundary],
    ) -> None:
        root_pkg = raw_imp.split(".")[0].split("/")[0].lower().replace("_", "-")
        if root_pkg.startswith("tree-sitter"):
            root_pkg = "tree-sitter"
        if root_pkg in KNOWN_EXTERNAL_DEPENDENCIES:
            name, kind, tech, desc = KNOWN_EXTERNAL_DEPENDENCIES[root_pkg]
            b_id = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
            if b_id not in external_boundaries:
                external_boundaries[b_id] = ExternalBoundary(
                    id=b_id,
                    name=name,
                    kind=kind,
                    technology=tech,
                    description=desc,
                )
            if src_id not in external_boundaries[b_id].referenced_by_modules:
                external_boundaries[b_id].referenced_by_modules.append(src_id)

    @staticmethod
    def _camel_to_snake_dotted(const_str: str) -> str:
        parts = const_str.split("::")
        snake_parts = [re.sub(r"(?<!^)(?=[A-Z])", "_", p).lower() for p in parts if p]
        return ".".join(snake_parts)
