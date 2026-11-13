"""Pinned clinical retrieval through the independent KG SDK."""
import time
from contextlib import nullcontext
from neurogra_common.model_admission import admit_model
from .config import ClinicalConfig
from .schemas import CaseSnapshot, EvidenceBundle, EvidenceItem, Issue, RetrievalRequest
from .utils import identity
from .storage import RunStore
from .knowledge_adapter import service as knowledge_service, settings as kg_settings, retrieve_backend


def knowledge_settings(config):
    settings = kg_settings(config)
    return settings, settings.resolve(settings.paths.data_root) / "registry.sqlite"


def pin_release(config):
    with knowledge_service(config) as kg:
        return kg.get_release(config.knowledge_release_id)["release_id"]


class KnowledgeService:
    def __init__(self, config: ClinicalConfig, release_id: str, store: RunStore, run_id: str):
        self.config, self.release_id, self.store, self.run_id = config, release_id, store, run_id

    def search_knowledge(self, request: RetrievalRequest, deadline=None) -> EvidenceBundle:
        if request.knowledge_release_id and request.knowledge_release_id != self.release_id:
            raise ValueError("knowledge_release_mismatch")
        if deadline is not None and time.time() >= deadline:
            raise TimeoutError('retrieval_deadline')
        call_id, _ = self.store.reserve(self.run_id, "retrieval")
        deadline = min(self.store.run(self.run_id)['deadline'], deadline if deadline is not None else float('inf'))
        statuses, rankings, issues, metadata = {}, {}, [], {}
        try:
            for backend in dict.fromkeys(request.requested_backends):
                try:
                    if time.time() >= deadline:
                        raise TimeoutError('retrieval_deadline')
                    # Backend isolation bounds local CPU, connection setup and socket reads alike.
                    backend_deadline = min(deadline, time.time() + (
                        self.config.vector.timeout_seconds if backend == 'vector' else 35))
                    status, items, info = self._run_backend(request, backend, backend_deadline)
                    if time.time() >= backend_deadline:
                        raise TimeoutError('retrieval_deadline')
                    statuses[backend] = status
                    rankings[backend] = items
                    metadata.update(info)
                except Exception as exc:
                    statuses[backend] = "failed"
                    issues.append(Issue(code=backend + ("_timeout" if isinstance(exc, TimeoutError) else "_failed"),
                                        stage="retrieval", message=type(exc).__name__))
            result_items = fuse_evidence(rankings, request.top_k)
            metadata['fusion'] = 'rrf_k60_unique_statement_source_per_backend'
            successful = any(s in {"ok", "empty"} for s in statuses.values())
            degraded = any(s not in {"ok", "empty"} for s in statuses.values())
            status = ("degraded" if degraded else ("ok" if result_items else "empty")) if successful else "failed"
            result = EvidenceBundle(bundle_id=identity("bundle", [call_id, request.request_id]),
                                    request_id=request.request_id, status=status, backend_status=statuses,
                                    items=result_items, issues=issues, retrieval_metadata=metadata,
                                    uncovered_questions=[] if result_items else [request.question])
            # Only the parent owns budget settlement and persistence. Workers are read-only.
            self.store.save(self.run_id, "retrieval", result.bundle_id, result)
            self.store.settle(call_id, status, {"items": len(result_items), "backends": statuses})
            return result
        except Exception:
            self.store.settle(call_id, "failed")
            raise

    def _snapshot_payload(self):
        rows = self.store.objects(self.run_id, "snapshot")
        return rows[-1]["payload"] if rows else None

    def _run_backend(self, request, backend, deadline):
        import sys
        from .retrieval_worker import run_json_process
        payload = {'config': self.config.model_dump(mode='json'), 'release_id': self.release_id,
                   'request': request.model_dump(mode='json'), 'backend': backend, 'deadline': deadline,
                   'snapshot': self._snapshot_payload()}
        # The disposable subprocess cannot inherit ContextVars. Hold one ticket
        # for its vector backend; that backend makes at most one embedding call.
        admission = admit_model('retrieval_vector') if backend == 'vector' else nullcontext()
        with admission:
            response = run_json_process([sys.executable, '-m', 'neurogra.clinical.retrieval_worker'], payload, deadline)
        if response.get('error'):
            if response['error'] == 'TimeoutError':
                raise TimeoutError('backend_deadline')
            raise ValueError('backend_failed:' + response['error'])
        return response['status'], [EvidenceItem.model_validate(i) for i in response['items']], response['metadata']

    def _backend(self, request, backend, deadline):
        snapshot = getattr(self, "snapshot", None)
        if snapshot is None and hasattr(self, "store"):
            rows = self.store.objects(self.run_id, "snapshot")
            if rows:
                snapshot = CaseSnapshot.model_validate(rows[-1]["payload"])
        return retrieve_backend(self.config, self.release_id, request, backend,
                                deadline - time.time(), snapshot)


def read_case_record(snapshot: CaseSnapshot, record_id: str, case_version: int,
                     start: int = 0, end: int | None = None) -> dict:
    if snapshot.version != case_version:
        raise ValueError("stale_case_version")
    record = next((r for r in snapshot.records if r.record_id == record_id), None)
    if record is None:
        raise ValueError("record_not_found")
    end = len(record.text) if end is None else end
    if not 0 <= start <= end <= len(record.text):
        raise ValueError("invalid_record_range")
    return {"record_id": record_id, "case_version": case_version, "text": record.text[start:end],
            "start": start, "end": end, "locator": record.locator, "content_hash": record.content_hash}


def fuse_evidence(rankings, top_k):
    merged = {}
    for backend, items in rankings.items():
        seen = set()
        for item in items:
            key = (item.version, item.document_id, item.span_id, item.statement_id)
            if key in seen:
                merged[key].clause_ids = sorted(set(merged[key].clause_ids + item.clause_ids))
                continue
            seen.add(key)
            rank = len(seen)
            if key not in merged:
                merged[key] = item.model_copy(deep=True)
                merged[key].backend_ranks = {}
                merged[key].matched_by = []
                merged[key].fusion_score = 0.0
            old = merged[key]
            old.backend_ranks[backend] = rank
            old.matched_by = sorted(set(old.matched_by + [backend]))
            old.clause_ids = sorted(set(old.clause_ids + item.clause_ids))
            old.fusion_score += 1.0 / (60 + rank)
    gate = {"applicable": 0, "unknown": 1, "not_applicable": 2}
    return sorted(merged.values(), key=lambda i: (gate[i.applicability], -i.fusion_score, i.evidence_id))[:top_k]
