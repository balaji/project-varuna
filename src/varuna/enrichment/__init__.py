"""Semantic enrichment and component naming for Louvain communities."""

from varuna.enrichment.heuristic_namer import enrich_components_heuristically
from varuna.enrichment.llm_agent import enrich_components_with_llm

__all__ = [
    "enrich_components_heuristically",
    "enrich_components_with_llm",
]
