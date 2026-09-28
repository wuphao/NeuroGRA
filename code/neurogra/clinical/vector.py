"""Clinical adapter to offline knowledge vector indexing; no automatic build during analysis."""
from neurogra.knowledge.indexing.vector import EmbeddingClient, VectorRetriever, build_vector_index
from neurogra.knowledge.retrieval.service import _read_release_chunks


def embedding_client(config, deadline=None):
    v = config.vector
    return EmbeddingClient(v.base_url, v.model, v.dimension, v.timeout_seconds, deadline=deadline)


def release_chunks(repository, release_id):
    import hashlib
    from pathlib import Path
    manifest = repository.get_release_manifest(release_id)
    path = Path(manifest.bm25_path).parent / 'chunks.jsonl'
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest.artifact_checksums.get('text_chunks'):
        raise ValueError('release_corpus_checksum_mismatch')
    chunks = _read_release_chunks(manifest)
    if {c.chunk_id for c in chunks} != set(manifest.chunk_ids):
        raise ValueError('release_chunk_membership_mismatch')
    return chunks


def build_index(config, release_id):
    from .retrieval import knowledge_settings, readonly_repository
    _, db = knowledge_settings(config)
    with readonly_repository(db) as repository:
        chunks = release_chunks(repository, release_id)
        # Refuse orphaned source references at indexing time.
        for chunk in chunks:
            for span_id in chunk.span_ids:
                span = repository.get_span(span_id)
                if span is None or span.document_id != chunk.document_id:
                    raise ValueError('orphaned_vector_source')
    return build_vector_index(release_id, chunks, config.resolve(config.vector.index_root),
                              embedding_client(config), config.vector.max_chars)


def search_vector(config, repository, release_id, query, top_k, deadline):
    import time
    if config.vector.index_manifest is None:
        raise FileNotFoundError('vector_index_not_configured')
    chunks = release_chunks(repository, release_id)
    retriever = VectorRetriever(config.resolve(config.vector.index_manifest), chunks, release_id,
                                embedding_client(config, min(deadline, time.time() + config.vector.timeout_seconds)), config.vector.max_chars)
    hits = retriever.search(query, top_k)
    by_id = {c.chunk_id: c for c in chunks}
    return [by_id[chunk_id] for chunk_id, _ in hits], retriever.manifest
