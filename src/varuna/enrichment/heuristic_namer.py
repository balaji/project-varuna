"""Deterministic TF-IDF, docstring, and symbol-based C3 component namer (zero-API-key default)."""

from __future__ import annotations

import re
from collections import Counter

from varuna.models.graph_schema import C3Component, CodeNode

STOP_TOKENS = {
    "src",
    "lib",
    "app",
    "base",
    "init",
    "index",
    "main",
    "utils",
    "helper",
    "helpers",
    "common",
    "get",
    "set",
    "run",
    "build",
    "create",
    "make",
    "parse",
    "test",
}

KNOWN_DOMAIN_LABELS: dict[str, str] = {
    "extractors": "AST & Spec Extractors",
    "graph": "Network Science & Graph Engine",
    "enrichment": "Semantic Enrichment & Naming",
    "emitters": "LikeC4 & Report Emitters",
    "models": "Core Graph Schema & Models",
    "cli": "CLI & Orchestration Entrypoint",
    "controllers": "HTTP API Controllers",
    "services": "Domain Business Services",
    "workers": "Async Background Workers",
    "jobs": "Scheduled & Async Jobs",
    "repositories": "Data Access & Repositories",
    "auth": "Authentication & Identity",
    "billing": "Billing & Invoicing Domain",
}


def enrich_components_heuristically(
    components: list[C3Component],
    nodes: dict[str, CodeNode],
) -> None:
    """
    Populate human-friendly architectural titles, technologies, and descriptions for each
    Louvain-discovered C3 component using symbol frequencies, docstrings, and entrypoints.
    """
    for comp in components:
        member_nodes = [nodes[nid] for nid in comp.node_ids if nid in nodes]
        if not member_nodes:
            continue

        dom_ns = comp.dominant_namespace.lower()

        # 1. Determine clean architectural title
        if dom_ns in KNOWN_DOMAIN_LABELS and len(comp.directory_namespaces) == 1:
            comp.name = KNOWN_DOMAIN_LABELS[dom_ns]
        elif len(member_nodes) == 1:
            single = member_nodes[0]
            if single.is_topological_entrypoint or single.entrypoints:
                comp.name = f"{single.name.upper() if len(single.name) <= 3 else single.name.title()} Orchestrator"
            else:
                comp.name = f"{single.name.replace('_', ' ').title()} Module"
        else:
            top_tokens = _extract_top_domain_tokens(member_nodes, dom_ns)
            ns_title = dom_ns.replace("_", " ").title() if dom_ns != "root" else "Core"
            if top_tokens:
                comp.name = f"{ns_title} ({', '.join(top_tokens[:2])})"
            else:
                comp.name = f"{ns_title} Subsystem"

        # 2. Refine technology tag
        langs = sorted({n.language.value.title() for n in member_nodes})
        tech_parts = list(langs)
        if any(n.is_utility_sink for n in member_nodes):
            tech_parts.append("Shared Schema / Sink")
        elif comp.exposed_routes:
            tech_parts.append("HTTP API")
        elif any(ep.kind.value == "cli_command" for ep in comp.entrypoints):
            tech_parts.append("Typer / CLI")
        elif any(ep.kind.value == "background_worker" for ep in comp.entrypoints):
            tech_parts.append("Async Worker")
        comp.technology = " · ".join(tech_parts)

        # 3. Synthesize concise architectural description
        docstrings = [n.docstring for n in member_nodes if n.docstring]
        exported_classes = [cls for n in member_nodes for cls in n.classes][:4]
        route_Paths = [f"{r.method} {r.path}" for r in comp.exposed_routes][:3]

        if len(member_nodes) == 1 and docstrings:
            comp.description = docstrings[0]
        elif any(n.is_utility_sink for n in member_nodes):
            comp.description = (
                f"Cross-cutting foundational models/utilities ({', '.join(exported_classes) or comp.dominant_namespace}) "
                f"referenced across {len(member_nodes)} module(s)."
            )
        elif route_Paths:
            comp.description = (
                f"Exposes {len(comp.exposed_routes)} endpoint(s) ({', '.join(route_Paths)}) "
                f"backed by {', '.join(n.name for n in member_nodes[:3])}."
            )
        elif docstrings:
            # Combine top module docstring with module count
            primary_doc = docstrings[0].rstrip(".")
            comp.description = f"{primary_doc} ({len(member_nodes)} modules)."
        else:
            mod_names = ", ".join(n.name for n in member_nodes[:4])
            comp.description = f"Louvain component grouping {len(member_nodes)} tightly coupled modules ({mod_names})."


def _extract_top_domain_tokens(member_nodes: list[CodeNode], dom_ns: str) -> list[str]:
    counts: Counter = Counter()
    for node in member_nodes:
        for sym in node.classes + node.functions + [node.name]:
            # Split CamelCase and snake_case
            words = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", sym).lower().split("_")
            for w in words:
                if len(w) >= 3 and w not in STOP_TOKENS and w != dom_ns:
                    counts[w.title()] += 1
    return [tok for tok, _ in counts.most_common(3)]
