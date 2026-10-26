"""Real model capability checks; installation and execution are separate facts."""

import importlib.metadata
from datetime import datetime, timezone

from .adapters.http import model_info, request_json
from .adapters.ollama_embedding import OllamaEmbeddingAdapter
from .adapters.ollama_llm import OllamaLLMAdapter
from .contracts.tasks import ProbeOutput, TASK_SCHEMAS
from .runtime import Budget
from .utils import atomic_json


def probe(config, execute=True):
    root = config.resolve(config.paths.output_root)
    report = {"timestamp": datetime.now(timezone.utc).isoformat(), "checks": {},
              "prompt_schemas": {task: bool(schema.model_json_schema()) for task, schema in TASK_SCHEMAS.items()}}
    try:
        report["ollama_version"] = request_json(config.llm.base_url + "/api/version", timeout=10)
    except Exception as exc:
        report["ollama_version_error"] = type(exc).__name__
    for label, settings in [("llm", config.llm), ("embedding", config.embedding)]:
        check = {"model": settings.model, "installed": False, "executed": False}
        try:
            info = model_info(settings.base_url, settings.model)
            check.update(installed=True, digest=info["digest"], size_bytes=info.get("size"))
            show = request_json(settings.base_url + "/api/show", {"model": settings.model}, timeout=10)
            check["capabilities"] = show.get("capabilities", [])
            check["model_info"] = {key: value for key, value in show.get("model_info", {}).items()
                                   if key.endswith("context_length") or key.endswith("embedding_length")}
            if execute:
                budget = Budget(max_seconds=settings.timeout_seconds + 30)
                if label == "llm":
                    adapter = OllamaLLMAdapter(settings, root)
                    output = adapter.execute("probe", {"blocks": [{"block_id": "probe-block",
                        "text": "这是一段非患者连通性检查文本。"}]}, ProbeOutput, budget)
                    quote_valid = any(ref.block_id == "probe-block" and ref.quote in
                        "这是一段非患者连通性检查文本。" and ref.occurrence == 0 for ref in output.source_refs)
                    if not output.ok or not quote_valid:
                        adapter.invalidate_last("probe_quote_or_ok_invalid")
                    check.update(executed=True, structured_output=settings.structured_output,
                                 quote_valid=quote_valid,
                                 ok=output.ok, thinking=settings.thinking)
                else:
                    adapter = OllamaEmbeddingAdapter(settings, root)
                    vectors = adapter.embed(["医学知识检索连通性检查。", "原文来源定位。"], budget)
                    check.update(executed=True, dimension=adapter.dimension,
                                 rows=len(vectors), normalization="l2")
        except Exception as exc:
            check["error_type"] = type(exc).__name__
            # Errors contain only sanitized adapter diagnostics, not patient/model input.
            check["error"] = str(exc)[:200]
        report["checks"][label] = check
    for package in ["pydantic", "PyMuPDF", "numpy", "neo4j"]:
        try:
            report.setdefault("dependencies", {})[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            report.setdefault("dependencies", {})[package] = None
    report["ready"] = all(item.get("executed") for item in report["checks"].values()) and bool(
        report["checks"]["llm"].get("ok") and report["checks"]["llm"].get("quote_valid"))
    atomic_json(root / "runtime_profile.json", report)
    return report
