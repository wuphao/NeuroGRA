"""Immutable local cosine index, bound to a published corpus and an Ollama model digest."""
from __future__ import annotations
import hashlib
import json
import math
import time
import urllib.request
from pathlib import Path
from typing import Literal
from pydantic import Field
from neurogra.knowledge.schemas.common import SchemaModel


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def request_json(url, payload=None, timeout=60):
    data = None if payload is None else canonical(payload).encode('utf-8')
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}), timeout=timeout) as response:
        return json.load(response)


class VectorIndexManifest(SchemaModel):
    index_id: str
    release_id: str
    model: str
    model_digest: str
    dimension: int = Field(ge=1)
    normalization: Literal['l2'] = 'l2'
    metric: Literal['cosine'] = 'cosine'
    text_view: Literal['retrieval_text_prefix_v1'] = 'retrieval_text_prefix_v1'
    max_chars: int = Field(ge=1)
    corpus_hash: str
    chunk_ids: list[str]
    vectors_file: Literal['vectors.json'] = 'vectors.json'
    vectors_sha256: str


class EmbeddingClient:
    def __init__(self, base_url, model, dimension, timeout=60, transport=None, deadline=None):
        self.base_url, self.model, self.dimension = base_url.rstrip('/'), model, dimension
        self.timeout, self.transport, self.deadline = timeout, transport or request_json, deadline

    def call(self, path, payload=None):
        timeout = min(self.timeout, self.deadline - time.time()) if self.deadline else self.timeout
        if timeout <= 0:
            raise TimeoutError('embedding_deadline')
        return self.transport(self.base_url + path, payload, timeout)

    def model_digest(self):
        models = self.call('/api/tags')['models']
        match = next((m for m in models if m['name'] == self.model), None)
        if match is None or not match.get('digest'):
            raise ValueError('embedding_model_unavailable')
        return match['digest']

    def embed(self, texts):
        response = self.call('/api/embed', {'model': self.model, 'input': texts, 'truncate': False})
        vectors = response.get('embeddings', [])
        if len(vectors) != len(texts):
            raise ValueError('embedding_row_count_mismatch')
        return [normalize(vector, self.dimension) for vector in vectors]


def normalize(vector, dimension):
    if len(vector) != dimension or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vector):
        raise ValueError('embedding_dimension_or_value_mismatch')
    length = math.sqrt(sum(v * v for v in vector))
    if not math.isfinite(length) or length <= 0:
        raise ValueError('embedding_zero_or_invalid_norm')
    return [float(v / length) for v in vector]


def selected_chunks(chunks):
    return sorted((c for c in chunks if c.role == 'child'), key=lambda c: c.chunk_id)


def corpus_hash(chunks):
    return digest([{'chunk_id': c.chunk_id, 'document_id': c.document_id, 'span_ids': c.span_ids,
                    'retrieval_text': c.retrieval_text, 'original_text': c.original_text,
                    'clause_ids': c.linked_clause_ids} for c in selected_chunks(chunks)])


def build_vector_index(release_id, chunks, root, client, max_chars=1800, batch_size=4):
    chunks = selected_chunks(chunks)
    if not chunks or any(not c.retrieval_text.strip() for c in chunks):
        raise ValueError('empty_vector_corpus')
    before = client.model_digest()
    rows = []
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start:start + batch_size]
        vectors = client.embed([c.retrieval_text[:max_chars] for c in batch])
        rows.extend({'chunk_id': c.chunk_id, 'vector': v} for c, v in zip(batch, vectors))
    if client.model_digest() != before:
        raise ValueError('embedding_model_changed_during_build')
    vector_bytes = canonical(rows).encode('utf-8')
    checksum = hashlib.sha256(vector_bytes).hexdigest()
    corpus = corpus_hash(chunks)
    index_id = 'vector_' + digest([release_id, client.model, before, client.dimension, 'l2',
                                  'retrieval_text_prefix_v1', max_chars, corpus, checksum])[:32]
    manifest = VectorIndexManifest(index_id=index_id, release_id=release_id, model=client.model,
        model_digest=before, dimension=client.dimension, max_chars=max_chars, corpus_hash=corpus,
        chunk_ids=[c.chunk_id for c in chunks], vectors_sha256=checksum)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    directory = root / index_id
    if directory.exists():
        # Do not overwrite a published index; verify existing bytes before reuse.
        existing = VectorRetriever(directory / 'manifest.json', chunks, release_id, client, max_chars)
        return directory / 'manifest.json', existing.manifest
    import tempfile
    temporary = Path(tempfile.mkdtemp(prefix='.building-', dir=root))
    try:
        (temporary / 'vectors.json').write_bytes(vector_bytes)
        (temporary / 'manifest.json').write_text(manifest.model_dump_json(indent=2), encoding='utf-8')
        try:
            temporary.rename(directory)
        except FileExistsError:
            VectorRetriever(directory / 'manifest.json', chunks, release_id, client, max_chars)
    finally:
        if temporary.exists():
            # Only delete files we just created under this unique staging directory.
            for name in ('vectors.json', 'manifest.json'):
                (temporary / name).unlink(missing_ok=True)
            temporary.rmdir()
    return directory / 'manifest.json', manifest


class VectorRetriever:
    def __init__(self, manifest_path, chunks, release_id, client, max_chars):
        self.client = client
        self.path = Path(manifest_path)
        self.manifest = VectorIndexManifest.model_validate_json(self.path.read_text(encoding='utf-8'))
        m = self.manifest
        if (m.release_id != release_id or m.model != client.model or m.dimension != client.dimension
                or m.max_chars != max_chars or m.corpus_hash != corpus_hash(chunks)
                or m.chunk_ids != [c.chunk_id for c in selected_chunks(chunks)]):
            raise ValueError('vector_manifest_mismatch')
        raw = (self.path.parent / m.vectors_file).read_bytes()
        if hashlib.sha256(raw).hexdigest() != m.vectors_sha256:
            raise ValueError('vector_checksum_mismatch')
        self.rows = json.loads(raw)
        if [r['chunk_id'] for r in self.rows] != m.chunk_ids:
            raise ValueError('vector_membership_mismatch')
        for row in self.rows:
            normed = normalize(row['vector'], m.dimension)
            if any(abs(a - b) > 1e-5 for a, b in zip(row['vector'], normed)):
                raise ValueError('vector_not_normalized')

    def search(self, query, top_k):
        if self.client.model_digest() != self.manifest.model_digest:
            raise ValueError('embedding_model_digest_mismatch')
        vector = self.client.embed([query[:self.manifest.max_chars]])[0]
        if self.client.model_digest() != self.manifest.model_digest:
            raise ValueError('embedding_model_changed_during_query')
        scores = [(r['chunk_id'], sum(a * b for a, b in zip(vector, r['vector']))) for r in self.rows]
        return sorted(scores, key=lambda pair: (-pair[1], pair[0]))[:top_k]
