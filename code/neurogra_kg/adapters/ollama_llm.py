"""Versioned structured tasks with validated caching and source-independent transport."""

import json
import time
import urllib.error
from pathlib import Path

from pydantic import BaseModel, ValidationError
from neurogra_common.schema import grammar_schema
from neurogra_common.llm_log import logged_model_call, record_llm_event

from ..config import LLMConfig
from ..runtime import MODEL_LOCK
from ..utils import atomic_json, canonical, identity, checksum
from .http import model_info, request_json


def source_bound_schema(schema, payload):
    """Constrain copied source IDs during decoding as well as after generation."""
    block_ids = set()
    def collect(value):
        if isinstance(value, dict):
            if isinstance(value.get("block_id"), str):
                block_ids.add(value["block_id"])
            for child in value.values(): collect(child)
        elif isinstance(value, list):
            for child in value: collect(child)
    collect(payload)
    result = schema.model_json_schema()
    target_ids = sorted({row["target"] for row in payload.get("targets", []) if isinstance(row, dict) and isinstance(row.get("target"), str)})
    def constrain(value):
        if isinstance(value, dict):
            properties = value.get("properties", {})
            if block_ids and "block_id" in properties:
                properties["block_id"]["enum"] = sorted(block_ids)
            if target_ids and "target" in properties:
                properties["target"]["enum"] = target_ids
            for child in value.values(): constrain(child)
        elif isinstance(value, list):
            for child in value: constrain(child)
    constrain(result)
    return result


