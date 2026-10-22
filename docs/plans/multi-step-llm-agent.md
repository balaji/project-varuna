# Plan: Scoped Multi-Step LLM Agents in Project Varuna

## Context & Design Principle

In Project Varuna's 5-stage pipeline, deterministic Tree-sitter AST extraction (`ExtractorRegistry`), NetworkX centrality pruning (`analyze_centrality_and_topology`), and Louvain modularity maximization (`discover_c3_components`) provide fast, reproducible, and mathematically grounded architectural discovery.

Replacing Stages 1–3 with a full-codebase multi-step LLM agent would duplicate static analysis at orders-of-magnitude higher cost/latency and conflict with drift detection (since LLMs naturally group modules by intended directory names rather than actual coupling density).

Instead, a multi-step LLM agent should take **the pre-computed NetworkX dependency graph as its starting state** and operate strictly on **structural gaps** and **actionable drift remediation** where static AST analysis stops.

---

## Pipeline Integration Overview

```text
[Stage 1: Tree-sitter AST + OpenAPI + Infra Scan]
                       │
                       ▼
[Stage 2: Static Edge Resolution (_resolve_edges)]
                       │
                       ├──► (Optional) Agent 1: Unresolved Edge Hunter (Pre-Louvain)
                       │    Resolves isolated nodes, unmatched OpenAPI routes,
                       │    DI containers, event buses, and dynamic dispatch.
                       ▼
[Stage 3: Centrality Pruning + Louvain C3 Clustering + Drift Detection]
                       │
                       ▼
[Stage 4: Semantic Component Naming (Heuristic + 1-Shot Digest)]
                       │
                       ├──► (Optional) Agent 2: Drift Refactoring Architect (Post-Louvain)
                       │    Inspects offending modules for CROSS_BOUNDARY_CLUSTER,
                       │    SPLIT_NAMESPACE, and HIGH_FAN_OUT_HUB issues to propose
                       │    concrete refactoring steps.
                       ▼
[Stage 5: LikeC4 DSL + Markdown Report Emitters]
```

---

## Agent 1: Unresolved Edge Hunter (Pre-Louvain)

### Problem Statement
Static AST extraction (`_resolve_edges` in `src/varuna/graph/builder.py`) captures explicit imports, symbol calls, class inheritance, and ORM associations, but cannot resolve:
- Unmatched OpenAPI operations (`unmatched_openapi_routes`) wired via dynamic router registries or string-based controller dispatch.
- Isolated or zero-degree modules (`in_degree == 0 and out_degree == 0`) invoked via Dependency Injection (DI) containers, reflection (`getattr` / `importlib`), or framework conventions.
- Implicit pub/sub, Celery/Sidekiq job queues, or event-bus producers and consumers linked by topic strings rather than direct module imports.

### Scoped Input State
Instead of scanning the entire repository, the agent receives only:
1. `unmatched_openapi_routes: list[RouteEndpoint]`
2. Isolated or suspiciously low-degree `CodeNode` entries (`in_degree == 0` and not marked as an entrypoint).
3. A compact symbol & file index (`dict[str, str]` mapping module IDs to relative paths and exported symbols).

### Agent Tools (Read-Only, Bounded)
- `grep_codebase(pattern: str, path_prefix: str | None)` — Search for string literals, route paths, event names, or DI bindings.
- `read_module_snippet(module_id: str, start_line: int, end_line: int)` — Inspect specific sections of candidate files.
- `emit_dependency_edge(source: str, target: str, kind: EdgeKind, symbol: str, rationale: str)` — Register a verified implicit edge before Louvain community detection runs.

### Guardrails & Determinism
- **Strict verification:** Both `source` and `target` must exist in `nodes: dict[str, CodeNode]`.
- **Turn budget:** Capped at `MAX_TURNS = 8` (or bounded by the number of unmatched routes / isolated nodes) to keep token usage predictable.
- **Provenance tagging:** Edges discovered by the agent are tagged (e.g., `EdgeKind.ROUTES_TO` or `symbols=["llm_inferred:<symbol>"]`) so deterministic vs. inferred edges remain auditable in `varuna-graph.json`.

---

## Agent 2: Drift Refactoring Architect (Post-Louvain)

### Problem Statement
`analyze_architecture_drift` (`src/varuna/graph/drift.py`) mathematically identifies three classes of architectural drift (`DriftIssue`):
1. `CROSS_BOUNDARY_CLUSTER` — Modules from one directory namespace pulled into another folder's Louvain community due to heavy cross-folder coupling.
2. `SPLIT_NAMESPACE` — A single directory namespace splintered across multiple Louvain communities due to low internal cohesion.
3. `HIGH_FAN_OUT_HUB` — Non-sink modules with excessive outbound dependencies across $\ge 3$ external namespaces (`out_degree >= 6`).

Currently, `emit_markdown_report` (`src/varuna/emitters/report_emitter.py`) reports *what* drifted, but cannot tell the engineer *how* to decouple the specific symbols causing the drift.

### Scoped Input State
For each `DriftIssue` in `result.drift_issues`:
1. The `DriftIssue` metadata (`kind`, `severity`, `source_module`, `target_module`, `weight`).
2. The specific `DependencyEdge` objects and `symbols` connecting the drifting module(s) to the foreign Louvain cluster.

### Agent Tools (Read-Only, Bounded)
- `inspect_coupled_symbols(source_module: str, target_module: str)` — Retrieve the exact lines where `source_module` imports, calls, or inherits from `target_module`.
- `read_module_snippet(module_id: str, start_line: int, end_line: int)` — Read the surrounding class/function definitions.

### Structured Output
Populate a new optional field on `DriftIssue`:
```python
class DriftIssue(BaseModel):
    kind: str = Field(description="CROSS_BOUNDARY_CLUSTER | SPLIT_NAMESPACE | HIGH_FAN_OUT_HUB")
    severity: str = Field(default="MEDIUM", description="LOW | MEDIUM | HIGH")
    description: str
    source_module: str = ""
    target_module: str = ""
    weight: float = 0.0
    remediation_plan: str = Field(
        default="",
        description="Concrete refactoring recommendation (e.g. interface extraction, module relocation, event decoupling)",
    )
```

### Example Remediation Output in `varuna-report.md`
- **Relocation vs. Decoupling Decision:** Determines whether a `CROSS_BOUNDARY_CLUSTER` module genuinely belongs in the target folder (`git mv`) or whether an interface/DTO should be extracted to break the coupling.
- **Cycle / Hub Breaking:** Identifies the exact 2–3 functions or classes inside a `HIGH_FAN_OUT_HUB` that should be inverted via dependency injection or split into a dedicated orchestrator.

---

## CLI & Cost Controls

To preserve Project Varuna's zero-API-key default and sub-second local execution:
- `--no-llm` (default): 100% deterministic heuristics and AST/Louvain pipeline.
- `--llm`: Single-shot semantic component naming digest (`enrich_components_with_llm`, `<250 tokens` per C3 component).
- `--llm-agent` (proposed): Enables the bounded multi-step tool loop for **Agent 1 (Unresolved Edge Hunter)** and **Agent 2 (Drift Refactoring Architect)** with configurable `--max-agent-turns` (default: `10`).
