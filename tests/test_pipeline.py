"""End-to-end tests for Project Varuna: self-dogfooding (`varuna analyze .`) + Ruby & TypeScript AST extraction."""

from __future__ import annotations

from pathlib import Path

from varuna.cli import run_analysis
from varuna.emitters.likec4_emitter import generate_likec4_string
from varuna.emitters.report_emitter import generate_markdown_report_string
from varuna.extractors.ruby_extractor import RubyASTExtractor
from varuna.extractors.ts_extractor import TypeScriptASTExtractor
from varuna.models.graph_schema import EntrypointKind, Language

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_dogfood_self_analysis() -> None:
    """
    Dogfood test: Run Project Varuna on its own source code (`project-varuna`)
    without any OpenAPI specification.
    """
    result = run_analysis(repo_path=REPO_ROOT, include_tests=False)

    # 1. Verify Python language & all core modules are discovered
    assert Language.PYTHON in result.languages_detected
    assert "src.varuna.cli" in result.nodes
    assert "src.varuna.models.graph_schema" in result.nodes
    assert "src.varuna.graph.clustering" in result.nodes
    assert "src.varuna.emitters.likec4_emitter" in result.nodes

    # 2. Verify Centrality Sink Pruning caught `src.varuna.models.graph_schema`
    # because it is imported across extractors, graph, enrichment, emitters, and cli with out_degree == 0
    assert "src.varuna.models.graph_schema" in result.utility_sinks
    schema_node = result.nodes["src.varuna.models.graph_schema"]
    assert schema_node.is_utility_sink is True
    assert schema_node.in_degree >= 8
    assert schema_node.out_degree == 0

    # 3. Verify No-OpenAPI Tier 1 + Tier 2 Entrypoint Discovery caught `src.varuna.cli`
    assert "src.varuna.cli" in result.topological_entrypoints
    cli_node = result.nodes["src.varuna.cli"]
    assert cli_node.is_topological_entrypoint is True
    cli_ep_kinds = {ep.kind for ep in cli_node.entrypoints}
    assert EntrypointKind.CLI_COMMAND in cli_ep_kinds
    assert EntrypointKind.TOPOLOGICAL_SOURCE in cli_ep_kinds

    # 4. Verify Louvain Community Detection produced distinct C3 architectural components
    assert len(result.components) >= 4
    assert result.modularity_score > 0.25
    assert result.namespace_alignment_score >= 0.80

    # 5. Verify LikeC4 DSL & Markdown report generation
    likec4_dsl = generate_likec4_string(result)
    assert "specification {" in likec4_dsl
    assert "view c3_components of" in likec4_dsl
    assert "comp_extractors" in likec4_dsl
    assert "comp_graph" in likec4_dsl

    md_report = generate_markdown_report_string(result)
    assert "Newman-Girvan Modularity" in md_report
    assert "src.varuna.models.graph_schema" in md_report


def test_ruby_zeitwerk_and_routes_extractor(tmp_path: Path) -> None:
    """Verify Ruby AST extractor captures Zeitwerk constants, mixins, associations, and Rails routes."""
    app_dir = tmp_path / "app" / "services" / "billing"
    app_dir.mkdir(parents=True)
    rb_file = app_dir / "invoice_creator.rb"
    rb_file.write_text(
        """
module Billing
  class InvoiceCreator < ApplicationJob
    include Sidekiq::Worker
    belongs_to :account
    has_many :line_items

    def call(order_id)
      order = Orders::OrderRepository.find(order_id)
      Payments::StripeGateway.charge(order)
    end
  end
end
""",
        encoding="utf-8",
    )

    extractor = RubyASTExtractor()
    node = extractor.extract_file(tmp_path, rb_file)

    assert node.language == Language.RUBY
    assert "Orders::OrderRepository" in node.referenced_constants
    assert "Payments::StripeGateway" in node.referenced_constants
    assert "account" in node.associations
    assert "line_items" in node.associations
    assert any(ep.kind == EntrypointKind.BACKGROUND_WORKER for ep in node.entrypoints)


def test_typescript_express_and_nestjs_extractor(tmp_path: Path) -> None:
    """Verify TypeScript AST extractor captures imports, NestJS decorators, and Express routes."""
    src_dir = tmp_path / "src" / "orders"
    src_dir.mkdir(parents=True)
    ts_file = src_dir / "orders.controller.ts"
    ts_file.write_text(
        """
import { OrdersService } from './orders.service';
import { AuthGuard } from '@/auth/auth.guard';

@Controller('orders')
export class OrdersController {
  @Post('checkout')
  async checkout() {
    return OrdersService.process();
  }
}
""",
        encoding="utf-8",
    )

    extractor = TypeScriptASTExtractor()
    node = extractor.extract_file(tmp_path, ts_file)

    assert node.language == Language.TYPESCRIPT
    assert "./orders.service" in node.raw_imports
    assert "@/auth/auth.guard" in node.raw_imports
    assert len(node.routes) == 1
    assert node.routes[0].method == "POST"
    assert node.routes[0].path == "/orders/checkout"
