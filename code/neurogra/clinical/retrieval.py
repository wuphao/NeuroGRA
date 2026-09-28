"""Pinned, read-only knowledge retrieval and explicit backend degradation."""
import sqlite3
import time
import hashlib
from contextlib import contextmanager
from pathlib import Path
from neurogra.knowledge.config import load_config as load_knowledge_config
from neurogra.knowledge.retrieval.service import search
from neurogra.knowledge.storage.sqlite import Repository
from .config import ClinicalConfig
from .schemas import CaseSnapshot, EvidenceBundle, EvidenceItem, Issue, RetrievalRequest
from .utils import identity
from .storage import RunStore, BudgetExceeded, VersionConflict


@contextmanager
def readonly_repository(path: Path):
    if not path.is_file():
        raise ValueError("knowledge_database_missing")
    # Existing Repository methods are read-only here, and SQLite enforces it.
    repository = Repository.__new__(Repository)
    repository.db_path = path
    repository.connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    repository.connection.row_factory = sqlite3.Row
    try:
        yield repository
    finally:
        repository.close()


def knowledge_settings(config: ClinicalConfig):
    settings = load_knowledge_config(config.resolve(config.knowledge_config))
    db = config.resolve(settings.database.path)
    return settings, db


def pin_release(config: ClinicalConfig) -> str:
    _, db = knowledge_settings(config)
    with readonly_repository(db) as repository:
        release_id = config.knowledge_release_id or repository.active_release_id()
        if repository.release_status(release_id) not in {"active", "validated", "retired"}:
            raise ValueError("knowledge_release_not_validated")
        repository.get_release_manifest(release_id)
        return release_id


