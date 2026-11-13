"""Structured generation. Every attempt, including repair, consumes budget."""
import json
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TypeVar
from pydantic import BaseModel, ValidationError
from .config import ModelConfig
from .generation import GenerationClient, GenerationError, NoRedirect, local_model
from .storage import RunStore
from .utils import dumps, fingerprint
from neurogra_common.schema import grammar_schema
from neurogra_common.llm_log import logged_model_call, record_llm_event
from neurogra_common.model_admission import admit_model
from .context_budget import ContextBudgetExceeded, messages_for, require_fit

T = TypeVar("T", bound=BaseModel)


class ModelFailure(RuntimeError):
    pass


class _TransportFailure(RuntimeError):
    pass


def http_json(url, payload=None, timeout=10):
    body = None if payload is None else dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
        return json.load(response)


class ModelGateway:
    def __init__(self, config: ModelConfig, store: RunStore, run_id: str, transport=None, scope=None, scope_limit=None, deadline=None, reserved_calls=0, before_model=None):
        self.config, self.store, self.run_id = config, store, run_id
        self.transport = transport or http_json
        self.scope, self.scope_limit, self.deadline = scope, scope_limit, deadline
        self.reserved_calls = reserved_calls
        self.before_model = before_model

    def generate_structured(self, system: str, data, schema: type[T]) -> T:
        try:
            root = (Path.cwd() if local_model(self.config) else
                    json.loads(self.store.run(self.run_id)['config']).get('project_root', Path.cwd()))
            client = GenerationClient(self.config, root, transport=self.transport)
        except GenerationError as error:
            raise ModelFailure(str(error)) from None
        messages = messages_for(system, data)
        repaired = False
        failures = 0
        while True:
            try:
                context_metrics = require_fit(messages, schema, self.config)
            except ContextBudgetExceeded as exc:
                self.store.event(self.run_id, 'context_rejected', {
                    'scope': self.scope, 'schema': schema.__name__, 'reason': str(exc)})
                raise ModelFailure(str(exc)) from exc
            call_id = None
            sent = False
            try:
                with admit_model('clinical:' + (self.scope or schema.__name__)):
                    if self.before_model:
                        self.before_model()
                    # The outbound policy and credentials may change while queued.
                    try:
                        client = GenerationClient(self.config, root, transport=self.transport)
                    except GenerationError as error:
                        raise ModelFailure(str(error)) from None
                    if self.reserved_calls:
                        limits = json.loads(self.store.run(self.run_id)['limits'])
                        used = sum(row['n'] for row in self.store.summary(self.run_id)['calls'] if row['kind'] == 'llm')
                        if limits['llm'] - used <= self.reserved_calls:
                            from .storage import BudgetExceeded
                            raise BudgetExceeded('synthesis_reserved')
                    if self.deadline is not None and self.deadline <= time.time():
                        raise ModelFailure("task_deadline")
                    call_id, remaining = self.store.reserve(self.run_id, "llm", self.scope, self.scope_limit,
                                                            reserved=self.reserved_calls)
                    self.store.save(self.run_id, 'context_manifest', call_id, {
                        **context_metrics, 'scope': self.scope, 'request_hash': fingerprint(messages),
                        'schema_hash': fingerprint(schema.model_json_schema())})
                    if self.deadline is not None:
                        remaining = min(remaining, self.deadline - time.time())
                        if remaining <= 0:
                            raise TimeoutError("task_deadline")
                    payload = client.payload(messages, schema=grammar_schema(schema.model_json_schema()))
                    self.store.event(self.run_id, "llm_started",
                                     {"call_id": call_id, "model": self.config.model, "schema": schema.__name__,
                                      "prompt_hash": fingerprint(system), "input_hash": fingerprint(data)})
                    try:
                        from neurogra_kg.runtime import MODEL_LOCK
                        if not MODEL_LOCK.acquire(timeout=min(remaining, self.config.timeout_seconds)):
                            raise TimeoutError("model_resource_wait_timeout")
                        try:
                            remaining = self.store.run(self.run_id)["deadline"] - time.time()
                            if self.deadline is not None:
                                remaining = min(remaining, self.deadline - time.time())
                            if remaining <= 0:
                                raise TimeoutError("model_resource_deadline")
                            sent = True
                            response = logged_model_call(client.request, client.chat_url, payload,
                                min(remaining, self.config.timeout_seconds), component='clinical', run_id=self.run_id,
                                scope=self.scope, budget_call_id=call_id, schema=schema.__name__,
                                repair=repaired, transport_failures=failures)
                            response = client.normalize(response)
                        finally:
                            MODEL_LOCK.release()
                    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, ValueError) as error:
                        raise _TransportFailure(type(error).__name__) from error
            except _TransportFailure as exc:
                cause = exc.__cause__
                reason = cause.reason if isinstance(cause, urllib.error.URLError) else cause
                # A lost response may still be executing remotely. Retry only
                # when the request was never sent or connection setup failed.
                safe_retry = not sent or isinstance(reason, (ConnectionRefusedError, socket.gaierror))
                if call_id:
                    self.store.settle(call_id, "failed" if safe_retry else "indeterminate", {"error_type": str(exc)})
                if not safe_retry:
                    raise ModelFailure("model_call_indeterminate") from exc
                failures += 1
                if failures >= self.config.transport_attempts:
                    raise ModelFailure("model_transport_failed") from exc
                continue
            except BaseException:
                if call_id:
                    self.store.settle(call_id, "indeterminate" if sent else "failed")
                raise
            usage = ({k: response.get(k) for k in ("prompt_eval_count", "eval_count", "total_duration")}
                     if isinstance(response, dict) else {})
            self.store.event(self.run_id, 'context_usage', {
                'call_id': call_id, **context_metrics,
                'actual_input_tokens': usage.get('prompt_eval_count')})
            try:
                result = schema.model_validate_json(response["message"]["content"])
            except (ValidationError, KeyError, TypeError, ValueError) as exc:
                record_llm_event('validation', run_id=self.run_id, budget_call_id=call_id,
                    status='invalid_output', error_type=type(exc).__name__, error=str(exc))
                self.store.settle(call_id, "invalid_output", usage)
                if repaired:
                    raise ModelFailure("model_schema_invalid_after_repair") from exc
                repaired = True
                # Retry original data, not untrusted generated facts or unrestricted tool content.
                errors = exc.errors(include_input=False, include_url=False) if isinstance(exc, ValidationError) else []
                errors.sort(key=lambda e: e['type'] not in {'too_long', 'too_short', 'missing'})
                feedback = [{'path': '.'.join(map(str, e['loc'])), 'message': e['msg'][:120]}
                            for e in errors[:2]]
                messages.append({"role": "user", "content": "上一次输出结构不合法。纠正以下字段限制，仍只引用原输入；缺少事实时返回空集合：" + dumps(feedback)})
                continue
            self.store.settle(call_id, "completed", usage)
            record_llm_event('validation', run_id=self.run_id, budget_call_id=call_id,
                             status='completed', output=result.model_dump(mode='json'))
            self.store.save(self.run_id, "model_response", call_id, result)
            self.store.event(self.run_id, "llm_completed", {"call_id": call_id, "schema": schema.__name__})
            return result
