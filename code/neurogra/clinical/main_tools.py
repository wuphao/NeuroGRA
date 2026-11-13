"""Main-agent-owned tools, immutable receipts and global model accounting."""

import time

from neurogra_common import validate_references
from neurogra_kb import KBConfig, TextKnowledgeService
from neurogra_kb import load_config as load_kb_config
from neurogra_kg.runtime import Budget

from .generation import patient_endpoint_allowed
from .knowledge_adapter import map_patient_facts
from .knowledge_adapter import settings as kg_settings
from .patient_data import PatientDataService
from .schemas import EvidenceItem, RequestContent
from .storage import BudgetExceeded
from .utils import identity


class RunModelBudget:
    """A direct KB budget using the same durable counters as clinical generation."""
    def __init__(self, store, run_id, scope, reserve_synthesis=6, max_llm_calls=None):
        self.store, self.run_id, self.scope = store, run_id, scope
        self.reserve_synthesis = reserve_synthesis
        self.calls = []
        self.max_llm_calls = max_llm_calls

    def remaining(self):
        remaining = self.store.run(self.run_id)["deadline"] - time.time()
        if remaining <= 0:
            raise TimeoutError("run_tool_deadline")
        return remaining

    def reserve(self, kind):
        if kind == "llm":
            import json
            limit = json.loads(self.store.run(self.run_id)["limits"])["llm"]
            used = sum(row["n"] for row in self.store.summary(self.run_id)["calls"] if row["kind"] == "llm")
            if limit - used <= self.reserve_synthesis:
                raise BudgetExceeded("synthesis_reserved")
        phase_scope = 'knowledge_models' if kind == 'llm' and self.max_llm_calls is not None else self.scope
        call_id, _ = self.store.reserve(self.run_id, kind, phase_scope,
                                       scope_limit=self.max_llm_calls if kind == 'llm' else None,
                                       reserved=self.reserve_synthesis if kind == "llm" else 0)
        self.calls.append(call_id)

    def finish(self, status):
        for call_id in self.calls:
            self.store.settle(call_id, status)


class BudgetedPort:
    """Observe actual SDK generation/embed reservations, including format repairs."""
    def __init__(self, port, ledger):
        self.port, self.ledger = port, ledger

    def __getattr__(self, name):
        return getattr(self.port, name)

    def _call(self, method, args, budget):
        if budget is None:
            budget = Budget(self.ledger.remaining())
        old = budget.on_reserve
        counts = {kind: getattr(budget, kind + "_calls") for kind in ("llm", "embedding")}
        def reserve(local):
            for kind in counts:
                value = getattr(local, kind + "_calls")
                for _ in range(value - counts[kind]):
                    self.ledger.reserve(kind)
                counts[kind] = value
            if old:
                old(local)
        budget.on_reserve = reserve
        try:
            return getattr(self.port, method)(*args, budget)
        finally:
            budget.on_reserve = old

    def execute(self, task, payload, schema, budget=None):
        return self._call("execute", (task, payload, schema), budget)

    def embed(self, texts, budget=None):
        return self._call("embed", (texts,), budget)


