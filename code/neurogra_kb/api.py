"""Immutable independent text releases and source-verified hybrid retrieval.

Reuses pure parsing/index primitives from the KG package. No graph driver,
construction pipeline, statements, or KG release registry is required.
"""

import json
import os
from pathlib import Path

from neurogra_common import new_id, stable_id, validate_references
from neurogra_kg.adapters.ollama_embedding import OllamaEmbeddingAdapter
from neurogra_kg.construction.parsing import DocumentParser
from neurogra_kg.contracts.domain import DocumentInput, ParsedDocument
from neurogra_kg.storage.indexes import LocalVectorStore, lexical_search
from neurogra_kg.utils import atomic_json, checksum, safe_identifier
from .config import KBConfig


class TextKnowledgeService:
    def __init__(self, config=None, embedding=None):
        self.config = config or KBConfig()
        if self.config.overlap_chars >= self.config.chunk_chars:
            raise ValueError("chunk_overlap_must_be_smaller_than_chunk")
        self.directory = self.config.resolve(self.config.output_root)
        self.embedding = embedding or OllamaEmbeddingAdapter(self.config.embedding, self.directory)

    def _directory(self, release_id):
        return self.directory / "releases" / safe_identifier(release_id)

    def active_release(self):
        path = self.directory / "active.json"
        if not path.is_file():
            return None
        release_id = json.loads(path.read_text(encoding="utf-8"))["release_id"]
        self.get_release(release_id)
        return release_id

    def publish(self, documents, *, budget=None, vector=True, activate=True):
        """Only code creates IDs; publication doesn't run an LLM or build a graph."""
        parser = DocumentParser(self.config.resolve(self.config.data_root))
        parsed = [parser.parse(row if isinstance(row, DocumentInput) else
                               DocumentInput(path=Path(row))) for row in documents]
        unique = {doc.document.document_id: doc for doc in parsed}
        parsed = [unique[key] for key in sorted(unique)]
        resources = []
        for doc in parsed:
            for block in doc.blocks:
                if not block.text.strip() or set(block.issues) & {
                    "container_only", "running_header", "page_number", "bibliography"}:
                    continue
                step = self.config.chunk_chars - self.config.overlap_chars
                for start in range(0, len(block.text), step):
                    end = min(start + self.config.chunk_chars, len(block.text))
                    text = block.text[start:end]
                    row = {"id": stable_id("snippet", [block.document_id, block.block_id, start, end, text]),
                           "text": text, "document_id": block.document_id,
                           "block_id": block.block_id, "start": start, "end": end,
                           "title": doc.document.title,
                           "page": block.page if block.page_kind != "unavailable" else None,
                           "page_kind": block.page_kind, "source_locator": block.source_locator,
                           "section_path": block.section_path}
                    resources.append(row)
                    if end == len(block.text):
                        break
        if not resources:
            raise ValueError("no_parseable_text_resources")
        binding = {"config": self.config.embedding.model_dump(mode="json"),
                   "digest": self.embedding.model_digest()} if vector else None
        release_id = stable_id("kb_release", [resources, binding])
        destination = self._directory(release_id)
        if not destination.exists():
            staging = self.directory / "staging" / new_id("publication")
            staging.mkdir(parents=True, exist_ok=False)
            atomic_json(staging / "documents.json", [doc.model_dump(mode="json") for doc in parsed])
            atomic_json(staging / "resources.json", resources)
            if vector:
                LocalVectorStore.build(staging, resources, self.embedding, release_id, budget)
            files = ["documents.json", "resources.json"]
            if vector:
                files += ["vectors.npy", "vector_manifest.json"]
            atomic_json(staging / "manifest.json", {
                "release_id": release_id, "version": 1, "vector": vector,
                "embedding_binding": binding, "resource_count": len(resources),
                "document_count": len(parsed), "files": {name: checksum(staging / name) for name in files}})
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.rename(staging, destination)
            except OSError:
                if not destination.is_dir():
                    raise
                # A concurrent identical publication won. Validate it before activation.
                self.get_release(release_id)
        manifest = self.get_release(release_id)
        if activate:
            atomic_json(self.directory / "active.json", {"release_id": release_id})
        return manifest

    def get_release(self, release_id):
        directory = self._directory(release_id)
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if manifest["release_id"] != release_id:
            raise ValueError("kb_release_binding_mismatch")
        expected = {"documents.json", "resources.json"}
        if manifest["vector"]:
            expected |= {"vectors.npy", "vector_manifest.json"}
        if set(manifest["files"]) != expected:
            raise ValueError("kb_manifest_file_set_mismatch")
        for name in expected:
            if checksum(directory / name) != manifest["files"][name]:
                raise ValueError("kb_artifact_checksum_mismatch")
        resources = json.loads((directory / "resources.json").read_text(encoding="utf-8"))
        docs = [ParsedDocument.model_validate(row) for row in
                json.loads((directory / "documents.json").read_text(encoding="utf-8"))]
        if len(resources) != manifest["resource_count"] or len(docs) != manifest["document_count"]:
            raise ValueError("kb_resource_count_mismatch")
        for doc in docs:
            snapshot = Path(doc.document.snapshot_path)
            data_root = self.config.resolve(self.config.data_root)
            if not snapshot.resolve().is_relative_to(data_root):
                raise ValueError("kb_source_outside_data_root")
            if checksum(snapshot) != doc.document.checksum:
                raise ValueError("kb_source_snapshot_checksum_mismatch")
        blocks = {block.block_id: block for doc in docs for block in doc.blocks}
        seen = set()
        for row in resources:
            block = blocks.get(row["block_id"])
            if block is None or block.document_id != row["document_id"]:
                raise ValueError("kb_source_binding_mismatch")
            if row["id"] in seen or block.text[row["start"]:row["end"]] != row["text"]:
                raise ValueError("kb_source_quote_mismatch")
            seen.add(row["id"])
            if row["id"] != stable_id("snippet", [row["document_id"], row["block_id"],
                                                 row["start"], row["end"], row["text"]]):
                raise ValueError("kb_snippet_identity_mismatch")
        if release_id != stable_id("kb_release", [resources, manifest["embedding_binding"]]):
            raise ValueError("kb_release_identity_mismatch")
        return manifest

    def _resources(self, release_id):
        manifest = self.get_release(release_id)
        rows = json.loads((self._directory(release_id) / "resources.json").read_text(encoding="utf-8"))
        return manifest, rows

    def search_text(self, query, *, release_id=None, document_ids=None, top_k=5,
                    budget=None, use_vector=True):
        if not query.strip() or not 1 <= top_k <= 100:
            raise ValueError("invalid_kb_query")
        release_id = release_id or self.active_release()
        if release_id is None:
            return {"release_id": None, "items": [], "backend_status": {"lexical": "not_published"}}
        manifest, resources = self._resources(release_id)
        allowed_docs = {row["document_id"] for row in resources}
        if document_ids is not None:
            validate_references(document_ids, allowed_docs)
            allowed_docs = set(document_ids)
        scoped = [row for row in resources if row["document_id"] in allowed_docs]
        rankings = {"lexical": lexical_search(scoped, query, top_k * 3)}
        status = {"lexical": "ok"}
        if use_vector and manifest["vector"]:
            try:
                store = LocalVectorStore(self._directory(release_id), self.embedding, budget)
                store.validate_binding(resources, release_id)
                allowed = {row["id"] for row in scoped}
                rankings["vector"] = [(key, score) for key, score in store.query(query, len(resources))
                                      if key in allowed][:top_k * 3]
                status["vector"] = "ok"
            except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                status["vector"] = "unavailable:" + type(exc).__name__
        else:
            status["vector"] = "disabled" if not use_vector else "not_published"
        scores, matched = {}, {}
        for backend, ranking in rankings.items():
            for rank, (key, _) in enumerate(ranking, 1):
                scores[key] = scores.get(key, 0) + 1 / (60 + rank)
                matched.setdefault(key, []).append(backend)
        by_id = {row["id"]: row for row in resources}
        items = [{**by_id[key], "evidence_id": stable_id("kb_evidence", [release_id, key]),
                  "release_id": release_id, "matched_by": matched[key], "score": scores[key],
                  "allowed_use": "background", "applicability": "unknown"}
                 for key in sorted(scores, key=lambda key: (-scores[key], key))[:top_k]]
        return {"release_id": release_id, "items": items, "backend_status": status}

    def expand_source(self, snippet_id, *, release_id, window=1):
        if not 0 <= window <= 5:
            raise ValueError("invalid_context_window")
        _, resources = self._resources(release_id)
        by_id = {row["id"]: row for row in resources}
        validate_references([snippet_id], by_id)
        source = by_id[snippet_id]
        rows = [row for row in resources if row["document_id"] == source["document_id"]]
        index = next(i for i, row in enumerate(rows) if row["id"] == snippet_id)
        return {"release_id": release_id, "source": source,
                "context": rows[max(0, index - window):index + window + 1]}
