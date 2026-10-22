"""LikeC4 DSL, Structurizr DSL, and Markdown modularity report emitters."""

from enum import Enum

from varuna.emitters.likec4_emitter import emit_likec4_dsl, generate_likec4_string
from varuna.emitters.report_emitter import emit_markdown_report, generate_markdown_report_string
from varuna.emitters.structurizr_emitter import (
    emit_structurizr_dsl,
    generate_structurizr_string,
)


class OutputEngine(str, Enum):
    LIKEC4 = "likec4"
    STRUCTURIZR = "structurizr"
    BOTH = "both"


__all__ = [
    "OutputEngine",
    "emit_likec4_dsl",
    "emit_markdown_report",
    "emit_structurizr_dsl",
    "generate_likec4_string",
    "generate_markdown_report_string",
    "generate_structurizr_string",
]
