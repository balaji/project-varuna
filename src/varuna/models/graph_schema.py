"""Core Pydantic schema definitions for C1-C4 architectural graphs, metrics, and drift analysis."""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional, Set
from pydantic import BaseModel, Field


class Language(str, Enum):
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    RUBY = "ruby"
    UNKNOWN = "unknown"


class NodeKind(str, Enum):
    MODULE = "module"
    CONTAINER = "container"
    EXTERNAL_SYSTEM = "external_system"
    DATABASE = "database"
    QUEUE = "queue"
    ACTOR = "actor"


class EntrypointKind(str, Enum):
    HTTP_ROUTE = "http_route"
    CLI_COMMAND = "cli_command"
    BACKGROUND_WORKER = "background_worker"
    TOPOLOGICAL_SOURCE = "topological_source"


class EdgeKind(str, Enum):
    IMPORTS = "imports"
    CALLS = "calls"
    REFERENCES_CONSTANT = "references_constant"
    INHERITS_OR_MIXIN = "inherits_or_mixin"
    ASSOCIATION = "association"
    ROUTES_TO = "routes_to"
    USES_EXTERNAL = "uses_external"
    USES_DATASTORE = "uses_datastore"


EDGE_BASE_WEIGHTS: Dict[EdgeKind, float] = {
    EdgeKind.IMPORTS: 1.0,
    EdgeKind.REFERENCES_CONSTANT: 2.0,
    EdgeKind.ASSOCIATION: 2.5,
    EdgeKind.CALLS: 3.0,
    EdgeKind.INHERITS_OR_MIXIN: 3.5,
    EdgeKind.ROUTES_TO: 3.0,
    EdgeKind.USES_EXTERNAL: 1.5,
    EdgeKind.USES_DATASTORE: 2.0,
}


class RouteEndpoint(BaseModel):
    method: str = Field(description="HTTP method (GET, POST, etc.) or CLI/RPC verb")
    path: str = Field(description="Route URI path or command name")
    handler_symbol: str = Field(default="", description="Controller/function handling the route")
    source_file: Optional[str] = Field(default=None, description="Relative path to handler file")
    summary: str = Field(default="", description="Description from docstring or OpenAPI spec")
    tags: List[str] = Field(default_factory=list, description="Semantic grouping tags")
    from_openapi: bool = Field(default=False, description="Whether enriched from an OpenAPI spec")


class EntrypointInfo(BaseModel):
    kind: EntrypointKind
    name: str
    detail: str = ""


class CodeNode(BaseModel):
    id: str = Field(description="Canonical normalized module ID (e.g., src.varuna.cli)")
    name: str = Field(description="Short module or file name")
    file_path: str = Field(description="Absolute path on disk")
    relative_path: str = Field(description="POSIX relative path from repo root")
    language: Language = Language.UNKNOWN
    directory_namespace: str = Field(
        default="root",
        description="Logical top-level domain/folder namespace for drift comparison",
    )
    classes: List[str] = Field(default_factory=list)
    functions: List[str] = Field(default_factory=list)
    exported_symbols: List[str] = Field(default_factory=list)
    raw_imports: List[str] = Field(default_factory=list)
    referenced_constants: List[str] = Field(default_factory=list)
    called_symbols: List[str] = Field(default_factory=list)
    mixins_and_parents: List[str] = Field(default_factory=list)
    associations: List[str] = Field(default_factory=list)
    routes: List[RouteEndpoint] = Field(default_factory=list)
    entrypoints: List[EntrypointInfo] = Field(default_factory=list)
    external_packages: Set[str] = Field(default_factory=set)
    docstring: str = Field(default="")
    lines_of_code: int = 0

    # Network science & topological metrics
    in_degree: int = 0
    out_degree: int = 0
    weighted_in_degree: float = 0.0
    weighted_out_degree: float = 0.0
    in_degree_centrality: float = 0.0
    pagerank: float = 0.0
    hub_score: float = 0.0
    authority_score: float = 0.0
    is_utility_sink: bool = False
    utility_reason: str = ""
    is_topological_entrypoint: bool = False
    community_id: Optional[str] = None


class DependencyEdge(BaseModel):
    source: str
    target: str
    kind: EdgeKind
    weight: float = 1.0
    symbols: List[str] = Field(default_factory=list)


class ExternalBoundary(BaseModel):
    id: str
    name: str
    kind: NodeKind = NodeKind.EXTERNAL_SYSTEM
    technology: str = ""
    description: str = ""
    referenced_by_modules: List[str] = Field(default_factory=list)
    referenced_by_components: List[str] = Field(default_factory=list)


class C3Component(BaseModel):
    id: str = Field(description="LikeC4 safe identifier, e.g., comp_extractors")
    name: str = Field(description="Human-readable component name")
    description: str = Field(default="")
    technology: str = Field(default="")
    community_index: int = 0
    node_ids: List[str] = Field(default_factory=list)
    directory_namespaces: List[str] = Field(default_factory=list)
    dominant_namespace: str = ""
    exposed_routes: List[RouteEndpoint] = Field(default_factory=list)
    entrypoints: List[EntrypointInfo] = Field(default_factory=list)
    outbound_component_deps: Dict[str, float] = Field(default_factory=dict)
    external_boundary_ids: List[str] = Field(default_factory=list)
    utility_sink_ids: List[str] = Field(default_factory=list)
    internal_cohesion: float = 0.0
    is_entrypoint_component: bool = False


class DriftIssue(BaseModel):
    kind: str = Field(description="HIDDEN_COUPLING | SPLIT_NAMESPACE | GOD_MODULE")
    severity: str = Field(default="MEDIUM", description="LOW | MEDIUM | HIGH")
    description: str
    source_module: str = ""
    target_module: str = ""
    weight: float = 0.0


class AnalysisResult(BaseModel):
    project_name: str
    root_path: str
    languages_detected: List[Language] = Field(default_factory=list)
    nodes: Dict[str, CodeNode] = Field(default_factory=dict)
    edges: List[DependencyEdge] = Field(default_factory=list)
    external_boundaries: Dict[str, ExternalBoundary] = Field(default_factory=dict)
    utility_sinks: List[str] = Field(default_factory=list)
    topological_entrypoints: List[str] = Field(default_factory=list)
    components: List[C3Component] = Field(default_factory=list)
    unmatched_openapi_routes: List[RouteEndpoint] = Field(default_factory=list)
    modularity_score: float = 0.0
    namespace_alignment_score: float = 0.0
    drift_issues: List[DriftIssue] = Field(default_factory=list)