class KnowledgeService:
    def __init__(self, config: ClinicalConfig, release_id: str, store: RunStore, run_id: str):
        self.config, self.release_id, self.store, self.run_id = config, release_id, store, run_id
        if config.vector_enabled:
            rows = store.objects(run_id, 'vector_binding')
            if not rows:
                path = config.resolve(config.vector.index_manifest) if config.vector.index_manifest else None
                binding = {'manifest_sha256': hashlib.sha256(path.read_bytes()).hexdigest() if path and path.is_file() else None}
                try:
                    store.save(run_id, 'vector_binding', 'main', binding)
                except VersionConflict:
                    pass
                rows = store.objects(run_id, 'vector_binding')
            self.vector_binding = rows[0]['payload']['manifest_sha256']

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
            metadata['fusion'] = 'rrf_k60_unique_span_per_backend'
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

    def _run_backend(self, request, backend, deadline):
        import sys
        from .retrieval_worker import run_json_process
        payload = {'config': self.config.model_dump(mode='json'), 'release_id': self.release_id,
                   'request': request.model_dump(mode='json'), 'backend': backend, 'deadline': deadline,
                   'vector_binding': getattr(self, 'vector_binding', None)}
        response = run_json_process([sys.executable, '-m', 'neurogra.clinical.retrieval_worker'], payload, deadline)
        if response.get('error'):
            if response['error'] == 'TimeoutError':
                raise TimeoutError('backend_deadline')
            raise ValueError('backend_failed:' + response['error'])
        return response['status'], [EvidenceItem.model_validate(i) for i in response['items']], response['metadata']

    def _backend(self, request, backend, deadline):
        """Read-only implementation, called inside a disposable worker process."""
        metadata = {}
        if backend == 'vector':
            if not self.config.vector_enabled:
                return 'not_configured', [], {}
            if not self.config.vector.index_manifest or not self.config.resolve(self.config.vector.index_manifest).is_file():
                return 'not_indexed', [], {}
            if not self.vector_binding or hashlib.sha256(self.config.resolve(self.config.vector.index_manifest).read_bytes()).hexdigest() != self.vector_binding:
                raise ValueError('vector_binding_changed')
        settings, db = knowledge_settings(self.config)
        with readonly_repository(db) as repository:
            repository.connection.set_progress_handler(lambda: int(time.time() >= deadline), 1000)
            manifest = repository.get_release_manifest(self.release_id)
            if time.time() >= deadline:
                raise TimeoutError('retrieval_deadline')
            if backend == 'vector':
                from .vector import search_vector
                chunks, index = search_vector(self.config, repository, self.release_id, request.question, request.top_k, deadline)
                items = [self._span_item(repository, span_id, 'vector', chunk.linked_clause_ids, chunk.retrieval_text)
                         for chunk in chunks for span_id in chunk.span_ids]
                metadata['vector'] = {'index_id': index.index_id, 'model': index.model,
                    'model_digest': index.model_digest, 'dimension': index.dimension,
                    'text_view': index.text_view, 'corpus_hash': index.corpus_hash}
            elif backend == 'bm25':
                response = search(repository, request.question, request.top_k, self.release_id)
                items = [self._span_item(repository, citation.span_id, 'bm25', hit.clause_ids, hit.context_text)
                         for hit in response.hits for citation in hit.citations]
            else:
                if settings.graph_store.provider != 'neo4j':
                    return 'not_configured', [], {}
                items = self._graph(request, repository, manifest, settings, max(.001, deadline - time.time()))
            if time.time() >= deadline:
                raise TimeoutError('retrieval_deadline')
            items = [i for i in items if i is not None]
            return ('ok' if items else 'empty'), items, metadata

    def _span_item(self, repository, span_id, backend, clause_ids, context=None):
        span = repository.get_span(span_id)
        if span is None:
            return None
        document = repository.get_document(span.document_id)
        source = repository.span_source_contexts([span_id]).get(span_id, {})
        return EvidenceItem(evidence_id=identity("evidence", [self.release_id, span_id]),
            document_id=span.document_id, span_id=span_id, title=document.title, quote=span.original_text,
            page=source.get('page_no'), context=context, clause_ids=clause_ids,
            version=self.release_id, matched_by=[backend])

    def _graph(self, request, repository, manifest, settings, remaining):
        from neo4j import GraphDatabase, Query
        graph = settings.graph_store
        password = graph.resolve_password()
        terms = request.candidate_terms or [request.question]
        # Actual graph has no release nodes. Restrict both clauses and evidence via SQLite manifest.
        query = """
        MATCH (c:`知识断言`)-[:`有证据`]->(e:`证据`)
        WHERE c.claim_id IN $claim_ids
          AND any(term IN $terms WHERE c.claim_text CONTAINS term OR e.original_text CONTAINS term)
        RETURN DISTINCT c.claim_id AS claim_id, e.span_id AS span_id
        ORDER BY claim_id, span_id
        LIMIT $limit
        """
        output = []
        allowed_spans = set()
        from neurogra.knowledge.retrieval.service import _read_release_chunks
        for chunk in _read_release_chunks(manifest):
            if chunk.chunk_id in manifest.chunk_ids:
                allowed_spans.update(chunk.span_ids)
        with GraphDatabase.driver(graph.uri, auth=(graph.username, password),
                                  connection_timeout=min(10, remaining),
                                  connection_acquisition_timeout=min(10, remaining)) as driver:
            with driver.session(database=graph.database, default_access_mode="READ") as session:
                rows = list(session.run(Query(query, timeout=min(15, remaining)),
                                        claim_ids=manifest.clause_revision_ids, terms=terms[:10],
                                        limit=request.top_k * 4))
        for row in rows:
            if row["span_id"] not in allowed_spans:
                continue
            span = repository.get_span(row["span_id"])
            if span is None:
                continue
            document = repository.get_document(span.document_id)
            source = repository.span_source_contexts([span.span_id]).get(span.span_id, {})
            output.append(EvidenceItem(evidence_id=identity("evidence", [self.release_id, span.span_id]),
                                      document_id=span.document_id, span_id=span.span_id,
                                      clause_ids=[row["claim_id"]], title=document.title,
                                      quote=span.original_text, page=source.get("page_no"),
                                      version=self.release_id, matched_by=["graph"]))
        return output


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
            key = (item.version, item.document_id, item.span_id)
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
    return sorted(merged.values(), key=lambda i: (-i.fusion_score, i.evidence_id))[:top_k]
