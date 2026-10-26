"""Public SDK. Callers only exchange contracts, never infrastructure objects."""

import json
import time
from pathlib import Path

from .adapters.graph import MemoryGraphStore, Neo4jGraphStore
from .adapters.ollama_embedding import OllamaEmbeddingAdapter
from .adapters.ollama_llm import OllamaLLMAdapter
from .config import KGConfig, load_config
from .construction.parsing import DocumentParser
from .construction.pipeline import ConstructionPipeline
from .contracts.domain import BuildRequest, BuildResult, EvidenceItem, ParsedDocument, RetrievalRequest, RetrievalResult
from .runtime import Budget, implementation_binding
from .storage.registry import Registry
from .storage.releases import create_release
from .retrieval.pipeline import retrieve
from .utils import atomic_json, checksum, identity, safe_identifier


class KnowledgeGraphService:
    def __init__(self, config: KGConfig | str | Path | None = None, *, llm=None, embedding=None, graph=None, read_only=False):
        self.config = config if isinstance(config, KGConfig) else load_config(config)
        self.data_root = self.config.resolve(self.config.paths.data_root)
        self.output_root = self.config.resolve(self.config.paths.output_root)
        self.llm = llm or OllamaLLMAdapter(self.config.llm, self.output_root)
        self.embedding = embedding or OllamaEmbeddingAdapter(self.config.embedding, self.output_root)
        graph_config = self.config.graph.model_copy(deep=True)
        if graph_config.password_file:
            graph_config.password_file = self.config.resolve(graph_config.password_file)
        self.graph = graph or (MemoryGraphStore() if self.config.graph.provider == "memory"
                               else Neo4jGraphStore(graph_config))
        self.read_only = read_only
        self.registry = Registry(self.data_root / "registry.sqlite", read_only=read_only)
        self.parser = DocumentParser(self.data_root)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.registry.close()

    def _check_runtime(self):
        if not isinstance(self.llm, OllamaLLMAdapter):
            return  # Explicit injected ports are used by engineering fixtures.
        path = self.output_root / "runtime_profile.json"
        if not path.is_file():
            raise ValueError("run_kg_probe_before_build")
        profile = json.loads(path.read_text(encoding="utf-8"))
        if not profile.get("ready"):
            raise ValueError("kg_runtime_profile_not_ready")
        for key, adapter in [("llm", self.llm), ("embedding", self.embedding)]:
            if profile["checks"][key]["model"] != adapter.config.model or profile["checks"][key]["digest"] != adapter.model_digest():
                raise ValueError("kg_runtime_profile_model_changed:" + key)

    def build(self, request: BuildRequest):
        if self.read_only:
            raise ValueError("read_only_service")
        self._check_runtime()
        request = request.model_copy(deep=True)
        for document in request.documents:
            document.path = self.config.resolve(document.path)
        binding = identity("input", [{"checksum": checksum(doc.path), "version": doc.version,
                                      "title": doc.title, "language": doc.language} for doc in request.documents])
        runtime_binding = identity("build_runtime", ["construction_pipeline_v3",
            implementation_binding(["construction", "adapters"]),
            self.config.construction.model_dump(mode="json"),
            self.llm.binding() if hasattr(self.llm, "binding") else self.llm.model_digest(),
            self.embedding.config.model_dump(mode="json"), self.embedding.model_digest()])
        build_id = request.build_id or identity("build", [binding, request.corpus_version,
            runtime_binding])
        directory = self.output_root / "builds" / safe_identifier(build_id)
        directory.mkdir(parents=True, exist_ok=True)
        input_path = directory / "request.json"
        if input_path.exists():
            previous = json.loads(input_path.read_text(encoding="utf-8"))
            if previous["binding"] != binding or previous["request"]["corpus_version"] != request.corpus_version:
                raise ValueError("build_resume_input_changed")
            if previous.get("runtime_binding") != runtime_binding:
                raise ValueError("build_resume_runtime_changed")
        atomic_json(input_path, {"binding": binding, "runtime_binding": runtime_binding,
                                "request": request.model_dump(mode="json")})
        result = BuildResult(build_id=build_id, status="running")
        self.registry.save("build", build_id, result)
        budget = Budget(request.max_seconds, max_llm_calls=request.max_llm_calls,
                        max_embedding_calls=request.max_embedding_calls)
        statements, parsed_documents = [], []
        try:
            # Entity embeddings are deferred: exact/local entities first, then all semantic
            # candidate work after generation to avoid repeated local model switching.
            pipeline = ConstructionPipeline(self.config.construction, self.llm)
            for input in request.documents:
                budget.remaining()
                parsed = self.parser.parse(input)
                from .construction.chunking import split_document_units
                parsed = split_document_units(parsed, self.config.construction.unit_max_chars)
                blocking_parse_issues = [issue for issue in parsed.issues if any(marker in issue for marker in
                    ["ocr_required_unavailable", "ocr_eng_used_language_review_required", "no_parseable_blocks", "table_detection_failed",
                     "docx_nested_table_requires_review", "docx_tracked_changes_requires_review", "docx_unparsed_object_requires_review"])]
                result.failed_units += len(blocking_parse_issues)
                parsed_documents.append(parsed)
                result.current_document_id = parsed.document.document_id
                atomic_json(directory / (parsed.document.document_id + ".parsed.json"), parsed.model_dump(mode="json"))
                completed_before, failed_before = result.completed_units, result.failed_units
                def progress(event):
                    result.current_unit_id = event["unit_id"]
                    result.current_document_units = event["total_units"]
                    result.progress_event = event["event"]
                    result.completed_units = completed_before + event["completed_units"]
                    result.failed_units = failed_before + event["failed_units"]
                    self.registry.save("build", build_id, result)
                    atomic_json(directory / "result.json", result.model_dump(mode="json"))
                new_statements, issues = pipeline.run(parsed, directory / parsed.document.document_id, budget, progress)
                statements.extend(new_statements)
                result.issues.extend(issues)
                result.completed_units = completed_before + pipeline.completed_units
                result.failed_units = failed_before + pipeline.failed_units
                self.registry.save("build", build_id, result)
            # All generator work has finished; resolve semantic candidates in one phase.
            from .construction.terminology import EntityResolver
            semantic_resolver = EntityResolver(embedding=self.embedding)
            for statement in statements:
                for field in ["subject", "object"]:
                    entity = getattr(statement, field)
                    if entity.status != "resolved":
                        setattr(statement, field, semantic_resolver.resolve(entity.name, entity.entity_type, budget))
            # Dedup identical persistent IDs; preserve distinct conditional assertions.
            statements = list({statement.statement_id: statement for statement in statements}.values())
            result.statement_ids = [statement.statement_id for statement in statements]
            result.pending_ids = [statement.statement_id for statement in statements if statement.status in {"draft", "source_checked"}]
            result.rejected_ids = [statement.statement_id for statement in statements if statement.status == "rejected"]
            result.status = ("partial" if result.failed_units else "completed") if statements else "failed"
            atomic_json(directory / "statements.json", [statement.model_dump(mode="json") for statement in statements])
            atomic_json(directory / "documents.json", [document.model_dump(mode="json") for document in parsed_documents])
        except (TimeoutError, KeyboardInterrupt):
            result.status = "interrupted"
            raise
        except Exception:
            result.status = "failed"
            raise
        finally:
            self.registry.save("build", build_id, result)
            atomic_json(directory / "result.json", result.model_dump(mode="json"))
        return result

    def inspect_build(self, build_id):
        return BuildResult.model_validate(self.registry.get("build", build_id))

    def publish(self, build_id):
        if self.read_only:
            raise ValueError("read_only_service")
        result = self.inspect_build(build_id)
        if result.status != "completed":
            raise ValueError("build_not_completed")
        directory = self.output_root / "builds" / safe_identifier(build_id)
        from .contracts.domain import KnowledgeStatement
        statements = [KnowledgeStatement.model_validate(row) for row in json.loads((directory / "statements.json").read_text(encoding="utf-8"))]
        parsed = [ParsedDocument.model_validate(row) for row in json.loads((directory / "documents.json").read_text(encoding="utf-8"))]
        return create_release(self.data_root, self.registry, build_id, statements, parsed,
                              self.embedding, self.graph, Budget(3600, max_embedding_calls=1000))

    def publish_builds(self, build_ids, *, max_seconds=3600, max_embedding_calls=10000):
        """Publish a single corpus from completed per-document builds, without regenerating statements."""
        if self.read_only:
            raise ValueError('read_only_service')
        ids = sorted(set(build_ids))
        if not ids:
            raise ValueError('no_completed_builds')
        from .contracts.domain import KnowledgeStatement
        statements, documents = {}, {}
        for build_id in ids:
            if self.inspect_build(build_id).status != 'completed':
                raise ValueError('build_not_completed:' + build_id)
            directory = self.output_root / 'builds' / safe_identifier(build_id)
            for row in json.loads((directory / 'statements.json').read_text(encoding='utf-8')):
                item = KnowledgeStatement.model_validate(row)
                if item.statement_id in statements and statements[item.statement_id] != item:
                    raise ValueError('conflicting_statement_identity')
                statements[item.statement_id] = item
            for row in json.loads((directory / 'documents.json').read_text(encoding='utf-8')):
                item = ParsedDocument.model_validate(row)
                if item.document.document_id in documents and documents[item.document.document_id] != item:
                    raise ValueError('conflicting_document_identity')
                documents[item.document.document_id] = item
        build_id = identity('build_collection', ids)
        directory = self.output_root / 'builds' / build_id
        directory.mkdir(parents=True, exist_ok=True)
        atomic_json(directory / 'members.json', {'build_ids': ids})
        atomic_json(directory / 'statements.json', [s.model_dump(mode='json') for s in statements.values()])
        atomic_json(directory / 'documents.json', [d.model_dump(mode='json') for d in documents.values()])
        result = BuildResult(build_id=build_id, status='completed', statement_ids=list(statements))
        self.registry.save('build', build_id, result)
        atomic_json(directory / 'result.json', result.model_dump(mode='json'))
        return create_release(self.data_root, self.registry, build_id, list(statements.values()),
            list(documents.values()), self.embedding, self.graph,
            Budget(max_seconds, max_embedding_calls=max_embedding_calls))

    def retrieve(self, request: RetrievalRequest):
        deadline = time.time() + request.max_seconds
        manifest = self.get_release(request.release_id)
        if self.read_only:
            return self._query_work(manifest, request, Budget(deadline - time.time(),
                                    max_llm_calls=request.max_llm_calls))
        from .storage.query_journal import QueryJournal
        request = request.model_copy(deep=True, update={"release_id": manifest["release_id"]})
        runtime_warnings = []
        def runtime_timeout():
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError("query_deadline_exceeded")
            return min(10, remaining)
        def available_binding(name, resolve):
            try:
                return resolve()
            except (OSError, TimeoutError, RuntimeError, ValueError) as exc:
                if isinstance(exc, ValueError) and not str(exc).startswith("configured_model_not_installed:"):
                    raise
                runtime_warnings.append(name + "_unavailable:" + type(exc).__name__)
                return {"availability": "unavailable", "error_type": type(exc).__name__}
        llm_binding = available_binding("llm", lambda: self.llm.binding(timeout=runtime_timeout())
            if isinstance(self.llm, OllamaLLMAdapter) else self.llm.binding()
            if hasattr(self.llm, "binding") else type(self.llm).__name__)
        embedding_binding = available_binding("embedding", lambda: self.embedding.model_digest(timeout=runtime_timeout())
            if isinstance(self.embedding, OllamaEmbeddingAdapter) else self.embedding.model_digest())
        # Run IDs identify journals; they are not part of the reproducible input.
        input_binding = request.model_dump(mode="json")
        input_binding["run_id"] = None
        completion_binding = None
        if self.config.completion.enabled:
            directory = self.data_root / "complex" / manifest["release_id"]
            completion_binding = {name: checksum(directory / name) if (directory / name).is_file() else None
                for name in ["manifest.json", "embeddings.npz"]}
        binding = identity("query_binding", [input_binding, manifest,
            "retrieval_pipeline_v3", implementation_binding(["construction", "retrieval", "completion", "storage", "adapters"]), completion_binding,
            self.config.construction.model_dump(mode="json"), self.config.retrieval.model_dump(mode="json"),
            self.config.completion.model_dump(mode="json"),
            llm_binding, self.llm.prompt_binding() if hasattr(self.llm, "prompt_binding") else None,
            self.embedding.config.model_dump(mode="json"), embedding_binding])
        run_id = request.run_id or identity("query", binding)
        request.run_id = safe_identifier(run_id)
        with QueryJournal(self.output_root / "query_runs", run_id) as journal:
            if journal.data:
                if journal.data["binding"] != binding:
                    raise ValueError("query_resume_input_or_runtime_changed")
                if journal.data["status"] == "completed":
                    return RetrievalResult.model_validate(journal.data["result"])
            else:
                journal.save(binding=binding, request=request.model_dump(mode="json"),
                    status="running", phase="recall", deadline=deadline,
                    llm_calls=0, embedding_calls=0)
            used_llm, used_embedding = journal.data["llm_calls"], journal.data["embedding_calls"]
            def reserve(budget):
                journal.save(llm_calls=used_llm + budget.llm_calls,
                             embedding_calls=used_embedding + budget.embedding_calls)
            budget = Budget(journal.data["deadline"] - time.time(),
                max_llm_calls=max(0, request.max_llm_calls - used_llm),
                max_embedding_calls=max(0, 100 - used_embedding), on_reserve=reserve)
            journal.save(status="running")
            try:
                budget.remaining()
                result = self._query_work(manifest, request, budget, journal)
                if runtime_warnings:
                    result.degraded = True
                    result.warnings = list(dict.fromkeys([*result.warnings, *runtime_warnings]))
                self.registry.save("query", result.run_id, result)
                for item in [*result.applicable, *result.unknown, *result.excluded]:
                    self.registry.save("evidence", item.evidence_id, item)
                atomic_json(self.output_root / "queries" / (result.run_id + ".json"), result.model_dump(mode="json"))
                journal.save(status="completed", result=result.model_dump(mode="json"))
                return result
            except (KeyboardInterrupt, TimeoutError):
                journal.save(status="interrupted")
                raise
            except Exception as exc:
                journal.save(status="failed", error_type=type(exc).__name__)
                raise

    def _query_work(self, manifest, request, budget, journal=None):
        if journal and journal.data.get("phase") == "supplement" and journal.data.get("result"):
            result = RetrievalResult.model_validate(journal.data["result"])
        else:
            result = retrieve(manifest, request, self.config.retrieval, self.embedding, self.graph, self.llm, budget)
            if journal:
                journal.save(phase="supplement", result=result.model_dump(mode="json"))
        if request.max_supplement_rounds:
            from .storage.releases import load_release
            from .retrieval.supplementation import supplement
            statements, blocks, resources, data = load_release(manifest)
            candidates = []
            if self.config.completion.enabled and any(gap.kind == "graph_connection" for gap in result.gaps):
                try:
                    from .completion.complex import ComplExModel
                    from .completion.candidates import predict_candidates
                    model_directory = self.data_root / "complex" / result.release_id
                    model = ComplExModel.load(model_directory, result.release_id)
                    candidates = predict_candidates(model, statements, request.question,
                                                    self.config.completion.max_candidate_edges)
                    result.supplementation_trace.append({"status": "complex_candidates", "candidates": candidates})
                except Exception as exc:
                    result.warnings.append("complex_unavailable:" + type(exc).__name__)
                    result.degraded = True
            def checkpoint(current, state):
                if journal:
                    journal.save(phase="supplement", result=current.model_dump(mode="json"), supplement_state=state)
            result = supplement(result, request, self.config, data, blocks, resources,
                                self.llm, self.output_root, budget, candidates=candidates,
                                resume_state=journal.data.get("supplement_state") if journal else None,
                                on_checkpoint=checkpoint if journal else None)
        return result

    def inspect_query(self, run_id):
        from .storage.query_journal import QueryJournal
        path = self.output_root / "query_runs" / safe_identifier(run_id) / "checkpoint.json"
        return QueryJournal.read(path)

    def resume_query(self, run_id):
        if self.read_only:
            raise ValueError("read_only_service")
        return self.retrieve(RetrievalRequest.model_validate(self.inspect_query(run_id)["request"]))

    def get_evidence(self, evidence_id):
        return EvidenceItem.model_validate(self.registry.get("evidence", evidence_id))

    def extract_patient_facts(self, records, reference_time, max_seconds=120):
        from .retrieval.facts import extract_patient_facts
        return extract_patient_facts(records, reference_time, self.llm, Budget(max_seconds))

    def train_complex(self, release_id=None):
        if self.read_only:
            raise ValueError("read_only_service")
        from .storage.releases import load_release
        from .completion.complex import ComplExModel
        manifest = self.registry.release(release_id)
        statements, _, _, _ = load_release(manifest)
        model = ComplExModel.train(statements, manifest["release_id"], self.config.completion)
        return model.save(self.data_root / "complex" / manifest["release_id"])

    def get_release(self, release_id=None):
        from .storage.releases import load_release
        manifest = self.registry.release(release_id)
        load_release(manifest)
        return manifest

    def get_source(self, block_id, release_id=None):
        from .storage.releases import load_release
        manifest = self.registry.release(release_id)
        _, blocks, _, data = load_release(manifest)
        if block_id not in blocks:
            raise KeyError("source:" + block_id)
        block = blocks[block_id]
        document = next(row for row in data["documents"] if row["document_id"] == block.document_id)
        return {"block": block.model_dump(mode="json"), "document": document, "release_id": manifest["release_id"]}

    def list_source_ids(self, release_id=None):
        from .storage.releases import load_release
        return sorted(load_release(self.registry.release(release_id))[1])

    def inspect_vector_index(self, release_id=None):
        """Validate the published vectors and their current model/template binding."""
        from .storage.indexes import LocalVectorStore
        release = self.get_release(release_id)
        index = LocalVectorStore(Path(release["directory"]), self.embedding)
        from .storage.releases import load_release
        index.validate_binding(load_release(release)[2], release["release_id"])
        manifest = index.manifest
        if manifest["release_id"] != release["release_id"]:
            raise ValueError("vector_release_binding_mismatch")
        cfg = self.embedding.config
        if isinstance(cfg.dimension, int) and cfg.dimension != manifest["dimension"]:
            raise ValueError("embedding_dimension_binding_mismatch")
        if cfg.model != manifest["model"] or self.embedding.model_digest() != manifest["model_digest"]:
            raise ValueError("embedding_model_binding_mismatch")
        for field in ["query_template_version", "document_template_version"]:
            if getattr(cfg, field) != manifest[field]:
                raise ValueError("embedding_template_binding_mismatch")
        return dict(manifest)

    def probe_graph(self):
        if isinstance(self.graph, MemoryGraphStore):
            return {"provider": "memory", "available": True, "production": False}
        return {"provider": "neo4j", "available": bool(self.graph.probe()), "production": True}
