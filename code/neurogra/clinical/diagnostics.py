"""Read-only capability inspection; model smoke uses a real budgeted request."""
import importlib.metadata
import importlib.util
import sys
from .config import ClinicalConfig
from .schemas import Capability, CapabilityReport, Contract
from .llm import http_json
from .retrieval import pin_release, knowledge_settings


class SmokeResponse(Contract):
    ok: bool


def probe_capabilities(config: ClinicalConfig, gateway=None) -> CapabilityReport:
    report = CapabilityReport(components=[
        Capability(name="python", configured=True, available=True, verified=True,
                   details={"executable": sys.executable, "version": sys.version.split()[0]})])
    for module, package in [("pypdf", "pypdf"), ("openpyxl", "openpyxl"),
                            ("docx", "python-docx"), ("pydicom", "pydicom"), ("SimpleITK", "SimpleITK")]:
        available = importlib.util.find_spec(module) is not None
        version = importlib.metadata.version(package) if available else None
        report.components.append(Capability(name="parser:" + module, configured=available, available=available,
                                            details={"version": version}))
    try:
        tags = http_json(config.model.base_url.rstrip("/") + "/api/tags", timeout=5)
        model = next((m for m in tags.get("models", []) if m.get("name") == config.model.model), None)
        available = model is not None
        verified = bool(gateway.generate_structured("返回ok=true，用于非患者连通性测试。", {"ping": True}, SmokeResponse).ok) if available and gateway else False
        report.components.append(Capability(name="llm", configured=True, available=available, verified=verified,
                                            details={"model": config.model.model, "digest": model.get("digest") if model else None}))
    except Exception as exc:
        report.components.append(Capability(name="llm", configured=True, available=False,
                                            details={"error_type": type(exc).__name__}))
    try:
        release_id = pin_release(config)
        report.components.append(Capability(name="knowledge", configured=True, available=True, verified=True,
                                            details={"release_id": release_id}))
    except Exception as exc:
        report.components.append(Capability(name="knowledge", configured=True, available=False,
                                            details={"error_type": type(exc).__name__}))
    try:
        settings, _ = knowledge_settings(config)
        graph = settings.graph_store
        configured = graph.provider == "neo4j"
        if configured:
            from neo4j import GraphDatabase
            with GraphDatabase.driver(graph.uri, auth=(graph.username, graph.resolve_password()),
                                      connection_timeout=5, connection_acquisition_timeout=5) as driver:
                driver.verify_connectivity()
        report.components.append(Capability(name="graph", configured=configured, available=configured, verified=configured))
    except Exception as exc:
        report.components.append(Capability(name="graph", configured=True, available=False,
                                            details={"error_type": type(exc).__name__}))
    try:
        if not config.vector_enabled or not config.vector.index_manifest:
            raise ValueError('vector_not_configured')
        from .retrieval import readonly_repository
        from .vector import release_chunks, embedding_client
        from neurogra.knowledge.indexing.vector import VectorRetriever
        _, db = knowledge_settings(config)
        with readonly_repository(db) as repository:
            vector = VectorRetriever(config.resolve(config.vector.index_manifest), release_chunks(repository, release_id),
                                     release_id, embedding_client(config), config.vector.max_chars)
        if vector.client.model_digest() != vector.manifest.model_digest:
            raise ValueError('embedding_model_digest_mismatch')
        report.components.append(Capability(name='vector', configured=True, available=True, verified=True,
            details={'index_id': vector.manifest.index_id, 'dimension': vector.manifest.dimension, 'query_executed': False}))
    except Exception as exc:
        report.components.append(Capability(name='vector', configured=config.vector_enabled, available=False,
            details={'error_type': type(exc).__name__, 'query_executed': False}))
    checks = {"repo": config.diamond_root.is_dir(), "python": config.diamond_python.is_file(),
              "checkpoint": config.diamond_checkpoint.is_file(),
              "raw_entrypoint": (config.diamond_root / "tools/predict_diamond_raw.py").is_file()}
    report.components.append(Capability(name="diamond", configured=True, available=all(checks.values()),
                                        verified=False, details={**checks, "inference_executed": False}))
    for role in ("history", "cognition", "laboratory", "imaging"):
        report.components.append(Capability(name="agent:" + role, configured=True, available=True,
                                            details={"implementation_stage": "S09-S13", "inference_executed": False,
                                                     "diamond_requires_validation": role == "imaging"}))
    return report