class MainToolExecutor:
    def __init__(self, snapshot, config, store, run_id, *, patient_service=None,
                 text_service=None, graph_release_id=None, diamond=None):
        self.snapshot, self.config, self.store, self.run_id = snapshot, config, store, run_id
        source_rows = store.objects(run_id, "rwe_source")
        rwe_config = None
        if source_rows and source_rows[-1]["payload"].get("rwe_config"):
            from .rwe import RweConfig
            rwe_config = RweConfig.model_validate(source_rows[-1]["payload"]["rwe_config"])
        self.patient = patient_service or PatientDataService(snapshot, config, rwe_config=rwe_config)
        self.diamond = diamond
        self.graph_release_id = graph_release_id
        if not config.knowledge_enabled('kg'):
            self.graph_release_id = None
        self.text = text_service
        if not config.knowledge_enabled('kb'):
            self.text = None
        elif self.text is None and (config.kb is not None or config.text_knowledge_config is not None):
            if config.kb is not None:
                cfg = config.kb
            else:
                path = config.resolve(config.text_knowledge_config)
                cfg = load_kb_config(path) if path.is_file() else KBConfig(root=config.project_root)
            self.text = TextKnowledgeService(cfg.model_copy(update={"root": config.project_root}))
        binding = self._saved("text_knowledge_binding", "main")
        if binding is None:
            try:
                release_id = (config.text_knowledge_release_id or self.text.active_release()) if self.text else None
                if release_id:
                    self.text.get_release(release_id)
                binding = {"release_id": release_id, "error": None}
            except Exception as exc:
                binding = {"release_id": None, "error": type(exc).__name__}
            store.save(run_id, "text_knowledge_binding", "main", binding)
        self.text_release_id = binding["release_id"]
        if self.text_release_id and self.text is not None:
            self.text.get_release(self.text_release_id)
        self.allowed_refs = {r.record_id for r in snapshot.records} | {
            o.observation_id for o in snapshot.observations} | {i.asset_id for i in snapshot.images}
        for record in store.objects(run_id, "main_tool_result"):
            self._register(record["payload"])

    def _saved(self, kind, object_id):
        return next((row["payload"] for row in self.store.objects(self.run_id, kind)
                     if row["object_id"] == object_id), None)

    def _register(self, result):
        if result.get('action_id'):
            self.allowed_refs.add(result['action_id'])
        data = result.get("data", {})
        for row in data.get("evidence", []):
            self.allowed_refs.update([row["evidence_id"], row["span_id"]])
        for row in data.get("observations", []):
            self.allowed_refs.add(row["observation_id"])
        for row in data.get("records", []):
            self.allowed_refs.add(row["record_id"])

    def execute(self, request: RequestContent, *, scope, allow_supplement=False, knowledge_access=None):
        access = knowledge_access or self.config.main_agent
        validate_references(request.source_refs, self.allowed_refs)
        action_id = identity("action", [self.run_id, scope, request.model_dump(mode="json"), allow_supplement,
            access.model_dump()])
        saved = self._saved("main_tool_result", action_id)
        if saved is not None:
            data = saved.get('data', {})
            if data.get('source_path'):
                from pathlib import Path

                from .utils import sha256_file
                path = Path(data['source_path'])
                if not path.is_file() or sha256_file(path) != data['source_checksum']:
                    raise ValueError('cached_patient_source_checksum_mismatch')
            return saved
        started = self._saved("main_tool_started", action_id)
        if started is not None:
            result = {"action_id": action_id, "request": request.model_dump(mode="json"),
                      "status": "indeterminate", "data": {}, "error": "interrupted_tool_not_repeated"}
            self.store.save(self.run_id, "main_tool_result", action_id, result)
            return result
        self.store.save(self.run_id, "main_tool_started", action_id, request)
        ledger = RunModelBudget(self.store, self.run_id, action_id, self.config.synthesis_reserved_calls,
                                self.config.knowledge_model_calls)
        kind = {"patient_data": "patient_data", "knowledge": "retrieval", "source_context": "retrieval",
                "imaging_tool": "image"}[request.type]
        call_id = None
        result = {"action_id": action_id, "request": request.model_dump(mode="json"), "data": {}}
        try:
            if request.type == "imaging_tool" and self.diamond is not None:
                ledger.remaining()  # DiamondTool owns the actual image-call reservation.
            else:
                call_id, _ = self.store.reserve(self.run_id, kind, action_id)
            if request.type == "patient_data":
                data = self.patient.fetch_patient_data(request, action_id)
                result.update(status=data.status, data=data.model_dump(mode="json"))
            elif request.type == "knowledge":
                data = self._knowledge(request, ledger, allow_supplement, access)
                result.update(status="found" if data["evidence"] else "empty", data=data)
            elif request.type == "source_context":
                result.update(status="found", data=self._source(request, access))
            else:
                if self.diamond is None:
                    result.update(status="unsupported", error="image_tool_unavailable")
                else:
                    data = self.diamond.predict(self.snapshot, self.store.run(self.run_id)["deadline"])
                    result.update(status=data.status, data=data.model_dump(mode="json"))
        except BudgetExceeded:
            # If the tool call could not be reserved, its backend was never
            # dispatched. A later budget failure may follow partial work and
            # must not be represented as an unexecuted request.
            result.update(status='budget_exhausted' if call_id is None else 'failed', error='BudgetExceeded')
        except Exception as exc:
            error = type(exc).__name__
            if isinstance(exc, ValueError) and str(exc).split(':')[0] in {
                'stale_or_invalid_source_cursor', 'source_item_exceeds_page_budget'}:
                error = str(exc).split(':')[0]
            result.update(status="failed", error=error)
        self.store.save(self.run_id, "main_tool_result", action_id, result)
        if call_id:
            self.store.settle(call_id, result["status"])
        ledger.finish("failed" if result["status"] in {"failed", "budget_exhausted"} else "completed")
        self._register(result)
        return result

    def _knowledge(self, request, ledger, allow_supplement, access=None):
        access = access or self.config.main_agent
        items, statuses, gaps, data_requests = [], {}, [], []
        if access.use_kb and self.text is not None and self.text_release_id:
            embedding = self.text.config.embedding
            vector_allowed = patient_endpoint_allowed(self.config.project_root, embedding.base_url, embedding.model)
            raw = self.text.search_text(request.question, release_id=self.text_release_id, budget=ledger,
                                        use_vector=vector_allowed)
            statuses.update({"kb_" + key: value for key, value in raw["backend_status"].items()})
            if not vector_allowed:
                statuses['kb_vector'] = 'blocked_patient_data_outbound'
            for row in raw["items"]:
                items.append(EvidenceItem(evidence_id=row["evidence_id"], document_id=row["document_id"],
                    span_id=row["id"], title=row["title"], quote=row["text"], context=row["text"],
                    page=row["page"], version=self.text_release_id, matched_by=["kb_" + key for key in row["matched_by"]],
                    page_kind=row["page_kind"], source_locator=row["source_locator"],
                    review_provenance="source_verified_raw_text", limitations=["原文背景资料，患者适用条件尚未验证"]))
        else:
            statuses["kb"] = "not_published" if access.use_kb else "disabled"
        if access.use_kg and self.graph_release_id and self.config.kg_representation == 'plain_triples':
            from .retrieval import fuse_evidence
            from .triple_knowledge import retrieve_triples
            rankings = {}
            for backend in ('bm25', 'graph'):
                status, found, metadata = retrieve_triples(self.config, self.graph_release_id,
                    request.question, backend, 10)
                statuses['kg_' + backend] = status
                rankings[backend] = found
            items.extend(fuse_evidence(rankings, 10))
        elif access.use_kg and self.graph_release_id and not self._graph_outbound_allowed():
            statuses['kg'] = 'blocked_patient_data_outbound'
        elif access.use_kg and self.graph_release_id:
            from neurogra_kg.adapters.ollama_embedding import OllamaEmbeddingAdapter
            from neurogra_kg.adapters.ollama_llm import OllamaLLMAdapter
            from neurogra_kg.api import KnowledgeGraphService
            from neurogra_kg.contracts.domain import RetrievalRequest as KGRequest
            cfg = kg_settings(self.config)
            llm = BudgetedPort(OllamaLLMAdapter(cfg.llm, cfg.resolve(cfg.paths.output_root)), ledger)
            embedding = BudgetedPort(OllamaEmbeddingAdapter(cfg.embedding, cfg.resolve(cfg.paths.output_root)), ledger)
            facts, mapping_audit = map_patient_facts(self.snapshot)
            # Query citations do not restrict the current-case facts needed for
            # applicability (e.g. age and function accompanying a biomarker query).
            self.store.event(self.run_id, 'graph_patient_mapping', {'scope': 'current_case',
                'query_source_refs': request.source_refs, 'facts': len(facts), 'audit': mapping_audit})
            reference = self.reference_time()
            try:
                with KnowledgeGraphService(cfg, llm=llm, embedding=embedding, read_only=True) as kg:
                    retrieved = kg.retrieve(KGRequest(question=request.question, purpose=request.purpose,
                        reference_time=reference, facts=facts, release_id=self.graph_release_id,
                        max_seconds=ledger.remaining(), max_llm_calls=(6 if allow_supplement else self.config.condition_model_calls),
                        max_supplement_rounds=1 if allow_supplement else 0))
                    statuses.update({"kg_" + key: value for key, value in retrieved.backend_status.items()})
                    gaps = [g.model_dump(mode="json") for g in retrieved.gaps]
                    data_requests = [g.model_dump(mode="json") for g in retrieved.data_requests]
                    for evidence in [*retrieved.applicable, *retrieved.unknown, *retrieved.excluded]:
                        statement = evidence.statement
                        for ref in statement.source_refs:
                            source = kg.get_source(ref.block_id, self.graph_release_id)
                            if source["block"]["text"][ref.start:ref.end] != ref.quote:
                                raise ValueError("graph_source_quote_mismatch")
                            state = evidence.state
                            language_limitations = [c.reason for c in evidence.condition_results if c.state != "SATISFIED"]
                            if statement.zh_annotation and statement.zh_annotation.status != "verified":
                                language_limitations.append("中文辅助表达未通过核验，保留原文依据")
                            items.append(EvidenceItem(evidence_id=identity("evidence", [evidence.evidence_id, ref.model_dump(mode="json")]),
                                statement_id=statement.statement_id, document_id=source["block"]["document_id"],
                                span_id=ref.block_id, quote=ref.quote, context=source["block"]["text"],
                                explanation_zh=statement.zh_annotation.text if statement.zh_annotation and statement.zh_annotation.status == "verified" else None,
                                source_language=source["document"].get("language", "und"),
                                title=source["document"]["title"], page=source["block"].get("page"),
                                page_kind=source["block"].get("page_kind"),
                                source_locator=source["block"].get("source_locator"),
                                version=self.graph_release_id, matched_by=["kg"],
                                applicability={"SATISFIED": "applicable", "UNKNOWN": "unknown", "VIOLATED": "not_applicable"}[state],
                                allowed_use="conditional_support" if state == "SATISFIED" else "background",
                                limitations=language_limitations,
                                condition_assessment=[c.model_dump(mode="json") for c in evidence.condition_results]))
            except Exception as exc:
                statuses["kg"] = "unavailable:" + type(exc).__name__
        else:
            statuses["kg"] = "not_published" if access.use_kg else "disabled"
        return {"evidence": [row.model_dump(mode="json") for row in items], "backend_status": statuses,
                "gaps": gaps, "data_requests": data_requests,
                "kb_release_id": self.text_release_id if access.use_kb else None,
                "kg_release_id": self.graph_release_id if access.use_kg else None}

    def _graph_outbound_allowed(self):
        cfg = kg_settings(self.config)
        endpoints = [(cfg.llm.base_url, cfg.llm.model), (cfg.embedding.base_url, cfg.embedding.model)]
        if cfg.graph.provider != 'memory':
            endpoints.append((cfg.graph.uri, ''))
        return all(patient_endpoint_allowed(self.config.project_root, url, model) for url, model in endpoints)

    def reference_time(self):
        rows = self.store.objects(self.run_id, "analysis_time")
        if rows:
            from datetime import datetime
            return datetime.fromisoformat(rows[0]["payload"]["value"])
        from datetime import datetime, timezone
        value = datetime.now(timezone.utc)
        self.store.save(self.run_id, "analysis_time", "main", {"value": value.isoformat(), "basis": "run_time"})
        return value

    def _source(self, request, access=None):
        from .context_views import source_page
        access = access or self.config.main_agent
        data = self._source_data(request, access)
        page = source_page(data, request, {
            'patient_id': self.snapshot.patient_id, 'case_version': self.snapshot.version,
            'source_refs': request.source_refs, 'access': access.model_dump(mode='json')})
        page['artifact_ref'] = request.source_refs[0]
        return page

    def _source_data(self, request, access=None):
        access = access or self.config.main_agent
        if len(request.source_refs) != 1:
            raise ValueError("one_source_reference_required")
        ref = request.source_refs[0]
        receipt = self._saved('main_tool_result', ref)
        if receipt:
            from .agent_policy import scoped_tool_data
            # Only data the caller is permitted to receive can be expanded.
            data = scoped_tool_data(receipt.get('data', {}), access)
            return {'artifact_ref': ref, **data}
        if ref in {r.record_id for r in self.snapshot.records}:
            return {"records": [self.patient.read_source(ref).model_dump(mode="json")]}
        for receipt in self.store.objects(self.run_id, "main_tool_result"):
            for evidence in receipt["payload"].get("data", {}).get("evidence", []):
                if evidence["evidence_id"] == ref:
                    ref = evidence["span_id"]
                    break
        if access.use_kb and self.text and self.text_release_id:
            try:
                return self.text.expand_source(ref, release_id=self.text_release_id)
            except ValueError:
                pass
        if access.use_kg and self.graph_release_id:
            from .knowledge_adapter import service
            with service(self.config) as kg:
                return {"source": kg.get_source(ref, self.graph_release_id),
                        "release_id": self.graph_release_id}
        raise ValueError("source_context_not_available")
