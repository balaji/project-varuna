"""Architecture Drift Analyzer: compares folder boundaries against Louvain communities."""

from __future__ import annotations

from collections import Counter

import networkx as nx

from varuna.models.graph_schema import C3Component, CodeNode, DriftIssue


def analyze_architecture_drift(
    graph: nx.DiGraph,
    nodes: dict[str, CodeNode],
    components: list[C3Component],
) -> tuple[float, list[DriftIssue]]:
    """
    Evaluate how closely the codebase's physical directory structure aligns with the
    behavioral Louvain communities discovered from actual call/import coupling.

    Returns (namespace_alignment_score in [0.0, 1.0], list of DriftIssue).
    """
    if not nodes or not components:
        return 1.0, []

    issues: list[DriftIssue] = []

    # 1. Compute cluster homogeneity (what fraction of nodes in each Louvain component belong to its dominant folder)
    aligned_nodes = 0
    total_active_nodes = 0
    for comp in components:
        if not comp.node_ids:
            continue
        ns_counts = Counter(nodes[nid].directory_namespace for nid in comp.node_ids)
        dom_ns, dom_count = ns_counts.most_common(1)[0]
        aligned_nodes += dom_count
        total_active_nodes += len(comp.node_ids)

        # Flag mixed-namespace clusters where Louvain pulled a module from another folder due to heavy coupling
        if len(ns_counts) > 1:
            minority_nodes = [
                nid for nid in comp.node_ids if nodes[nid].directory_namespace != dom_ns
            ]
            # Only flag if not a single-file root entrypoint orchestrator
            non_root_minorities = [
                nid for nid in minority_nodes if nodes[nid].directory_namespace != "root"
            ]
            if non_root_minorities:
                issues.append(
                    DriftIssue(
                        kind="CROSS_BOUNDARY_CLUSTER",
                        severity="MEDIUM",
                        description=(
                            f"Component `{comp.id}` merges modules from `{dom_ns}` with "
                            f"{', '.join(f'`{m}`' for m in non_root_minorities)} due to tight coupling."
                        ),
                        source_module=non_root_minorities[0],
                        target_module=comp.id,
                    )
                )

    # 2. Check if a single directory namespace got splintered across multiple Louvain communities
    ns_to_comps: dict[str, list[str]] = {}
    for comp in components:
        for nid in comp.node_ids:
            ns = nodes[nid].directory_namespace
            if ns != "root":
                ns_to_comps.setdefault(ns, [])
                if comp.id not in ns_to_comps[ns]:
                    ns_to_comps[ns].append(comp.id)

    for ns, comp_ids in ns_to_comps.items():
        if len(comp_ids) > 1:
            issues.append(
                DriftIssue(
                    kind="SPLIT_NAMESPACE",
                    severity="MEDIUM",
                    description=(
                        f"Directory namespace `{ns}` is splintered across {len(comp_ids)} "
                        f"Louvain communities ({', '.join(comp_ids)}), indicating low internal cohesion."
                    ),
                    source_module=ns,
                )
            )

    # 3. Check for God Modules (excessive outbound fan-out across many distinct namespaces)
    for nid, node in nodes.items():
        if node.is_utility_sink or node.is_topological_entrypoint:
            continue
        target_namespaces = {
            nodes[dst].directory_namespace
            for _, dst in graph.out_edges(nid)
            if dst in nodes
            and not nodes[dst].is_utility_sink
            and nodes[dst].directory_namespace != node.directory_namespace
        }
        if len(target_namespaces) >= 3 and node.out_degree >= 6:
            issues.append(
                DriftIssue(
                    kind="HIGH_FAN_OUT_HUB",
                    severity="LOW",
                    description=(
                        f"Module `{nid}` depends on {node.out_degree} internal modules across "
                        f"{len(target_namespaces)} external namespaces ({', '.join(sorted(target_namespaces))})."
                    ),
                    source_module=nid,
                    weight=node.weighted_out_degree,
                )
            )

    alignment = round(aligned_nodes / float(total_active_nodes), 4) if total_active_nodes else 1.0
    return alignment, issues
