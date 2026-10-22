"""Scans infrastructure files and dependency manifests to identify C2 datastores, queues, and C1 external systems."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Tuple
import yaml

from varuna.extractors.base import sanitize_boundary_id
from varuna.models.graph_schema import ExternalBoundary, NodeKind

# Known packages and docker images mapped to (Boundary Name, NodeKind, Technology, Description)
KNOWN_EXTERNAL_DEPENDENCIES: Dict[str, Tuple[str, NodeKind, str, str]] = {
    # Databases & Caches (C2 Datastores)
    "psycopg": ("PostgreSQL Database", NodeKind.DATABASE, "PostgreSQL", "Primary relational database"),
    "psycopg2": ("PostgreSQL Database", NodeKind.DATABASE, "PostgreSQL", "Primary relational database"),
    "asyncpg": ("PostgreSQL Database", NodeKind.DATABASE, "PostgreSQL", "Async PostgreSQL database"),
    "pg": ("PostgreSQL Database", NodeKind.DATABASE, "PostgreSQL", "PostgreSQL database"),
    "sqlalchemy": ("Relational Database", NodeKind.DATABASE, "SQL / SQLAlchemy", "Relational database persistence"),
    "prisma": ("Relational Database", NodeKind.DATABASE, "Prisma ORM", "Application relational database"),
    "typeorm": ("Relational Database", NodeKind.DATABASE, "TypeORM", "Application relational database"),
    "mongoose": ("MongoDB Atlas / Cluster", NodeKind.DATABASE, "MongoDB", "Document store"),
    "pymongo": ("MongoDB Atlas / Cluster", NodeKind.DATABASE, "MongoDB", "Document store"),
    "redis": ("Redis Cache / Store", NodeKind.DATABASE, "Redis", "In-memory cache and state store"),
    "ioredis": ("Redis Cache / Store", NodeKind.DATABASE, "Redis", "In-memory cache and state store"),
    # Message Queues & Brokers (C2 Queues)
    "celery": ("Task Broker Queue", NodeKind.QUEUE, "Celery / Redis / AMQP", "Asynchronous job queue"),
    "sidekiq": ("Sidekiq Job Queue", NodeKind.QUEUE, "Sidekiq / Redis", "Background job processing queue"),
    "bullmq": ("BullMQ Job Queue", NodeKind.QUEUE, "BullMQ / Redis", "Distributed Node.js job queue"),
    "kafka-python": ("Kafka Event Bus", NodeKind.QUEUE, "Apache Kafka", "Event streaming platform"),
    "kafkajs": ("Kafka Event Bus", NodeKind.QUEUE, "Apache Kafka", "Event streaming platform"),
    "pika": ("RabbitMQ Broker", NodeKind.QUEUE, "RabbitMQ", "AMQP message broker"),
    # External SaaS & AI Systems (C1 External Systems)
    "stripe": ("Stripe Billing API", NodeKind.EXTERNAL_SYSTEM, "REST / Stripe SDK", "Payment processing & billing"),
    "boto3": ("AWS Cloud Services", NodeKind.EXTERNAL_SYSTEM, "AWS SDK", "Cloud storage and infrastructure APIs"),
    "@aws-sdk": ("AWS Cloud Services", NodeKind.EXTERNAL_SYSTEM, "AWS SDK", "Cloud storage and infrastructure APIs"),
    "aws-sdk": ("AWS Cloud Services", NodeKind.EXTERNAL_SYSTEM, "AWS SDK", "Cloud storage and infrastructure APIs"),
    "openai": ("LLM Inference API", NodeKind.EXTERNAL_SYSTEM, "OpenAI / LLM API", "Semantic synthesis & AI completions"),
    "anthropic": ("LLM Inference API", NodeKind.EXTERNAL_SYSTEM, "Anthropic API", "Semantic synthesis & AI completions"),
    "google-genai": ("LLM Inference API", NodeKind.EXTERNAL_SYSTEM, "Google Gemini API", "Semantic synthesis & AI completions"),
    "httpx": ("External HTTP / LLM APIs", NodeKind.EXTERNAL_SYSTEM, "HTTPS / REST", "External API & LLM gateway client"),
    "sendgrid": ("SendGrid Email API", NodeKind.EXTERNAL_SYSTEM, "SendGrid", "Transactional email delivery"),
    "twilio": ("Twilio Messaging API", NodeKind.EXTERNAL_SYSTEM, "Twilio", "SMS and communications gateway"),
    "sentry-sdk": ("Sentry Telemetry", NodeKind.EXTERNAL_SYSTEM, "Sentry", "Error monitoring and observability"),
    # Core Embedded Engines (for developer/CLI tools like Varuna itself)
    "tree-sitter": ("Tree-sitter AST Runtime", NodeKind.EXTERNAL_SYSTEM, "Native C / Tree-sitter", "Multi-language incremental AST parser"),
    "networkx": ("NetworkX Graph Runtime", NodeKind.EXTERNAL_SYSTEM, "NetworkX / SciPy", "Graph topology & Louvain community engine"),
}


def scan_infrastructure_and_manifests(repo_root: Path) -> Dict[str, ExternalBoundary]:
    """Scan docker-compose.yml, pyproject.toml, package.json, and Gemfile for C1/C2 boundaries."""
    boundaries: Dict[str, ExternalBoundary] = {}

    # 1. Scan docker-compose.yml / compose.yaml
    for compose_name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        compose_path = repo_root / compose_name
        if compose_path.is_file():
            _parse_docker_compose(compose_path, boundaries)

    # 2. Scan pyproject.toml / requirements.txt
    pyproject = repo_root / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8", errors="replace").lower()
        _match_known_packages(text, boundaries)

    req_txt = repo_root / "requirements.txt"
    if req_txt.is_file():
        text = req_txt.read_text(encoding="utf-8", errors="replace").lower()
        _match_known_packages(text, boundaries)

    # 3. Scan package.json
    pkg_json = repo_root / "package.json"
    if pkg_json.is_file():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
            deps = list(data.get("dependencies", {}).keys()) + list(data.get("devDependencies", {}).keys())
            _match_known_packages("\n".join(deps).lower(), boundaries)
        except Exception:
            pass

    # 4. Scan Gemfile
    gemfile = repo_root / "Gemfile"
    if gemfile.is_file():
        text = gemfile.read_text(encoding="utf-8", errors="replace").lower()
        _match_known_packages(text, boundaries)

    return boundaries


def _match_known_packages(manifest_text: str, boundaries: Dict[str, ExternalBoundary]) -> None:
    for pkg_key, (name, kind, tech, desc) in KNOWN_EXTERNAL_DEPENDENCIES.items():
        pattern = rf"(?<![a-z0-9_]){re.escape(pkg_key)}(?![a-z0-9_])"
        if re.search(pattern, manifest_text):
            b_id = _safe_id(name)
            if b_id not in boundaries:
                boundaries[b_id] = ExternalBoundary(
                    id=b_id,
                    name=name,
                    kind=kind,
                    technology=tech,
                    description=desc,
                )


def _parse_docker_compose(compose_path: Path, boundaries: Dict[str, ExternalBoundary]) -> None:
    try:
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return
    if not isinstance(data, dict):
        return
    services = data.get("services", {})
    if not isinstance(services, dict):
        return

    for svc_name, svc_cfg in services.items():
        if not isinstance(svc_cfg, dict):
            continue
        image = str(svc_cfg.get("image", "")).lower()
        if any(db in image for db in ("postgres", "mysql", "mariadb", "mongo", "redis", "clickhouse")):
            b_id = _safe_id(f"db_{svc_name}")
            boundaries[b_id] = ExternalBoundary(
                id=b_id,
                name=f"{svc_name.title()} Datastore",
                kind=NodeKind.DATABASE,
                technology=image or "Database",
                description=f"Docker Compose datastore service `{svc_name}`",
            )
        elif any(q in image for q in ("kafka", "rabbitmq", "nats", "pulsar")):
            b_id = _safe_id(f"queue_{svc_name}")
            boundaries[b_id] = ExternalBoundary(
                id=b_id,
                name=f"{svc_name.title()} Broker",
                kind=NodeKind.QUEUE,
                technology=image or "Message Broker",
                description=f"Docker Compose message broker `{svc_name}`",
            )


def _safe_id(raw: str) -> str:
    return sanitize_boundary_id(raw)
