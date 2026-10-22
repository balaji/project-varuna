# Architecture & Modularity Report: `project-varuna`

Generated automatically by **Project Varuna** (AST + Centrality Pruning + Louvain Community Detection).

## 1. Executive Summary

| Metric | Value |
| :--- | :--- |
| **Languages Detected** | Python |
| **Source Modules Analyzed (C4)** | 17 (3,350 LOC) |
| **Internal Dependency Edges** | 106 |
| **Discovered C3 Components (Louvain)** | 6 |
| **Cross-Cutting Utility Sinks Pruned** | 1 |
| **Topological / AST Entrypoints** | 1 |
| **Newman-Girvan Modularity ($Q$)** | `0.2813` |
| **Folder-to-Cluster Alignment** | `100.0%` |

---

## 2. C3 Component Architecture (Mermaid Preview)

```mermaid
flowchart TD
    Actor["Developer / Consumer"]
    comp_cli["CLI Pipeline Orchestrator<br/>(Python · Typer / CLI · 1 modules)"]
    comp_extractors["AST and Spec Extractors<br/>(Python · 7 modules)"]
    comp_graph["Graph Analytics Engine<br/>(Python · 4 modules)"]
    comp_emitters["Architecture Report Emitter<br/>(Python · 2 modules)"]
    comp_enrichment["Semantic Component Enricher<br/>(Python · 2 modules)"]
    comp_models["Core Domain Models<br/>(Python · Shared Schema / Sink · 1 modules)"]
    ext_external_http_llm_apis[("External HTTP / LLM APIs<br/>(HTTPS / REST)")]
    ext_tree_sitter_ast_runtime[("Tree-sitter AST Runtime<br/>(Native C / Tree-sitter)")]
    ext_networkx_graph_runtime[("NetworkX Graph Runtime<br/>(NetworkX / SciPy)")]
    Actor --> comp_cli
    comp_cli -->|"7.2"| comp_emitters
    comp_cli -->|"3.6"| comp_enrichment
    comp_cli -->|"4.6"| comp_graph
    comp_cli -->|"3.6"| comp_models
    comp_extractors -->|"46.2"| comp_models
    comp_extractors -.-> ext_tree_sitter_ast_runtime
    comp_graph -->|"6.0"| comp_extractors
    comp_graph -->|"24.4"| comp_models
    comp_graph -.-> ext_networkx_graph_runtime
    comp_emitters -->|"6.2"| comp_models
    comp_enrichment -->|"6.2"| comp_models
    comp_enrichment -.-> ext_external_http_llm_apis
```

---

## 3. Discovered C3 Components (Louvain Communities)

| Component ID | Name | Technology | Modules | Cohesion | Dominant Folder | Description |
| :--- | :--- | :--- | :---: | :---: | :--- | :--- |
| `comp_cli` | **CLI Pipeline Orchestrator** | Python · Typer / CLI | 1 (`cli`) | `0%` | `cli` | Orchestrates static analysis and inspection workflows via a command-line interface. |
| `comp_extractors` | **AST and Spec Extractors** | Python | 7 (`base`, `infra_scanner`, `openapi_parser`, `python_extractor`, `registry`, `ruby_extractor`, `ts_extractor`) | `100%` | `extractors` | Parses source code ASTs, OpenAPI specifications, and infrastructure manifests to extract architectural symbols and dependencies. |
| `comp_graph` | **Graph Analytics Engine** | Python | 4 (`builder`, `centrality`, `clustering`, `drift`) | `60%` | `graph` | Constructs dependency graphs and executes network science algorithms to detect components, centrality, and drift. |
| `comp_emitters` | **Architecture Report Emitter** | Python | 2 (`likec4_emitter`, `report_emitter`) | `100%` | `emitters` | Generates LikeC4 architecture definitions and comprehensive Markdown drift reports from graph analysis results. |
| `comp_enrichment` | **Semantic Component Enricher** | Python | 2 (`heuristic_namer`, `llm_agent`) | `100%` | `enrichment` | Infers human-readable names and descriptions for detected components using heuristic analysis and language models. |
| `comp_models` | **Core Domain Models** | Python · Shared Schema / Sink | 1 (`graph_schema`) | `100%` | `models` | Defines Pydantic schemas and entity types representing architectural nodes, edges, and graph metadata. |

---

## 4. Centrality & Cross-Cutting Utility Sinks

Modules with high cross-namespace fan-in and zero/minimal internal outbound coupling are isolated before Louvain clustering so they don't collapse independent domains into a single hairball:

| Module | Path | In-Degree | Out-Degree | PageRank | Authority | Classification Reason |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| `src.varuna.models.graph_schema` | `src/varuna/models/graph_schema.py` | 16 | 0 | `0.0588` | `0.0000` | Cross-cutting sink imported by 16 modules across 6 namespaces (out_degree=0, PageRank=0.059) |

---

## 5. Entrypoint Discovery (3-Tier Boundary Detection)

| Module | Entrypoint Kind | Name | Detail |
| :--- | :--- | :--- | :--- |
| `src.varuna.cli` | `cli_command` | `cli:analyze` | CLI command handler `analyze_command` |
| `src.varuna.cli` | `cli_command` | `cli:inspect` | CLI command handler `inspect_command` |
| `src.varuna.cli` | `cli_command` | `python -m src.varuna.cli` | Main module execution block |
| `src.varuna.cli` | `topological_source` | `root:cli` | Topological source node (in_degree=0, out_degree=5) |

---

## 6. Architecture Drift & Modularity Diagnostics

✅ **Zero Architecture Drift Detected** — Physical folder namespaces align cleanly with Louvain behavioral communities.
