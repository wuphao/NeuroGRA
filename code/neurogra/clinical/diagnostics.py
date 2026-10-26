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
    from .knowledge_adapter import service as knowledge_service
    try:
        from neurogra_kb import KBConfig, TextKnowledgeService, load_config as load_kb
        path = config.resolve(config.text_knowledge_config) if config.text_knowledge_config else None
        kb_config = load_kb(path) if path and path.is_file() else KBConfig(root=config.project_root)
        kb = TextKnowledgeService(kb_config.model_copy(update={'root': config.project_root}))
        release_id = config.text_knowledge_release_id or kb.active_release()
        manifest = kb.get_release(release_id) if release_id else None
        report.components.append(Capability(name='text_knowledge', configured=config.text_knowledge_config is not None,
            available=manifest is not None, verified=manifest is not None,
            details={'release_id': release_id, 'resources': manifest['resource_count'] if manifest else 0,
                     'vector_published': manifest['vector'] if manifest else False, 'query_executed': False}))
    except Exception as exc:
        report.components.append(Capability(name='text_knowledge', configured=config.text_knowledge_config is not None,
            available=False, details={'error_type': type(exc).__name__}))
    try:
        with knowledge_service(config) as kg:
            info = kg.probe_graph()
        configured = info.get("provider") == "neo4j"
        report.components.append(Capability(name="graph", configured=configured,
            available=bool(info.get("available")), verified=bool(info.get("available")), details=info))
    except Exception as exc:
        report.components.append(Capability(name="graph", configured=True, available=False,
            details={"error_type": type(exc).__name__}))
    try:
        if not config.vector_enabled:
            raise ValueError("vector_not_configured")
        with knowledge_service(config) as kg:
            index = kg.inspect_vector_index(config.knowledge_release_id)
        report.components.append(Capability(name="vector", configured=True, available=True, verified=True,
            details={"release_id": index["release_id"], "dimension": index["dimension"],
                     "model": index["model"], "query_executed": False}))
    except Exception as exc:
        report.components.append(Capability(name="vector", configured=config.vector_enabled, available=False,
            details={"error_type": type(exc).__name__, "query_executed": False}))
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
