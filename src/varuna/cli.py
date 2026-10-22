"""Typer + Rich CLI and pipeline orchestrator for Project Varuna."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from varuna.emitters.likec4_emitter import emit_likec4_dsl
from varuna.emitters.report_emitter import emit_markdown_report
from varuna.enrichment.llm_agent import enrich_components_with_llm
from varuna.graph.builder import GraphBuilder
from varuna.models.graph_schema import AnalysisResult

app = typer.Typer(
    name="varuna",
    help="Project Varuna: Automated C4 Architecture & Modularity Discovery Engine (AST + Louvain + LikeC4).",
    add_completion=False,
)
console = Console()


def run_analysis(
    repo_path: Path,
    openapi_path: Path | None = None,
    resolution: float = 1.0,
    use_llm: bool = False,
    include_tests: bool = False,
) -> AnalysisResult:
    """Execute the full Project Varuna 5-stage analysis pipeline on a repository."""
    repo_root = repo_path.resolve()
    project_name = repo_root.name

    # Stage 1-3: AST + Optional OpenAPI + Infra scan -> NetworkX DiGraph -> Centrality -> Louvain -> Drift
    builder = GraphBuilder(
        repo_root=repo_root,
        openapi_path=openapi_path,
        include_tests=include_tests,
    )
    (
        _graph,
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
    ) = builder.analyze_graph(resolution=resolution)

    # Stage 4: Semantic Component Naming (Heuristic + Optional LLM Agent)
    enrich_components_with_llm(components, nodes, use_llm=use_llm)

    languages_detected = sorted(
        {n.language for n in nodes.values()},
        key=lambda lang: lang.value,
    )

    return AnalysisResult(
        project_name=project_name,
        root_path=str(repo_root),
        languages_detected=languages_detected,
        nodes=nodes,
        edges=edges,
        external_boundaries=external_boundaries,
        utility_sinks=utility_sinks,
        topological_entrypoints=topological_entrypoints,
        components=components,
        unmatched_openapi_routes=unmatched_routes,
        modularity_score=modularity_score,
        namespace_alignment_score=alignment_score,
        drift_issues=drift_issues,
    )


@app.command("analyze")
def analyze_command(
    repo_path: Path = typer.Argument(
        Path("."),
        exists=True,
        file_okay=False,
        dir_okay=True,
        help="Path to the target repository root to analyze.",
    ),
    output_dir: Path = typer.Option(
        Path("./docs/architecture"),
        "--output",
        "-o",
        help="Directory where LikeC4 (.c4) and Markdown modularity reports will be written.",
    ),
    openapi_path: Path | None = typer.Option(
        None,
        "--openapi",
        help="Optional path to an OpenAPI/Swagger (.yaml/.json) specification.",
    ),
    resolution: float = typer.Option(
        1.0,
        "--resolution",
        "-r",
        help="Louvain community resolution parameter (higher = more granular C3 components).",
    ),
    llm: bool = typer.Option(
        False,
        "--llm/--no-llm",
        help="Enable LLM agent synthesis (uses GEMINI_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY).",
    ),
    include_tests: bool = typer.Option(
        False,
        "--include-tests",
        help="Include test/spec files in the architecture graph.",
    ),
    emit_json: bool = typer.Option(
        True,
        "--json/--no-json",
        help="Also emit the raw graph & metrics as `varuna-graph.json`.",
    ),
) -> None:
    """Analyze a repository and generate LikeC4 (.c4) diagrams and an Architecture Drift report."""
    result = run_analysis(
        repo_path=repo_path,
        openapi_path=openapi_path,
        resolution=resolution,
        use_llm=llm,
        include_tests=include_tests,
    )

    c4_file = emit_likec4_dsl(result, output_dir / "architecture.c4")
    report_file = emit_markdown_report(result, output_dir / "varuna-report.md")

    if emit_json:
        json_file = output_dir / "varuna-graph.json"
        json_file.write_text(result.model_dump_json(indent=2), encoding="utf-8")

    _print_console_summary(result, c4_file, report_file)


@app.command("inspect")
def inspect_command(
    repo_path: Path = typer.Argument(
        Path("."),
        exists=True,
        file_okay=False,
        dir_okay=True,
        help="Path to the repository root to inspect in the terminal.",
    ),
    resolution: float = typer.Option(
        1.0,
        "--resolution",
        "-r",
        help="Louvain community resolution parameter.",
    ),
) -> None:
    """Inspect a repository's centrality sinks, entrypoints, and Louvain C3 clusters in the terminal."""
    result = run_analysis(repo_path=repo_path, resolution=resolution)
    _print_console_summary(result, None, None)


def _print_console_summary(
    result: AnalysisResult,
    c4_file: Path | None,
    report_file: Path | None,
) -> None:
    langs = ", ".join(lang.value.title() for lang in result.languages_detected) or "None"
    console.print(
        Panel.fit(
            f"[bold cyan]Project Varuna[/bold cyan] — C4 Architecture & Network Science Summary for [bold]{result.project_name}[/bold]\n"
            f"Languages: [green]{langs}[/green] | Modules: [bold]{len(result.nodes)}[/bold] | "
            f"Edges: [bold]{len(result.edges)}[/bold] | "
            f"Modularity Q: [bold yellow]{result.modularity_score:.4f}[/bold yellow] | "
            f"Folder Alignment: [bold magenta]{result.namespace_alignment_score:.1%}[/bold magenta]",
            border_style="cyan",
        )
    )

    # Table 1: Utility Sinks Pruned
    if result.utility_sinks:
        sink_table = Table(title="Centrality-Pruned Cross-Cutting Utility Sinks", show_lines=False)
        sink_table.add_column("Module ID", style="cyan")
        sink_table.add_column("In-Deg", justify="right")
        sink_table.add_column("Out-Deg", justify="right")
        sink_table.add_column("PageRank", justify="right", style="yellow")
        sink_table.add_column("Reason", style="dim")
        for sid in result.utility_sinks:
            n = result.nodes[sid]
            sink_table.add_row(
                sid,
                str(n.in_degree),
                str(n.out_degree),
                f"{n.pagerank:.4f}",
                n.utility_reason,
            )
        console.print(sink_table)

    # Table 2: Discovered C3 Components
    comp_table = Table(title="Louvain-Discovered C3 Architectural Components", show_lines=True)
    comp_table.add_column("Component ID", style="bold green")
    comp_table.add_column("Name", style="bold")
    comp_table.add_column("Technology", style="cyan")
    comp_table.add_column("Modules", justify="right")
    comp_table.add_column("Cohesion", justify="right", style="magenta")
    comp_table.add_column("Depends On (C3)", style="yellow")

    for comp in result.components:
        deps_str = (
            ", ".join(
                f"{dst} (w={w:.1f})" for dst, w in sorted(comp.outbound_component_deps.items())
            )
            or "—"
        )
        comp_table.add_row(
            comp.id,
            comp.name,
            comp.technology,
            str(len(comp.node_ids)),
            f"{comp.internal_cohesion:.0%}",
            deps_str,
        )
    console.print(comp_table)

    if c4_file and report_file:
        console.print(
            f"\n[bold green]Generated Artifacts:[/bold green]\n"
            f"  • LikeC4 DSL:      [underline]{c4_file}[/underline]\n"
            f"  • Drift Report:    [underline]{report_file}[/underline]\n"
            f"  • Interactive UI:  [dim]npx likec4 start {c4_file.parent.as_posix()}[/dim]"
        )


if __name__ == "__main__":
    app()
