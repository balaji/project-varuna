"""Louvain community detection engine for discovering C3 architectural components."""

from __future__ import annotations

import re
from collections import Counter
from itertools import combinations

import networkx as nx
from networkx.algorithms.community import louvain_communities, modularity

from varuna.models.graph_schema import (
    C3Component,
    CodeNode,
    ExternalBoundary,
)

SIBLING_STRUCTURAL_PRIOR_WEIGHT: float = 1.25


def discover_c3_components(
    graph: nx.DiGraph,
    nodes: dict[str, CodeNode],
    external_boundaries: dict[str, ExternalBoundary],
    utility_sinks: list[str],
    resolution: float = 1.0,
    seed: int = 42,
) -> tuple[list[C3Component], float]:
    """
    Discover C3 components using Louvain community detection on the centrality-pruned graph.

    Steps:
    1. Exclude cross-cutting utility sinks (`is_utility_sink=True`) from clustering so they do not
       collapse independent business domains into a single cluster.
    2. Build an undirected weighted coupling graph combining strong AST call/import edges with a
       lightweight structural co-location prior for sibling modules in the same leaf package.
    3. Run seeded Louvain community detection (`louvain_communities`).
    4. Compute Newman-Girvan Modularity Q and assemble `C3Component` objects (plus a dedicated
       foundational component for cross-cutting schema/utility sinks).
    """
    sink_set: set[str] = set(utility_sinks)

    # Also identify top-level Composition Root Orchestrators (in_degree == 0 that fan out to >= 3 namespaces)
    # In an undirected projection, composition roots act like reverse utility sinks (star hubs) that bridge
    # otherwise independent subsystems. Isolating them preserves clean domain boundaries.
    composition_roots: list[str] = []
    for nid in graph.nodes:
        if nid in sink_set:
            continue
        node = nodes[nid]
        if node.in_degree == 0 and node.out_degree >= 3:
            out_namespaces = {
                nodes[dst].directory_namespace
                for _, dst in graph.out_edges(nid)
                if dst in nodes
                and dst not in sink_set
                and nodes[dst].directory_namespace != node.directory_namespace
            }
            if len(out_namespaces) >= 3:
                composition_roots.append(nid)

    isolated_star_nodes = sink_set | set(composition_roots)
    active_node_ids = [nid for nid in graph.nodes if nid not in isolated_star_nodes]

    if not active_node_ids:
        return [], 0.0

    # Build undirected weighted graph for Louvain
    undirected = nx.Graph()
    for nid in active_node_ids:
        undirected.add_node(nid)

    for u, v, data in graph.edges(data=True):
        if u in isolated_star_nodes or v in isolated_star_nodes:
            continue
        w = float(data.get("weight", 1.0))
        if undirected.has_edge(u, v):
            undirected[u][v]["weight"] += w
        else:
            undirected.add_edge(u, v, weight=w)

    # Add lightweight sibling structural prior within the same non-root directory namespace
    by_namespace: dict[str, list[str]] = {}
    for nid in active_node_ids:
        ns = nodes[nid].directory_namespace
        if ns and ns != "root":
            by_namespace.setdefault(ns, []).append(nid)

    for member_ids in by_namespace.values():
        if len(member_ids) <= 1:
            continue
        for u, v in combinations(member_ids, 2):
            if undirected.has_edge(u, v):
                undirected[u][v]["weight"] += SIBLING_STRUCTURAL_PRIOR_WEIGHT
            else:
                undirected.add_edge(u, v, weight=SIBLING_STRUCTURAL_PRIOR_WEIGHT)

    # Run Louvain community detection
    if undirected.number_of_edges() > 0:
        raw_communities = louvain_communities(
            undirected,
            weight="weight",
            resolution=resolution,
            seed=seed,
        )
        try:
            mod_score = float(modularity(undirected, raw_communities, weight="weight"))
        except Exception:
            mod_score = 0.0
    else:
        raw_communities = [{nid} for nid in active_node_ids]
        mod_score = 0.0

    # Sort communities deterministically by dominant namespace & size
    sorted_comms = sorted(
        [sorted(list(c)) for c in raw_communities if c],
        key=lambda c: (-len(c), c[0]),
    )

    components: list[C3Component] = []
    node_to_comp_id: dict[str, str] = {}
    used_comp_ids: set[str] = set()

    # Package Composition Root Orchestrators first (top of C3 hierarchy)
    if composition_roots:
        root_ns_counts = Counter(nodes[nid].directory_namespace for nid in composition_roots)
        dom_root_ns = (
            nodes[composition_roots[0]].name
            if len(composition_roots) == 1
            else root_ns_counts.most_common(1)[0][0]
        )
        root_comp_id = _make_comp_id(dom_root_ns or "cli", 0, used_comp_ids)
        used_comp_ids.add(root_comp_id)
        for nid in composition_roots:
            nodes[nid].community_id = root_comp_id
            node_to_comp_id[nid] = root_comp_id

        root_comp = _build_component_from_members(
            comp_id=root_comp_id,
            community_index=0,
            member_ids=sorted(composition_roots),
            dominant_ns=dom_root_ns,
            ns_counts=root_ns_counts,
            graph=graph,
            nodes=nodes,
            sink_set=sink_set,
        )
        components.append(root_comp)

    for idx, member_ids in enumerate(sorted_comms, start=len(components)):
        ns_counts = Counter(nodes[nid].directory_namespace for nid in member_ids)
        dominant_ns = ns_counts.most_common(1)[0][0] if ns_counts else f"cluster_{idx}"
        base_comp_id = _make_comp_id(dominant_ns, idx, used_comp_ids)
        used_comp_ids.add(base_comp_id)

        for nid in member_ids:
            nodes[nid].community_id = base_comp_id
            node_to_comp_id[nid] = base_comp_id

        comp = _build_component_from_members(
            comp_id=base_comp_id,
            community_index=idx,
            member_ids=member_ids,
            dominant_ns=dominant_ns,
            ns_counts=ns_counts,
            graph=graph,
            nodes=nodes,
            sink_set=sink_set,
        )
        components.append(comp)

    # Package cross-cutting utility/schema sinks into a foundational C3 component so they are visible
    if utility_sinks:
        sink_ns_counts = Counter(nodes[nid].directory_namespace for nid in utility_sinks)
        dom_sink_ns = sink_ns_counts.most_common(1)[0][0]
        sink_comp_id = _make_comp_id(dom_sink_ns or "shared_kernel", len(components), used_comp_ids)
        used_comp_ids.add(sink_comp_id)
        for nid in utility_sinks:
            nodes[nid].community_id = sink_comp_id
            node_to_comp_id[nid] = sink_comp_id

        sink_comp = _build_component_from_members(
            comp_id=sink_comp_id,
            community_index=len(components),
            member_ids=sorted(utility_sinks),
            dominant_ns=dom_sink_ns,
            ns_counts=sink_ns_counts,
            graph=graph,
            nodes=nodes,
            sink_set=sink_set,
        )
        components.append(sink_comp)

    # Populate inter-component dependencies and external boundary links
    for comp in components:
        outbound_weights: dict[str, float] = {}
        for src_id in comp.node_ids:
            for _, dst_id, edge_data in graph.out_edges(src_id, data=True):
                dst_comp_id = node_to_comp_id.get(dst_id)
                if dst_comp_id and dst_comp_id != comp.id:
                    w = float(edge_data.get("weight", 1.0))
                    outbound_weights[dst_comp_id] = round(
                        outbound_weights.get(dst_comp_id, 0.0) + w, 2
                    )
        comp.outbound_component_deps = outbound_weights

        # External boundary mapping
        for b_id, boundary in external_boundaries.items():
            if any(nid in boundary.referenced_by_modules for nid in comp.node_ids):
                if b_id not in comp.external_boundary_ids:
                    comp.external_boundary_ids.append(b_id)
                if comp.id not in boundary.referenced_by_components:
                    boundary.referenced_by_components.append(comp.id)

    return components, round(mod_score, 4)


