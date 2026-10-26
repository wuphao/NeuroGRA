"""Embedding adapter with dimensional, finite-value and model identity checks."""

import math
from pathlib import Path

from ..config import EmbeddingConfig
from ..runtime import MODEL_LOCK
from ..utils import atomic_json, identity
from .http import model_info, request_json


class OllamaEmbeddingAdapter:
    def __init__(self, config: EmbeddingConfig, output_root: Path, transport=None):
        self.config, self.output_root = config, Path(output_root)
        self.transport = transport or request_json
        self.dimension = None if config.dimension == "auto" else config.dimension

    def model_digest(self, timeout=10):
        if self.transport is request_json:
            return model_info(self.config.base_url, self.config.model, timeout)["digest"]
        rows = self.transport(self.config.base_url + "/api/tags", None, timeout)["models"]
        return next(row["digest"] for row in rows if row["name"] == self.config.model)

    def embed(self, texts, budget=None):
        texts = list(texts)
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("empty_embedding_input")
        digest = self.model_digest(min(10, budget.remaining()) if budget else 10)
        output = []
        for start in range(0, len(texts), self.config.batch_size):
            batch = texts[start:start + self.config.batch_size]
            key = identity("embed", [digest, self.config.model_dump(mode="json"), batch])
            path = self.output_root / "embedding_cache" / (key + ".json")
            if path.is_file():
                import json
                vectors = json.loads(path.read_text(encoding="utf-8"))["vectors"]
            else:
                if budget:
                    budget.reserve("embedding")
                with MODEL_LOCK:
                    timeout = min(self.config.timeout_seconds, budget.remaining()) if budget else self.config.timeout_seconds
                    response = self.transport(self.config.base_url.rstrip("/") + "/api/embed",
                        {"model": self.config.model, "input": batch, "truncate": False,
                         "keep_alive": self.config.keep_alive}, timeout)
                    vectors = response.get("embeddings", [])
                    if self.model_digest(min(10, budget.remaining()) if budget else 10) != digest:
                        raise ValueError("embedding_model_changed_during_call")
            if len(vectors) != len(batch):
                raise ValueError("embedding_row_count_mismatch")
            normalized = [self._normalize(vector) for vector in vectors]
            if not path.is_file():
                atomic_json(path, {"model_digest": digest, "dimension": self.dimension, "vectors": normalized})
            output.extend(normalized)
        return output

    def _normalize(self, vector):
        if not vector or any(isinstance(value, bool) or not isinstance(value, (int, float))
                             or not math.isfinite(value) for value in vector):
            raise ValueError("invalid_embedding_values")
        if self.dimension is None:
            self.dimension = len(vector)
        if len(vector) != self.dimension:
            raise ValueError("embedding_dimension_mismatch")
        norm = math.sqrt(sum(value * value for value in vector))
        if norm <= 0 or not math.isfinite(norm):
            raise ValueError("invalid_embedding_norm")
        return [float(value / norm) for value in vector]
