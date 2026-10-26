"""BM25 and persisted cosine vectors with explicit template/model binding."""

import json
import math
import re
from collections import Counter
from pathlib import Path

import numpy as np

from ..utils import atomic_json, checksum, identity


def tokens(text):
    words = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", text.casefold())
    output = []
    for word in words:
        if re.fullmatch(r"[\u3400-\u9fff]+", word):
            output.extend(word)
            output.extend(word[i:i + 2] for i in range(len(word) - 1))
        else:
            output.append(word)
    return output


def lexical_search(resources, query, top_k):
    corpus = [(row["id"], Counter(tokens(row["text"]))) for row in resources]
    if not corpus:
        return []
    terms = list(dict.fromkeys(tokens(query)))
    lengths = [sum(counts.values()) for _, counts in corpus]
    average = sum(lengths) / len(corpus) or 1
    df = {term: sum(term in counts for _, counts in corpus) for term in terms}
    ranked = []
    for (id, counts), length in zip(corpus, lengths):
        score = 0.0
        for term in terms:
            frequency = counts.get(term, 0)
            inverse = math.log(1 + (len(corpus) - df[term] + .5) / (df[term] + .5))
            score += inverse * frequency * 2.2 / (frequency + 1.2 * (.25 + .75 * length / average))
        if score > 0:
            ranked.append((id, score))
    return sorted(ranked, key=lambda item: (-item[1], item[0]))[:top_k]


class LocalVectorStore:
    def __init__(self, directory: Path, embedding, budget=None):
        self.directory, self.embedding, self.budget = directory, embedding, budget
        self.manifest = json.loads((directory / "vector_manifest.json").read_text(encoding="utf-8"))
        if checksum(directory / "vectors.npy") != self.manifest["checksum"]:
            raise ValueError("vector_artifact_checksum_mismatch")
        self.vectors = np.load(directory / "vectors.npy", allow_pickle=False)
        if self.vectors.shape != (len(self.manifest["resource_ids"]), self.manifest["dimension"]):
            raise ValueError("vector_artifact_shape_mismatch")
        if not np.isfinite(self.vectors).all():
            raise ValueError("vector_artifact_invalid_values")
        if np.any(np.linalg.norm(self.vectors, axis=1) <= 0):
            raise ValueError("vector_artifact_zero_row")

    def validate_binding(self, resources, release_id):
        if self.manifest["release_id"] != release_id:
            raise ValueError("vector_release_binding_mismatch")
        if self.manifest["resource_ids"] != [row["id"] for row in resources] or self.manifest["corpus_hash"] != identity("corpus", resources):
            raise ValueError("vector_corpus_binding_mismatch")

    @classmethod
    def build(cls, directory, resources, embedding, release_id, budget=None):
        if not resources:
            raise ValueError("empty_vector_resources")
        digest = embedding.model_digest()
        vectors = embedding.embed([row["text"] for row in resources], budget)
        matrix = np.asarray(vectors, dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[0] != len(resources):
            raise ValueError("vector_build_shape_mismatch")
        if not np.isfinite(matrix).all() or np.any(np.linalg.norm(matrix, axis=1) <= 0):
            raise ValueError("vector_build_invalid_values")
        if isinstance(embedding.config.dimension, int) and matrix.shape[1] != embedding.config.dimension:
            raise ValueError("vector_build_dimension_mismatch")
        matrix /= np.linalg.norm(matrix, axis=1)[:, None]
        directory.mkdir(parents=True, exist_ok=True)
        np.save(directory / "vectors.npy", matrix, allow_pickle=False)
        if embedding.model_digest() != digest:
            raise ValueError("embedding_model_changed_during_index_build")
        cfg = embedding.config
        manifest = {"release_id": release_id, "model": cfg.model, "model_digest": digest,
            "dimension": matrix.shape[1], "normalization": "l2", "metric": "cosine",
            "query_template_version": cfg.query_template_version,
            "document_template_version": cfg.document_template_version,
            "resource_ids": [row["id"] for row in resources], "corpus_hash": identity("corpus", resources),
            "checksum": checksum(directory / "vectors.npy")}
        manifest["resource_template_versions"] = sorted({row.get("text_template_version", "original_v1") for row in resources})
        manifest["terminology_bindings"] = sorted({row["terminology_binding"] for row in resources if row.get("terminology_binding")})
        atomic_json(directory / "vector_manifest.json", manifest)
        return manifest

    def query(self, text, top_k):
        cfg = self.embedding.config
        if isinstance(cfg.dimension, int) and cfg.dimension != self.manifest["dimension"]:
            raise ValueError("embedding_dimension_binding_mismatch")
        digest = self.embedding.model_digest()
        if cfg.model != self.manifest["model"] or digest != self.manifest["model_digest"]:
            raise ValueError("embedding_model_binding_mismatch")
        for key in ["query_template_version", "document_template_version"]:
            if getattr(cfg, key) != self.manifest[key]:
                raise ValueError("embedding_template_binding_mismatch")
        vector = self.embedding.embed([text], self.budget)[0]
        if len(vector) != self.manifest["dimension"]:
            raise ValueError("query_embedding_dimension_mismatch")
        return self.search(vector, top_k)

    def search(self, vector, top_k):
        query = np.asarray(vector, dtype=np.float32)
        if query.shape != (self.manifest["dimension"],) or not np.isfinite(query).all():
            raise ValueError("invalid_query_vector")
        norm = np.linalg.norm(query)
        if norm <= 0:
            raise ValueError("zero_query_vector")
        scores = self.vectors @ (query / norm)
        indices = sorted(range(len(scores)), key=lambda i: (-float(scores[i]), self.manifest["resource_ids"][i]))[:top_k]
        return [(self.manifest["resource_ids"][i], float(scores[i])) for i in indices]