def _build_component_from_members(
    comp_id: str,
    community_index: int,
    member_ids: list[str],
    dominant_ns: str,
    ns_counts: Counter,
    graph: nx.DiGraph,
    nodes: dict[str, CodeNode],
    sink_set: set[str],
) -> C3Component:
    languages = Counter(nodes[nid].language.value for nid in member_ids)
    top_lang = languages.most_common(1)[0][0].title() if languages else "Python"

    routes = []
    entrypoints = []
    used_sinks: set[str] = set()

    internal_edges = 0
    total_edges = 0
    member_set = set(member_ids)

    for nid in member_ids:
        n_obj = nodes[nid]
        routes.extend(n_obj.routes)
        entrypoints.extend(n_obj.entrypoints)
        for _, dst_id in graph.out_edges(nid):
            if dst_id in sink_set:
                used_sinks.add(dst_id)
            else:
                total_edges += 1
                if dst_id in member_set:
                    internal_edges += 1

    cohesion = round(internal_edges / float(total_edges), 3) if total_edges > 0 else 1.0
    is_entry = any(nodes[nid].is_topological_entrypoint or nodes[nid].routes for nid in member_ids)

    return C3Component(
        id=comp_id,
        name=dominant_ns.replace("_", " ").title(),
        technology=top_lang,
        community_index=community_index,
        node_ids=member_ids,
        directory_namespaces=list(ns_counts.keys()),
        dominant_namespace=dominant_ns,
        exposed_routes=routes,
        entrypoints=entrypoints,
        utility_sink_ids=sorted(used_sinks),
        internal_cohesion=cohesion,
        is_entrypoint_component=is_entry,
    )


def _make_comp_id(dominant_ns: str, idx: int, used: set[str]) -> str:
    clean = re.sub(r"[^a-z0-9_]+", "_", dominant_ns.lower()).strip("_") or f"group_{idx}"
    candidate = f"comp_{clean}"
    if candidate not in used:
        return candidate
    return f"comp_{clean}_{idx}"
