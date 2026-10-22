"""Optional LLM Agent for semantic C3 component synthesis (supports Gemini, OpenAI, and Anthropic)."""

from __future__ import annotations

import json
import logging
import os

import httpx

from varuna.enrichment.heuristic_namer import enrich_components_heuristically
from varuna.models.graph_schema import C3Component, CodeNode

logger = logging.getLogger(__name__)


def enrich_components_with_llm(
    components: list[C3Component],
    nodes: dict[str, CodeNode],
    use_llm: bool = False,
) -> bool:
    """
    First applies deterministic heuristic naming, then (if `use_llm=True` and an API key is present)
    invokes an LLM agent with a compact structural digest of the Louvain communities to refine
    component names and architectural descriptions.

    Returns True if LLM synthesis succeeded, False if heuristic fallback was used.
    """
    logger.info(
        "[Stage 4/5] Running deterministic heuristic naming for %d C3 components...",
        len(components),
    )
    # Always run heuristic namer first so every field has a solid baseline
    enrich_components_heuristically(components, nodes)

    if not use_llm:
        logger.info(
            "[Stage 4/5] LLM enrichment disabled (--no-llm); using heuristic component names."
        )
        return False

    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")

    if not (gemini_key or openai_key or anthropic_key):
        logger.info(
            "[Stage 4/5] No LLM API key found in environment; falling back to heuristic component names."
        )
        return False

    # Build a compact JSON digest (<250 tokens per component) — never send raw source files
    digest = []
    for comp in components:
        member_nodes = [nodes[nid] for nid in comp.node_ids if nid in nodes]
        digest.append(
            {
                "id": comp.id,
                "current_name": comp.name,
                "modules": [n.relative_path for n in member_nodes],
                "classes": [c for n in member_nodes for c in n.classes][:8],
                "functions": [
                    f for n in member_nodes for f in n.functions if not f.startswith("_")
                ][:8],
                "routes": [f"{r.method} {r.path}" for r in comp.exposed_routes][:5],
                "entrypoints": [ep.name for ep in comp.entrypoints][:5],
                "docstrings": [n.docstring for n in member_nodes if n.docstring][:3],
            }
        )

    prompt = (
        "You are a Software Architecture Agent analyzing C3 components discovered via the Louvain "
        "community detection algorithm. Given the following JSON array of discovered clusters, "
        "return ONLY a JSON object mapping each component `id` to an object with:\n"
        '  `{"name": "<concise 2-4 word C3 component name>", "description": "<1-sentence responsibility>"}`\n\n'
        f"Clusters:\n{json.dumps(digest, indent=2)}"
    )

    try:
        if gemini_key:
            logger.info("[Stage 4/5] Invoking Gemini LLM agent for semantic component synthesis...")
            response_mapping = _call_gemini(gemini_key, prompt)
        elif openai_key:
            logger.info("[Stage 4/5] Invoking OpenAI LLM agent for semantic component synthesis...")
            response_mapping = _call_openai(openai_key, prompt)
        else:
            logger.info(
                "[Stage 4/5] Invoking Anthropic LLM agent for semantic component synthesis..."
            )
            response_mapping = _call_anthropic(anthropic_key or "", prompt)

        for comp in components:
            if comp.id in response_mapping:
                item = response_mapping[comp.id]
                if isinstance(item, dict):
                    if item.get("name"):
                        comp.name = str(item["name"]).strip()
                    if item.get("description"):
                        comp.description = str(item["description"]).strip()
        logger.info("[Stage 4/5] LLM component synthesis completed successfully.")
        return True
    except Exception as exc:
        logger.debug(
            "[Stage 4/5] LLM synthesis failed (%s); retaining heuristic component names.", exc
        )
        return False


def _call_gemini(api_key: str, prompt: str) -> dict[str, dict[str, str]]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
    }
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)


def _call_openai(api_key: str, prompt: str) -> dict[str, dict[str, str]]:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
    }
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        return json.loads(text)


def _call_anthropic(api_key: str, prompt: str) -> dict[str, dict[str, str]]:
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": "claude-3-5-haiku-latest",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    }
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"]
        start = text.find("{")
        end = text.rfind("}")
        return json.loads(text[start : end + 1])
