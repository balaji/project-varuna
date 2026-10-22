# Project Varuna

**Automated C4 Architecture & Modularity Discovery Engine** powered by **Tree-sitter ASTs** (Python, TypeScript/Node, Ruby/Rails), **Network Science** (Centrality utility-sink pruning & Louvain community detection), **3-Tier Entrypoint Discovery** (AST routes/CLIs/workers, topological source nodes, optional OpenAPI), and **LikeC4 DSL** generation.

## Quick Start

```bash
# Install with uv
uv sync --all-extras

# Dogfood: Run Varuna on its own source code (no OpenAPI spec needed)
uv run varuna analyze . --output ./docs/architecture

# Or inspect metrics in the terminal
uv run varuna inspect .

# Launch interactive LikeC4 viewer
npx likec4 start ./docs/architecture
```

## Architecture Pipeline

1. **Multi-Language AST Fact Extraction (`src/varuna/extractors`)**:
   - **Python**: Imports, classes, call sites, FastAPI/Flask/Django routes, Typer/Click CLI commands, Celery workers.
   - **TypeScript / Node**: ESM/CJS imports, `@/*` alias paths, Express/Fastify routes, NestJS `@Controller` decorators, BullMQ workers.
   - **Ruby / Rails**: Explicit `require` + implicit Zeitwerk constant references (`Billing::InvoiceService`), mixins (`include Sidekiq::Worker`), ActiveRecord associations (`belongs_to`, `has_many`), and `config/routes.rb`.
2. **3-Tier Boundary Discovery (Zero OpenAPI Required)**:
   - Tier 1: AST Route, CLI, and Worker decorators/DSL.
   - Tier 2: Graph-topological root entrypoint inference (`in_degree == 0` source nodes).
   - Tier 3: Optional OpenAPI/Swagger overlay + `docker-compose.yml` / manifest scanning.
3. **Network Science Engine (`src/varuna/graph`)**:
   - **Centrality Utility Sink Pruning**: Identifies cross-cutting schemas and utilities via In-Degree, Cross-Namespace Reach, PageRank, and HITS Authority scores before clustering so they don't collapse independent domains into a single cluster.
   - **Louvain Community Detection**: Discovers C3 architectural components and computes Newman-Girvan Modularity ($Q$).
   - **Architecture Drift Analyzer**: Compares physical directory namespaces against Louvain behavioral clusters.
4. **Semantic Enrichment & Emitters (`src/varuna/enrichment`, `src/varuna/emitters`)**:
   - Deterministic heuristic component naming + optional LLM Agent (`--llm` via Gemini/OpenAI/Anthropic).
   - Emits zoomable **LikeC4 (`architecture.c4`)** across C1–C4 levels and a **Markdown Drift Report (`varuna-report.md`)**.
