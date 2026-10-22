"""Network science centrality metrics, topological entrypoint inference, and utility sink detection."""

from __future__ import annotations

from typing import Dict, List, Set, Tuple
import networkx as nx

from varuna.models.graph_schema import CodeNode, EntrypointInfo, EntrypointKind


def analyze_centrality_and_topology(
    graph: nx.DiGraph,
    nodes: Dict[str, CodeNode],
) -> Tuple[List[str], List[str]]:
    """
    Compute network centrality metrics on the dependency graph and classify:
    1. Topological Entrypoints (Tier 2 fallback: root source nodes with in-degree == 0 and out-degree > 0)
    2. Cross-Cutting Utility Sinks (high cross-namespace in-degree, zero/minimal internal outbound coupling)

    Returns (utility_sink_ids, topological_entrypoint_ids).
    """
    if len(graph) == 0:
        return [], []

    in_deg_cent = nx.in_degree_centrality(graph)
    try:
        pagerank_scores = nx.pagerank(graph, weight="weight")
    except Exception:
        pagerank_scores = {n: 1.0 / len(graph) for n in graph.nodes}

    try:
        hubs, authorities = nx.hits(graph, max_iter=200, normalized=True)
    except Exception:
        hubs = {n: 0.0 for n in graph.nodes}
        authorities = {n: 0.0 for n in graph.nodes}

    utility_sinks: List[str] = []
    topological_entrypoints: List[str] = []

    for node_id in graph.nodes:
        node = nodes[node_id]
        in_d = graph.in_degree(node_id)
        out_d = graph.out_degree(node_id)
        w_in = float(graph.in_degree(node_id, weight="weight"))
        w_out = float(graph.out_degree(node_id, weight="weight"))

        node.in_degree = in_d
        node.out_degree = out_d
        node.weighted_in_degree = round(w_in, 2)
        node.weighted_out_degree = round(w_out, 2)
        node.in_degree_centrality = round(float(in_deg_cent.get(node_id, 0.0)), 4)
        node.pagerank = round(float(pagerank_scores.get(node_id, 0.0)), 4)
        node.hub_score = round(float(hubs.get(node_id, 0.0)), 4)
        node.authority_score = round(float(authorities.get(node_id, 0.0)), 4)

        # Tier 2: Topological Entrypoint Detection
        # A node with zero internal callers (in_degree == 0) that orchestrates internal modules (out_degree >= 1)
        if in_d == 0 and out_d >= 1 and not node.relative_path.endswith("__init__.py"):
            node.is_topological_entrypoint = True
            topological_entrypoints.append(node_id)
            if not any(ep.kind == EntrypointKind.TOPOLOGICAL_SOURCE for ep in node.entrypoints):
                node.entrypoints.append(
                    EntrypointInfo(
                        kind=EntrypointKind.TOPOLOGICAL_SOURCE,
                        name=f"root:{node.name}",
                        detail=f"Topological source node (in_degree=0, out_degree={out_d})",
                    )
                )
        elif node.entrypoints and node_id not in topological_entrypoints:
            topological_entrypoints.append(node_id)

        # Cross-Cutting Utility / Schema Sink Detection:
        # Check how many distinct directory namespaces import this node
        caller_ids = list(graph.predecessors(node_id))
        caller_namespaces: Set[str] = {
            nodes[cid].directory_namespace
            for cid in caller_ids
            if cid in nodes and nodes[cid].directory_namespace != node.directory_namespace
        }

        is_leaf_sink = out_d == 0 and in_d >= 3 and len(caller_namespaces) >= 2
        is_high_fan_in_utility = (
            in_d >= 4
            and len(caller_namespaces) >= 3
            and (out_d == 0 or (in_d / float(out_d + 1)) >= 4.0)
        )

        if is_leaf_sink or is_high_fan_in_utility:
            node.is_utility_sink = True
            node.utility_reason = (
                f"Cross-cutting sink imported by {in_d} modules across "
                f"{len(caller_namespaces) + 1} namespaces (out_degree={out_d}, PageRank={node.pagerank:.3f})"
            )
            utility_sinks.append(node_id)

    return sorted(utility_sinks), sorted(topological_entrypoints)
