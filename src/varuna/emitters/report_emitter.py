"""Generates a Markdown Architecture & Modularity Drift Report with embedded Mermaid C3 diagrams."""

from __future__ import annotations

from pathlib import Path

from varuna.models.graph_schema import AnalysisResult


def emit_markdown_report(result: AnalysisResult, output_path: Path) -> Path:
    """Write a comprehensive Markdown architecture & modularity report."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = generate_markdown_report_string(result)
    output_path.write_text(content, encoding="utf-8")
    return output_path


def generate_markdown_report_string(result: AnalysisResult) -> str:
    langs = ", ".join(lang.value.title() for lang in result.languages_detected) or "None"
    total_loc = sum(n.lines_of_code for n in result.nodes.values())

    lines: list[str] = [
        f"# Architecture & Modularity Report: `{result.project_name}`",
        "",
        "Generated automatically by **Project Varuna** (AST + Centrality Pruning + Louvain Community Detection).",
        "",
        "## 1. Executive Summary",
        "",
        "| Metric | Value |",
        "| :--- | :--- |",
        f"| **Languages Detected** | {langs} |",
        f"| **Source Modules Analyzed (C4)** | {len(result.nodes)} ({total_loc:,} LOC) |",
        f"| **Internal Dependency Edges** | {len(result.edges)} |",
        f"| **Discovered C3 Components (Louvain)** | {len(result.components)} |",
        f"| **Cross-Cutting Utility Sinks Pruned** | {len(result.utility_sinks)} |",
        f"| **Topological / AST Entrypoints** | {len(result.topological_entrypoints)} |",
        f"| **Newman-Girvan Modularity ($Q$)** | `{result.modularity_score:.4f}` |",
        f"| **Folder-to-Cluster Alignment** | `{result.namespace_alignment_score:.1%}` |",
        "",
        "---",
        "",
        "## 2. C3 Component Architecture (Mermaid Preview)",
        "",
        "```mermaid",
        "flowchart TD",
        '    Actor["Developer / Consumer"]',
    ]

    for comp in result.components:
        mod_count = len(comp.node_ids)
        lines.append(f'    {comp.id}["{comp.name}<br/>({comp.technology} · {mod_count} modules)"]')

    for b_id, boundary in result.external_boundaries.items():
        lines.append(f'    ext_{b_id}[("{boundary.name}<br/>({boundary.technology})")]')

    for comp in result.components:
        if comp.is_entrypoint_component:
            lines.append(f"    Actor --> {comp.id}")
        for dst_id, w in sorted(comp.outbound_component_deps.items()):
            lines.append(f'    {comp.id} -->|"{w:.1f}"| {dst_id}')
        for b_id in comp.external_boundary_ids:
            lines.append(f"    {comp.id} -.-> ext_{b_id}")

    lines.extend(
        [
            "```",
            "",
            "---",
            "",
            "## 3. Discovered C3 Components (Louvain Communities)",
            "",
            "| Component ID | Name | Technology | Modules | Cohesion | Dominant Folder | Description |",
            "| :--- | :--- | :--- | :---: | :---: | :--- | :--- |",
        ]
    )

    for comp in result.components:
        mod_list = ", ".join(
            f"`{result.nodes[nid].name}`" for nid in comp.node_ids if nid in result.nodes
        )
        lines.append(
            f"| `{comp.id}` | **{comp.name}** | {comp.technology} | {len(comp.node_ids)} ({mod_list}) | "
            f"`{comp.internal_cohesion:.0%}` | `{comp.dominant_namespace}` | {comp.description} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 4. Centrality & Cross-Cutting Utility Sinks",
            "",
            "Modules with high cross-namespace fan-in and zero/minimal internal outbound coupling are isolated before Louvain clustering so they don't collapse independent domains into a single hairball:",
            "",
        ]
    )

    if result.utility_sinks:
        lines.extend(
            [
                "| Module | Path | In-Degree | Out-Degree | PageRank | Authority | Classification Reason |",
                "| :--- | :--- | :---: | :---: | :---: | :---: | :--- |",
            ]
        )
        for sink_id in result.utility_sinks:
            s_node = result.nodes[sink_id]
            lines.append(
                f"| `{s_node.id}` | `{s_node.relative_path}` | {s_node.in_degree} | {s_node.out_degree} | "
                f"`{s_node.pagerank:.4f}` | `{s_node.authority_score:.4f}` | {s_node.utility_reason} |"
            )
    else:
        lines.append("_No cross-cutting utility sinks detected._")

    lines.extend(
        [
            "",
            "---",
            "",
            "## 5. Entrypoint Discovery (3-Tier Boundary Detection)",
            "",
            "| Module | Entrypoint Kind | Name | Detail |",
            "| :--- | :--- | :--- | :--- |",
        ]
    )

    for ep_mod_id in result.topological_entrypoints:
        node = result.nodes[ep_mod_id]
        for ep in node.entrypoints:
            lines.append(f"| `{node.id}` | `{ep.kind.value}` | `{ep.name}` | {ep.detail} |")

    lines.extend(
        [
            "",
            "---",
            "",
            "## 6. Architecture Drift & Modularity Diagnostics",
            "",
        ]
    )

    if result.drift_issues:
        lines.extend(
            [
                "| Severity | Issue Kind | Description |",
                "| :---: | :--- | :--- |",
            ]
        )
        for issue in result.drift_issues:
            lines.append(f"| **{issue.severity}** | `{issue.kind}` | {issue.description} |")
    else:
        lines.append(
            "✅ **Zero Architecture Drift Detected** — Physical folder namespaces align cleanly with Louvain behavioral communities."
        )

    lines.append("")
    return "\n".join(lines)