class OllamaLLMAdapter:
    def __init__(self, config: LLMConfig, output_root: Path, prompt_version="v1", transport=None):
        self.config, self.output_root, self.prompt_version = config, Path(output_root), prompt_version
        self.transport = transport or request_json
        self.prompt_root = Path(__file__).resolve().parents[1] / "prompts"
        self.last_cache_path = None

    def invalidate_last(self, reason):
        """Quarantine a schema-valid output rejected by downstream source/logic checks."""
        self.invalidate_cached(self.last_cache_path, reason)
        self.last_cache_path = None

    def invalidate_cached(self, path, reason):
        if path is not None and path.is_file():
            record = json.loads(path.read_text(encoding="utf-8"))
            record.update(status="semantic_rejected", rejection_reason=str(reason)[:120])
            atomic_json(self.output_root / "llm_rejected" / path.name, record)
            path.unlink()

    def model_digest(self, timeout=10):
        if self.transport is request_json:
            return model_info(self.config.base_url, self.config.model, timeout)["digest"]
        rows = self.transport(self.config.base_url + "/api/tags", None, timeout)["models"]
        return next(row["digest"] for row in rows if row["name"] == self.config.model)

    def prompt_binding(self):
        return {"config": self.config.model_dump(mode="json"),
                "prompt_version": self.prompt_version, "prompt_files": {
                    path.name: checksum(path) for path in sorted(self.prompt_root.iterdir()) if path.is_file()}}

    def binding(self, timeout=10):
        return {**self.prompt_binding(), "model_digest": self.model_digest(timeout)}

    def _messages(self, task, payload, schema):
        common = (self.prompt_root / "common.system.txt").read_text(encoding="utf-8")
        system = (self.prompt_root / f"{task}.system.txt").read_text(encoding="utf-8")
        template = (self.prompt_root / f"{task}.user.txt").read_text(encoding="utf-8")
        content = template.replace("{{input_json}}", canonical(payload)).replace(
            "{{output_schema}}", canonical(schema.model_json_schema()))
        if len(content) > self.config.max_input_chars:
            raise ValueError("llm_input_over_character_budget:split_structural_unit")
        return [{"role": "system", "content": common + "\n" + system},
                {"role": "user", "content": content}]

    def execute(self, task: str, payload: dict, schema: type[BaseModel], budget=None):
        self.last_cache_path = None
        timeout = min(10, budget.remaining()) if budget else 10
        model_digest = self.model_digest(timeout)
        messages = self._messages(task, payload, schema)
        output_schema = source_bound_schema(schema, payload)
        configurations = [self.config.model_dump(mode="json")]
        # Increasing the character acceptance bound changes no inference option.
        # Reuse old outputs only for otherwise identical messages/schema/config.
        if self.config.max_input_chars > 14000:
            configurations.append({**configurations[0], "max_input_chars": 14000})
        def keys():
            return [identity("task", [task, model_digest, config, self.prompt_version, messages, output_schema])
                    for config in configurations]
        cache_key = keys()[0]
        # Retrying a quarantined schema-valid output must receive the concrete
        # rejection reason; replaying the same deterministic prompt repeats errors.
        for _ in range(2):
            rejected_path = next((self.output_root / "llm_rejected" / (key + ".json") for key in keys()
                                  if (self.output_root / "llm_rejected" / (key + ".json")).is_file()), None)
            if rejected_path is None:
                break
            rejected = json.loads(rejected_path.read_text(encoding="utf-8"))
            messages = messages + [
                {"role": "assistant", "content": canonical(rejected.get("output", {}))},
                {"role": "user", "content": "上次结果被代码校验拒绝，原因：" + str(rejected.get("rejection_reason", "")) +
                    "。请仅依据最初提供的资料和 Schema 修复，不能编造来源或放宽条件。"
                    "所有 quote 必须来自相应 block 原文，不能引用用途/字段目录的键名，不能把译文当作原文。"
                    "quote 不能用省略号 ... 或 … 拼接不连续片段；请选择原文中连续的短句，不要重写。"
                    "缺少用途或条件证据时明确返回空/未知，不补造引用。引用、节点关联与数字必须准确。"},
            ]
            cache_key = keys()[0]
        cache_path = self.output_root / "llm_cache" / (cache_key + ".json")
        cached_path = next((self.output_root / "llm_cache" / (key + ".json") for key in keys()
                            if (self.output_root / "llm_cache" / (key + ".json")).is_file()), None)
        if cached_path is not None:
            cached = json.loads(cached_path.read_text(encoding="utf-8"))
            record_llm_event('cache_hit', component='kg', task=task, cache_key=cached_path.stem,
                             model=self.config.model, messages=messages, output=cached['output'])
            self.last_cache_path = cached_path
            return schema.model_validate(cached["output"])
        errors = []
        for repair in range(self.config.max_format_repair_attempts + 1):
            if budget:
                budget.reserve("llm")
            fmt = grammar_schema(output_schema) if self.config.structured_output == "json_schema" else "json"
            request = {"model": self.config.model, "messages": messages, "stream": False,
                       "format": fmt, "keep_alive": self.config.keep_alive,
                       "options": {"temperature": self.config.temperature,
                                   "num_ctx": self.config.context_tokens,
                                   "num_predict": self.config.max_output_tokens}}
            if self.config.thinking != "auto":
                request["think"] = self.config.thinking == "enabled"
            started = time.monotonic()
            log = {"task": task, "cache_key": cache_key, "model": self.config.model,
                   "model_digest": model_digest, "prompt_version": self.prompt_version,
                   "input_hash": identity("input", payload), "attempt": repair}
            try:
                with MODEL_LOCK:
                    response = None
                    for attempt in range(self.config.max_transport_attempts):
                        if attempt and budget:
                            budget.reserve("llm")
                        timeout = min(self.config.timeout_seconds, budget.remaining()) if budget else self.config.timeout_seconds
                        try:
                            response = logged_model_call(self.transport, self.config.base_url.rstrip("/") + "/api/chat", request, timeout,
                                component='kg', task=task, cache_key=cache_key, repair=repair, transport_attempt=attempt)
                            break
                        except urllib.error.URLError as exc:
                            # Retrying an observation timeout may duplicate a still-running request.
                            if isinstance(exc.reason, TimeoutError) or attempt + 1 == self.config.max_transport_attempts:
                                raise
                    if self.model_digest(min(10, budget.remaining()) if budget else 10) != model_digest:
                        raise ValueError("generation_model_changed_during_call")
                content = response.get("message", {}).get("content", "")
                if response.get("done_reason") == "length":
                    raise ValueError("llm_output_truncated")
                output = schema.model_validate_json(content)
                log.update(status="validated", seconds=time.monotonic() - started,
                           output=output.model_dump(mode="json"),
                           eval_count=response.get("eval_count"), prompt_eval_count=response.get("prompt_eval_count"))
                atomic_json(cache_path, log)
                self.last_cache_path = cache_path
                atomic_json(self.output_root / "llm_calls" / f"{cache_key}-{repair}.json", log)
                return output
            except (ValidationError, json.JSONDecodeError) as exc:
                errors.append(type(exc).__name__)
                log.update(status="invalid_format", seconds=time.monotonic() - started,
                           error_type=type(exc).__name__)
                atomic_json(self.output_root / "llm_calls" / f"{cache_key}-{repair}.json", log)
                if repair < self.config.max_format_repair_attempts:
                    messages = messages + [{"role": "assistant", "content": content},
                        {"role": "user", "content": "上次输出未通过给定 JSON Schema，校验原因：" + str(exc)[:1500] +
                         "。仅修复格式和结构，重新依据原输入输出；不能补写事实或改变任务。"}]
            except Exception as exc:
                log.update(status="failed", seconds=time.monotonic() - started, error_type=type(exc).__name__)
                atomic_json(self.output_root / "llm_calls" / f"{cache_key}-{repair}.json", log)
                raise
        raise ValueError("llm_schema_validation_failed:" + ",".join(errors))
